"""Ingest core data from football-data.org (competition WC): fixtures, results, groups.

2-3 requests per run, cached in api_cache, calls spaced >=6.5s apart (free tier: 10/min).
Creates teams the seed didn't know (e.g. playoff winners) with DEFAULT_ELO.
"""
import re
import time
import unicodedata

import requests

from . import cache, config
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

# football-data.org names -> our seed slugs where plain slugification differs
SLUG_ALIASES = {
    "usa": "united-states",
    "south-korea": "korea-republic",
    "ivory-coast": "cote-divoire",
    "cabo-verde": "cape-verde",
    "ir-iran": "iran",
    "turkiye": "turkey",
}


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return SLUG_ALIASES.get(s, s)


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
            entry = seen.setdefault(slug, {"name": name, "group_code": None})
            if gc:
                entry["group_code"] = gc

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


def _chunks(rows, size):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


if __name__ == "__main__":
    run()
