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
# documented match_corners line keys -> our market keys
CORNER_KEYS = {"over_8_5": "corners_o85", "over_9_5": "corners_o95", "over_10_5": "corners_o105"}


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
    body = r.json() if r.ok else {}
    bms = (body.get("data", {}) if isinstance(body, dict) else {}).get("bookmakers")
    available = r.status_code == 200 and bool(bms)
    ops.set_status("statsapi_odds", {
        "available": available,
        "status_code": r.status_code,
        "checked_at": _now(),
    })
    print(f"[statsapi_extra] odds endpoint {'AVAILABLE' if available else 'excluded'} "
          f"(status={r.status_code})")
    return available


def _price(node) -> float | None:
    """A market entry is {'opening': '2.10', 'last_seen': '2.05'} — take the
    freshest (last_seen) price, falling back to opening."""
    if not isinstance(node, dict):
        return None
    v = node.get("last_seen") if node.get("last_seen") is not None else node.get("opening")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _odds_rows(fixture_id: int, data) -> list[dict]:
    """Parse the documented odds shape: data.bookmakers[].markets.{match_odds,
    total_goals.over_2_5, match_corners.over_9_5, btts}.<sel>.{opening,last_seen}."""
    rows = []
    now = _now()

    def add(bm_name, market, selection, node):
        p = _price(node)
        if p is not None:
            rows.append({
                "fixture_id": fixture_id, "bookmaker": bm_name, "market": market,
                "selection": selection, "decimal_odds": p, "fetched_at": now, "source": "statsapi",
            })

    for bm in as_list(data, "bookmakers"):
        bm_name = str(pick(bm, "bookmaker", "name", "key", default="statsapi"))
        markets = bm.get("markets") if isinstance(bm, dict) else None
        if not isinstance(markets, dict):
            continue
        mo = markets.get("match_odds")
        if isinstance(mo, dict):
            for sel in ("home", "draw", "away"):
                add(bm_name, "h2h", sel, mo.get(sel))
        tg = markets.get("total_goals")
        if isinstance(tg, dict) and isinstance(tg.get("over_2_5"), dict):
            add(bm_name, "ou25", "over", tg["over_2_5"].get("over"))
            add(bm_name, "ou25", "under", tg["over_2_5"].get("under"))
        mc = markets.get("match_corners")
        if isinstance(mc, dict):
            for line_key, market in CORNER_KEYS.items():
                node = mc.get(line_key)
                if isinstance(node, dict):
                    add(bm_name, market, "over", node.get("over"))
                    add(bm_name, market, "under", node.get("under"))
        btts = markets.get("btts")
        if isinstance(btts, dict):
            for sel in ("yes", "no"):
                add(bm_name, "btts", sel, btts.get(sel))
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
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        rows.extend(_odds_rows(f["id"], data))
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


# --------------------------------- 5.7 per-match player stats (v4.9) ----
def _num(node, *keys):
    """Nested numeric lookup: _num(p, 'shooting', 'goals') -> p['shooting']['goals']
    as int, or None. Defensive against missing blocks / non-numeric values."""
    cur = node
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    try:
        return int(round(float(cur)))
    except (TypeError, ValueError):
        return None


def _player_row(p: dict, fixture_id: int, team_id: int, now: str) -> dict:
    """One /matches/{id}/player-stats entry -> a player_match_stats row. Pure;
    unit-tested against the documented payload shape."""
    rating = pick(p, "rating")
    try:
        rating = round(float(rating), 2)
    except (TypeError, ValueError):
        rating = None
    return {
        "fixture_id": fixture_id,
        "team_id": team_id,
        "statsapi_id": str(pick(p, "player_id", "id", default="")),
        "name": pick(p, "player_name", "name"),
        "position": pick(p, "position"),
        "minutes": _num(p, "minutes_played") if "minutes_played" in p else _num(p, "minutes"),
        "rating": rating,
        "goals": _num(p, "shooting", "goals"),
        "shots": _num(p, "shooting", "total"),
        "shots_on_target": _num(p, "shooting", "on_target"),
        "key_passes": _num(p, "passing", "key_passes"),
        "duels_won": _num(p, "duels", "won"),
        "dribbles": _num(p, "general", "dribbles_succeeded"),
        "fouls_drawn": _num(p, "general", "fouls_drawn"),
        "fouls_committed": _num(p, "general", "fouls_committed"),
        "yellows": _num(p, "general", "yellow_cards"),
        "reds": _num(p, "general", "red_cards"),
        "computed_at": now,
    }


def ingest_player_match_stats():
    """Per-appearance player stats for finished mapped fixtures -> player_match_stats.
    The team page aggregates these into per-metric 'driver' rankings. Mirrors the
    shotmap ingest's once-per-fixture cadence."""
    fmap = _fixture_map()
    tmap = _team_map()
    finished = sb().table("fixtures").select("id").eq("status", "finished").execute().data
    have = {r["fixture_id"] for r in sb().table("player_match_stats").select("fixture_id").execute().data}
    todo = [f["id"] for f in finished if f["id"] in fmap and f["id"] not in have]
    n = 0
    for fid in todo:
        try:
            payload = statsapi.get(f"/matches/{fmap[fid]}/player-stats", ttl=86400 * 30)
        except Exception as e:
            print(f"[statsapi_extra] player-stats fetch failed for fixture {fid}: {e}")
            continue
        now = _now()
        rows = []
        for p in as_list(payload, "players", "player_stats"):
            team_id = tmap.get(str(pick(p, "team_id", "team")))
            if team_id is None:
                continue
            row = _player_row(p, fid, team_id, now)
            if row["statsapi_id"]:
                rows.append(row)
        if rows:
            sb().table("player_match_stats").upsert(rows, on_conflict="fixture_id,statsapi_id").execute()
            n += len(rows)
    print(f"[statsapi_extra] ingested {n} player-match-stat rows for {len(todo)} fixtures")


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
    ingest_player_match_stats()
    ingest_players_and_strength()


if __name__ == "__main__":
    run()
