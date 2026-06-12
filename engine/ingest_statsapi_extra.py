"""Pipeline B hidden-insights ingestion (spec v4.1 §5.0, 5.1, 5.3).

- probe_odds / ingest_match_odds: re-test of the vendor's odds endpoint (the
  pricing page claims odds on every plan; Phase 0 recorded them excluded).
  If accessible, opening/closing 1X2 and corners totals land in odds_snapshots
  with source='statsapi'. The Odds API remains the live/free source.
- ingest_shotmaps: per-shot xG -> shots table, for finished mapped fixtures.
- ingest_players + lineup strength: per-player season stats -> players table;
  confirmed lineups get a minutes-weighted XI rating + key-absence flags.

All payload field access is defensive (pick chains) — confirm real shapes via
verify_statsapi.py and adjust here if the report disagrees.
"""
from datetime import datetime, timedelta, timezone

import requests

from . import config, ops, statsapi
from .db import chunked, sb
from .statsapi import as_list, pick

CORNER_LINES = {8.5: "corners_o85", 9.5: "corners_o95", 10.5: "corners_o105"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fixture_map() -> dict[int, str]:
    return {
        x["fixture_id"]: str(x["statsapi_id"])
        for x in sb().table("xmap_fixtures").select("fixture_id, statsapi_id").execute().data
        if x["statsapi_id"]
    }


def _team_map() -> dict[str, int]:
    return {
        str(x["statsapi_id"]): x["team_id"]
        for x in sb().table("xmap_teams").select("team_id, statsapi_id").execute().data
        if x["statsapi_id"]
    }


# ---------------------------------------------------------------- 5.0 odds ----
def probe_odds() -> bool:
    """One cheap call to settle whether our plan includes odds. Cached a day."""
    cached = ops.get_status("statsapi_odds")
    if cached is not None and cached.get("checked_at", "") > (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).isoformat():
        return bool(cached.get("available"))
    fmap = _fixture_map()
    if not fmap:
        return False
    mid = next(iter(fmap.values()))
    r = requests.get(
        f"{config.STATSAPI_BASE}/matches/{mid}/odds",
        headers={"Authorization": f"Bearer {config.STATSAPI_KEY}"},
        timeout=30,
    )
    available = r.status_code == 200 and bool(as_list(r.json() if r.ok else [], "odds", "bookmakers"))
    ops.set_status("statsapi_odds", {
        "available": available,
        "status_code": r.status_code,
        "checked_at": _now(),
    })
    print(f"[statsapi_extra] odds endpoint {'AVAILABLE' if available else 'excluded'} "
          f"(status={r.status_code})")
    return available


def _odds_rows(fixture_id: int, payload) -> list[dict]:
    rows = []
    now = _now()
    for bm in as_list(payload, "odds", "bookmakers"):
        bm_name = str(pick(bm, "bookmaker", "name", "key", default="statsapi"))
        for phase in ("opening", "closing", "current"):
            block = bm.get(phase) if isinstance(bm.get(phase), (dict, list)) else None
            markets = as_list(block or bm, "markets")
            for mkt in markets:
                mname = str(pick(mkt, "key", "name", "market", default="")).lower()
                outcomes = as_list(pick(mkt, "outcomes", "odds", default=[]), "outcomes")
                if any(k in mname for k in ("1x2", "result", "winner", "h2h")):
                    for o in outcomes:
                        sel = str(pick(o, "name", "selection", default="")).lower()
                        sel = {"1": "home", "x": "draw", "2": "away"}.get(sel, sel)
                        if sel in ("home", "draw", "away") and pick(o, "price", "odds") is not None:
                            rows.append({
                                "fixture_id": fixture_id,
                                "bookmaker": f"{bm_name}:{phase}",
                                "market": "h2h",
                                "selection": sel,
                                "decimal_odds": pick(o, "price", "odds"),
                                "fetched_at": now,
                                "source": "statsapi",
                            })
                elif "corner" in mname:
                    for o in outcomes:
                        line = pick(o, "line", "handicap", "point")
                        sel = str(pick(o, "name", "selection", default="")).lower()
                        try:
                            market_key = CORNER_LINES.get(float(line))
                        except (TypeError, ValueError):
                            market_key = None
                        if market_key and sel in ("over", "under") and pick(o, "price", "odds") is not None:
                            rows.append({
                                "fixture_id": fixture_id,
                                "bookmaker": f"{bm_name}:{phase}",
                                "market": market_key,
                                "selection": sel,
                                "decimal_odds": pick(o, "price", "odds"),
                                "fetched_at": now,
                                "source": "statsapi",
                            })
            if block is None:
                break  # bookmaker payload wasn't phase-split; don't re-read it 3x
    return rows


def ingest_match_odds():
    if not probe_odds():
        return
    fmap = _fixture_map()
    horizon = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    upcoming = (
        sb().table("fixtures").select("id")
        .neq("status", "finished").lte("kickoff", horizon)
        .execute().data
    )
    rows = []
    for f in upcoming:
        mid = fmap.get(f["id"])
        if not mid:
            continue
        try:
            payload = statsapi.get(f"/matches/{mid}/odds", ttl=1800)
        except Exception as e:
            print(f"[statsapi_extra] odds fetch failed for fixture {f['id']}: {e}")
            continue
        rows.extend(_odds_rows(f["id"], payload))
    for batch in chunked(rows):
        sb().table("odds_snapshots").insert(batch).execute()
    print(f"[statsapi_extra] appended {len(rows)} statsapi odds rows")


# ------------------------------------------------------------ 5.1 shotmaps ----
def ingest_shotmaps():
    fmap = _fixture_map()
    tmap = _team_map()
    finished = sb().table("fixtures").select("id").eq("status", "finished").execute().data
    have = {s["fixture_id"] for s in sb().table("shots").select("fixture_id").execute().data}
    todo = [f["id"] for f in finished if f["id"] in fmap and f["id"] not in have]
    n = 0
    for fid in todo:
        try:
            payload = statsapi.get(f"/matches/{fmap[fid]}/shotmap", ttl=86400 * 30)
        except Exception as e:
            print(f"[statsapi_extra] shotmap fetch failed for fixture {fid}: {e}")
            continue
        rows = []
        for s in as_list(payload, "shots", "shotmap"):
            team_id = tmap.get(str(pick(s, "team_id", "team")))
            if team_id is None:
                continue
            rows.append({
                "fixture_id": fid,
                "team_id": team_id,
                "minute": pick(s, "minute", "min"),
                "xg": pick(s, "xg", "expected_goals", "xG"),
                "is_goal": bool(pick(s, "is_goal", "goal", default=False)),
                "situation": pick(s, "situation", "play_type"),
                "body_part": pick(s, "body_part", "bodyPart"),
                "x": pick(s, "x"),
                "y": pick(s, "y"),
            })
        if rows:
            sb().table("shots").upsert(rows, on_conflict="fixture_id,team_id,minute,x,y").execute()
            n += len(rows)
    print(f"[statsapi_extra] ingested {n} shots for {len(todo)} fixtures")


# ------------------------------------------ 5.3 players + lineup strength ----
def ingest_players_and_strength():
    """Weekly-cadence player stats (api_cache TTL handles the cadence), then
    XI strength + key absences on confirmed lineups. Display/rationale only —
    deliberately NOT a model input in v4.1."""
    lineups = sb().table("lineups").select("*").execute().data
    if not lineups:
        return
    tmap_rev = _team_map()  # statsapi_id -> team_id (players keep vendor ids)

    wanted: dict[str, int] = {}  # player statsapi_id -> team_id
    for lu in lineups:
        for group in (lu.get("starters") or []), (lu.get("bench") or []):
            for p in group:
                pid = str(pick(p, "player_id", "id", default="")) if isinstance(p, dict) else ""
                if pid:
                    wanted[pid] = lu["team_id"]
    if not wanted:
        print("[statsapi_extra] lineups carry no player ids — skipping player stats")
        return

    have = {
        p["statsapi_id"]: p
        for p in sb().table("players").select("statsapi_id, fetched_at").execute().data
    }
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    fetched = 0
    for pid, team_id in wanted.items():
        if pid in have and have[pid]["fetched_at"] > week_ago:
            continue
        try:
            payload = statsapi.get(f"/players/{pid}/stats", ttl=86400 * 7)
        except Exception:
            continue
        stats = payload if isinstance(payload, dict) else {}
        sb().table("players").upsert({
            "statsapi_id": pid,
            "team_id": team_id,
            "name": pick(stats, "name", "player_name"),
            "position": pick(stats, "position"),
            "rating": pick(stats, "rating", "average_rating", "avg_rating"),
            "minutes": pick(stats, "minutes", "minutes_played"),
            "fetched_at": _now(),
        }, on_conflict="statsapi_id").execute()
        fetched += 1
    print(f"[statsapi_extra] refreshed {fetched} player stat rows ({len(wanted)} squad players seen)")

    # XI strength: minutes-weighted mean rating; key absences: top-3 rated
    # squad players not in the named XI
    players = sb().table("players").select("*").execute().data
    by_team: dict[int, list[dict]] = {}
    for p in players:
        if p["rating"] is not None:
            by_team.setdefault(p["team_id"], []).append(p)
    for lu in lineups:
        squad = by_team.get(lu["team_id"], [])
        if not squad:
            continue
        xi_ids = {
            str(pick(p, "player_id", "id", default=""))
            for p in (lu.get("starters") or [])
            if isinstance(p, dict)
        }
        xi = [p for p in squad if p["statsapi_id"] in xi_ids]
        strength = None
        if xi:
            weights = [max(float(p["minutes"] or 90), 1) for p in xi]
            strength = sum(float(p["rating"]) * w for p, w in zip(xi, weights)) / sum(weights)
        top3 = sorted(squad, key=lambda p: float(p["rating"]), reverse=True)[:3]
        absences = [p["name"] for p in top3 if p["statsapi_id"] not in xi_ids and p["name"]]
        sb().table("lineups").update({
            "strength": round(strength, 2) if strength is not None else None,
            "key_absences": absences,
        }).eq("id", lu["id"]).execute()
    print("[statsapi_extra] lineup strength + key absences updated")


def run():
    ingest_match_odds()
    ingest_shotmaps()
    ingest_players_and_strength()


if __name__ == "__main__":
    run()
