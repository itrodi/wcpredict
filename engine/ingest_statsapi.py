"""Pipeline B ingest: TheStatsAPI -> xmap identities, match_stats (incl. xG, corners), lineups.

Identity resolution order (spec v4 §4): existing xmap_teams.statsapi_id -> exact
name -> normalized/alias match (engine/aliases.py) -> LOG LOUDLY AND SKIP.
Never auto-creates a team: Pipeline A (football-data.org) owns team creation.
Fixtures map by (home, away, kickoff ±3h) and persist into xmap_fixtures.
"""
from datetime import datetime, timedelta, timezone

from . import cache, config, ops, statsapi
from .aliases import slugify
from .db import chunked, sb
from .statsapi import as_list, pick

KICKOFF_TOLERANCE = timedelta(hours=3)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _resolve_team_ids(api_teams: list[dict]) -> tuple[dict[str, int], list[str]]:
    """statsapi team id -> our team id. Persists new mappings into xmap_teams.
    Also returns the exact vendor names it could NOT map (spec v4.1 §1.2), so
    /admin/health can show copy-paste-ready alias candidates."""
    teams = sb().table("teams").select("id, slug, name").execute().data
    by_slug = {t["slug"]: t["id"] for t in teams}
    by_name = {t["name"].lower(): t["id"] for t in teams}
    xmap = {
        str(x["statsapi_id"]): x["team_id"]
        for x in sb().table("xmap_teams").select("team_id, statsapi_id").execute().data
        if x["statsapi_id"]
    }
    resolved: dict[str, int] = {}
    unmapped: list[str] = []
    new_maps = []
    for at in api_teams:
        sid = str(pick(at, "id", "team_id"))
        name = str(pick(at, "name", "title", default=""))
        if sid in xmap:                                # 1. existing mapping
            resolved[sid] = xmap[sid]
            continue
        team_id = by_name.get(name.lower())           # 2. exact name
        if team_id is None:
            team_id = by_slug.get(slugify(name))      # 3. normalized/alias
        if team_id is None:
            unmapped.append(name)
            print(f"[ingest_statsapi] !! UNMAPPED TEAM: statsapi id={sid} name={name!r} — skipped, "
                  f"add an alias in engine/aliases.py")
            continue
        resolved[sid] = team_id
        new_maps.append({"team_id": team_id, "statsapi_id": sid})
    if new_maps:
        sb().table("xmap_teams").upsert(new_maps, on_conflict="team_id").execute()
        print(f"[ingest_statsapi] mapped {len(new_maps)} new teams into xmap_teams")
    return resolved, unmapped


def _season_matches(comp_id: str, season_id: str) -> list[dict]:
    """Season fixtures via TheStatsAPI's flat matches collection.

    Per the API docs the correct shape is
        GET /football/matches?competition_id={c}&season_id={s}&per_page=100
    (a top-level filtered collection), NOT a nested
        /competitions/{c}/seasons/{s}/matches
    path — the latter 404s. The nested shape is kept only as a defensive
    fallback in case the API ever changes. If every shape fails the cached
    competition/season may be stale, so we bust the cache and return []."""
    candidates = [
        ("/matches", {"competition_id": comp_id, "season_id": season_id}),
        (f"/competitions/{comp_id}/seasons/{season_id}/matches", None),
        (f"/competitions/{comp_id}/matches", {"season_id": season_id}),
    ]
    items, path = statsapi.get_all_try(candidates)
    if path:
        print(f"[ingest_statsapi] matches via {path} -> {len(items)}")
        return items
    print(f"[ingest_statsapi] NO working matches endpoint for comp={comp_id} season={season_id} "
          f"— busting wc_season cache so the next run re-resolves the competition")
    cache.set("statsapi:wc_season", {}, 0)
    return []


