"""Ingest core data from football-data.org (competition WC): fixtures, results, groups.

2-3 requests per run, cached in api_cache, calls spaced >=6.5s apart (free tier: 10/min).
Creates teams the seed didn't know (e.g. playoff winners) with DEFAULT_ELO.
"""
import time

import requests

from . import cache, config, ops
from .aliases import slugify
from .db import sb

_last_call = 0.0

STAGE_MAP = {
    "GROUP_STAGE": "group",
    "LAST_32": "R32",
    "LAST_16": "R16",
    "ROUND_OF_32": "R32",
    "ROUND_OF_16": "R16",
    "QUARTER_FINALS": "QF",
    "SEMI_FINALS": "SF",
    "THIRD_PLACE": "3P",
    "FINAL": "F",
}

STATUS_MAP = {
    "SCHEDULED": "scheduled",
    "TIMED": "scheduled",
    "POSTPONED": "scheduled",
    "IN_PLAY": "live",
    "PAUSED": "live",
    "FINISHED": "finished",
    "AWARDED": "finished",
}

HOST_SLUGS = {"united-states", "canada", "mexico"}


def _fd_get(path: str, ttl: int = config.FD_CACHE_TTL_S):
    """GET with api_cache + rate-limit spacing."""
    global _last_call
    key = f"fd:{path}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    wait = config.FD_MIN_CALL_SPACING_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    r = requests.get(
        f"{config.FD_BASE}{path}",
        headers={"X-Auth-Token": config.FOOTBALL_DATA_ORG_TOKEN},
        timeout=30,
    )
    _last_call = time.monotonic()
    r.raise_for_status()
    payload = r.json()
    cache.set(key, payload, ttl)
    return payload


def _group_code(match) -> str | None:
    g = match.get("group")  # e.g. "GROUP_A"
    if g and g.startswith("GROUP_"):
        return g.split("_", 1)[1]
    return None


def run():
    matches = _fd_get(f"/competitions/{config.FD_COMPETITION}/matches")["matches"]
    print(f"[ingest_fd] {len(matches)} matches from football-data.org")
    # football-data.org returns the whole WC in one response — anything short of
    # the official 104 is a data problem, not pagination (spec v4.1 §1.1)
    if len(matches) < 100:
        print(f"[ingest_fd] ERROR: only {len(matches)} matches returned — "
              f"expected ~{ops.EXPECTED_FIXTURES}; investigate before trusting this run")
    ops.set_status("fixture_count_fd", {
        "ingested": len(matches),
        "expected": ops.EXPECTED_FIXTURES,
    })

    # ---- teams: collect every named team, with group codes from group-stage matches ----
    seen: dict[str, dict] = {}  # slug -> {name, group_code}
    for m in matches:
        gc = _group_code(m)
        for side in ("homeTeam", "awayTeam"):
            t = m.get(side) or {}
            name = t.get("name")
            if not name:
                continue  # knockout slot not yet determined
            slug = slugify(name)
            entry = seen.setdefault(slug, {"name": name, "group_code": None, "fd_id": None})
            if gc:
                entry["group_code"] = gc
            if t.get("id") is not None:
                entry["fd_id"] = str(t["id"])

    existing = {t["slug"]: t for t in sb().table("teams").select("id, slug, group_code").execute().data}
    inserts, updates = [], []
    for slug, info in seen.items():
        if slug in existing:
            if info["group_code"] and info["group_code"] != existing[slug]["group_code"]:
                updates.append((existing[slug]["id"], {"group_code": info["group_code"]}))
        else:
            inserts.append(
                {
                    "slug": slug,
                    "name": info["name"],
                    "group_code": info["group_code"],
                    "elo": config.DEFAULT_ELO,
                }
            )
    if inserts:
        sb().table("teams").insert(inserts).execute()
        print(f"[ingest_fd] created {len(inserts)} teams not in seed: {[t['slug'] for t in inserts]}")
    for team_id, patch in updates:
        sb().table("teams").update(patch).eq("id", team_id).execute()

    team_ids = {t["slug"]: t["id"] for t in sb().table("teams").select("id, slug").execute().data}

    # cross-vendor identity map, fd side (v4 §4). Upserting only fd_id preserves statsapi_id.
    xmap_rows = [
        {"team_id": team_ids[slug], "fd_id": info["fd_id"]}
        for slug, info in seen.items()
        if info.get("fd_id") and slug in team_ids
    ]
    if xmap_rows:
        sb().table("xmap_teams").upsert(xmap_rows, on_conflict="team_id").execute()

    # ---- fixtures: upsert by ext_id (elo_applied intentionally omitted -> preserved) ----
    rows = []
    for m in matches:
        stage = STAGE_MAP.get(m.get("stage", ""), None)
        if stage is None:
            continue
        home = (m.get("homeTeam") or {}).get("name")
        away = (m.get("awayTeam") or {}).get("name")
        home_slug = slugify(home) if home else None
        score = (m.get("score") or {}).get("fullTime") or {}
        rows.append(
            {
                "ext_id": str(m["id"]),
                "stage": stage,
                "group_code": _group_code(m),
                "home_id": team_ids.get(home_slug) if home else None,
                "away_id": team_ids.get(slugify(away)) if away else None,
                "kickoff": m["utcDate"],
                "venue": m.get("venue"),
                "host_home": home_slug in HOST_SLUGS if home_slug else False,
                "status": STATUS_MAP.get(m.get("status", ""), "scheduled"),
                "home_goals": score.get("home"),
                "away_goals": score.get("away"),
            }
        )
    for batch in _chunks(rows, 500):
        sb().table("fixtures").upsert(batch, on_conflict="ext_id").execute()
    print(f"[ingest_fd] upserted {len(rows)} fixtures")

    fx = sb().table("fixtures").select("id, ext_id").execute().data
    xfix = [{"fixture_id": f["id"], "fd_id": f["ext_id"]} for f in fx if f["ext_id"]]
    for batch in _chunks(xfix, 500):
        sb().table("xmap_fixtures").upsert(batch, on_conflict="fixture_id").execute()


def _chunks(rows, size):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


if __name__ == "__main__":
    run()