def _map_fixtures(matches: list[dict], team_map: dict[str, int]) -> dict[str, int]:
    """statsapi match id -> our fixture id, via (home, away, kickoff ±3h)."""
    fixtures = sb().table("fixtures").select("id, home_id, away_id, kickoff").execute().data
    by_pair: dict[tuple, list] = {}
    for f in fixtures:
        if f["home_id"] and f["away_id"]:
            by_pair.setdefault((f["home_id"], f["away_id"]), []).append(f)
    existing = {
        str(x["statsapi_id"]): x["fixture_id"]
        for x in sb().table("xmap_fixtures").select("fixture_id, statsapi_id").execute().data
        if x["statsapi_id"]
    }
    mapped: dict[str, int] = dict(existing)
    new_maps = []
    unmapped = 0
    for m in matches:
        mid = str(pick(m, "id", "match_id"))
        if mid in mapped:
            continue
        home = pick(m, "home_team", "homeTeam", default={}) or {}
        away = pick(m, "away_team", "awayTeam", default={}) or {}
        h = team_map.get(str(pick(home, "id", "team_id")))
        a = team_map.get(str(pick(away, "id", "team_id")))
        ko = _parse_dt(pick(m, "kickoff", "utc_date", "date", "start_time"))
        if not h or not a:
            continue  # TBD knockout slot or unmapped team (already logged)
        candidates = by_pair.get((h, a), [])
        fixture = next(
            (
                f
                for f in candidates
                if ko is None
                or abs(datetime.fromisoformat(f["kickoff"].replace("Z", "+00:00")) - ko)
                <= KICKOFF_TOLERANCE
            ),
            None,
        )
        if fixture is None:
            unmapped += 1
            print(f"[ingest_statsapi] !! UNMAPPED FIXTURE: statsapi id={mid} "
                  f"({pick(home,'name')} vs {pick(away,'name')} @ {ko})")
            continue
        mapped[mid] = fixture["id"]
        new_maps.append({"fixture_id": fixture["id"], "statsapi_id": mid})
    if new_maps:
        for batch in chunked(new_maps):
            sb().table("xmap_fixtures").upsert(batch, on_conflict="fixture_id").execute()
    # CI-style assertion (spec §4): every WC fixture should carry both vendor ids
    holes = [
        x for x in sb().table("xmap_fixtures").select("fixture_id, fd_id, statsapi_id").execute().data
        if not x["fd_id"] or not x["statsapi_id"]
    ]
    if holes or unmapped:
        print(f"[ingest_statsapi] !! DISCREPANCY: {len(holes)} fixtures missing a vendor id, "
              f"{unmapped} unmappable — see /admin/health")
    return mapped


_PERIODS = (("all", "FT"), ("first_half", "1H"), ("second_half", "2H"))


def _stat_at(data: dict, path: tuple, period: str, side: str):
    """Navigate the documented stats shape data.<category>.<stat>.<period>.<home|away>."""
    node = data
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
        if node is None:
            return None
    pnode = node.get(period) if isinstance(node, dict) else None
    return pnode.get(side) if isinstance(pnode, dict) else None


def _stat_rows(fixture_id: int, data: dict, home_team_id, away_team_id) -> list[dict]:
    """Flatten a /matches/{id}/stats payload into match_stats rows.

    The payload is nested by category with home/away inside each stat and a
    per-period split, e.g.:
        data.attack.corners.all.home          -> corners (FT)
        data.shots.total.first_half.away       -> shots (1H)
        data.np_expected_goals.all.home        -> npxG (FT)
    There are no team ids in the stats payload, so the caller passes the mapped
    home/away team ids. Only non-penalty xG is provided, so it doubles as the xG
    signal the models consume."""
    if not isinstance(data, dict):
        return []
    rows = []
    now = _now()
    metrics = ("shots", "shots_on_target", "corners", "possession", "xg", "fouls",
               "yellows", "reds", "passes")
    for side, team_id in (("home", home_team_id), ("away", away_team_id)):
        if team_id is None:
            continue
        for period, label in _PERIODS:
            npxg = _stat_at(data, ("np_expected_goals",), period, side)
            row = {
                "fixture_id": fixture_id,
                "team_id": team_id,
                "is_home": side == "home",
                "period": label,
                "shots": _stat_at(data, ("shots", "total"), period, side),
                "shots_on_target": _stat_at(data, ("shots", "on_target"), period, side),
                "corners": _stat_at(data, ("attack", "corners"), period, side),
                "possession": _stat_at(data, ("overview", "possession"), period, side),
                "xg": npxg,
                "npxg": npxg,
                "fouls": _stat_at(data, ("overview", "fouls"), period, side),
                "yellows": _stat_at(data, ("overview", "yellow_cards"), period, side),
                "reds": _stat_at(data, ("overview", "red_cards"), period, side),
                "passes": _stat_at(data, ("passes", "total"), period, side),
                "source_payload": data if label == "FT" else None,
                "fetched_at": now,
            }
            # keep FT always; skip half-period rows the API didn't populate
            if label != "FT" and all(row[m] is None for m in metrics):
                continue
            rows.append(row)
    return rows


def run():
    comp_id, season_id = statsapi.find_world_cup_season()
    matches = _season_matches(comp_id, season_id)
    print(f"[ingest_statsapi] {len(matches)} season matches")

    api_teams = []
    seen = set()
    for m in matches:
        for side_key in ("home_team", "homeTeam", "away_team", "awayTeam"):
            t = m.get(side_key)
            if isinstance(t, dict):
                sid = str(pick(t, "id", "team_id"))
                if sid not in seen and pick(t, "name"):
                    seen.add(sid)
                    api_teams.append(t)
    team_map, unmapped_names = _resolve_team_ids(api_teams)
    fixture_map = _map_fixtures(matches, team_map)

    # operational truth for /admin/health (spec v4.1 §1.2, §1.4)
    ops.set_status("fixture_count_statsapi", {
        "ingested": len(fixture_map),
        "season_matches": len(matches),
        "expected": ops.EXPECTED_FIXTURES,
    })
    ops.set_status("unmapped_teams", {"statsapi": unmapped_names})
    if len(matches) and len(matches) < 100:
        print(f"[ingest_statsapi] ERROR: only {len(matches)} season matches — "
              f"expected ~{ops.EXPECTED_FIXTURES}; check pagination / season id")

    # home/away OUR-team-id per statsapi match id (the stats payload has no team
    # ids — home/away are keys inside each stat)
    match_sides: dict[str, tuple] = {}
    for m in matches:
        mid = str(pick(m, "id", "match_id"))
        home = pick(m, "home_team", "homeTeam", default={}) or {}
        away = pick(m, "away_team", "awayTeam", default={}) or {}
        match_sides[mid] = (
            team_map.get(str(pick(home, "id", "team_id"))),
            team_map.get(str(pick(away, "id", "team_id"))),
        )

    # ---- match stats (xG, corners, shots) for finished + live mapped fixtures ----
    live_states = {"live", "in_play"}
    finished_states = {"finished", "ft", "full_time", "ended"} | live_states
    want_stats = [
        m for m in matches
        if str(pick(m, "status", "state", default="")).lower() in finished_states
        and str(pick(m, "id", "match_id")) in fixture_map
    ]
    have = {
        (s["fixture_id"], s["period"])
        for s in sb().table("match_stats").select("fixture_id, period").execute().data
    }
    n_stats = 0
    for m in want_stats:
        mid = str(pick(m, "id", "match_id"))
        fixture_id = fixture_map[mid]
        is_live = str(pick(m, "status", "state", default="")).lower() in live_states
        if not is_live and (fixture_id, "FT") in have:
            continue  # finalized stats already stored
        try:
            payload = statsapi.get(f"/matches/{mid}/stats", ttl=60 if is_live else 86400)
        except Exception as e:
            print(f"[ingest_statsapi] stats fetch failed for match {mid}: {e}")
            continue
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        home_tid, away_tid = match_sides.get(mid, (None, None))
        rows = _stat_rows(fixture_id, data, home_tid, away_tid)
        if rows:
            sb().table("match_stats").upsert(rows, on_conflict="fixture_id,team_id,period").execute()
            n_stats += len(rows)
    print(f"[ingest_statsapi] upserted {n_stats} match_stats rows")
    # NOTE: TheStatsAPI exposes no pre-match lineup endpoint (only post-match
    # player-stats), so confirmed-XI / key-absence ingestion is intentionally not
    # attempted here — the picks engine's lineup suppression simply stays dormant.


def prune_payloads():
    """Storage discipline (spec v4 §10): drop raw source_payload for finished
    fixtures older than the retention window; modeled fields are kept."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=config.STATSAPI_RETENTION_DAYS)
    ).isoformat()
    old = (
        sb().table("fixtures").select("id").eq("status", "finished").lt("kickoff", cutoff).execute().data
    )
    ids = [f["id"] for f in old]
    for i in range(0, len(ids), 100):
        sb().table("match_stats").update({"source_payload": None}).in_(
            "fixture_id", ids[i : i + 100]
        ).neq("source_payload", "null").execute()
    if ids:
        print(f"[ingest_statsapi] pruned raw payloads for {len(ids)} old fixtures")


if __name__ == "__main__":
    run()
