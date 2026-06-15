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


def _stat_rows(fixture_id: int, match_payload: dict, team_map: dict[str, int]) -> list[dict]:
    """Flatten a /matches/{id}/stats payload into match_stats rows (FT + 1H when present)."""
    rows = []
    sides = as_list(pick(match_payload, "stats", "statistics", "teams", default=match_payload), "stats")
    for side in sides:
        sid = str(pick(side, "team_id", "id"))
        team_id = team_map.get(sid)
        if team_id is None:
            continue
        periods = {"FT": side}
        for pkey, plabel in (("first_half", "1H"), ("1h", "1H"), ("second_half", "2H"), ("2h", "2H")):
            if isinstance(side.get(pkey), dict):
                periods[plabel] = side[pkey]
        for plabel, src in periods.items():
            rows.append(
                {
                    "fixture_id": fixture_id,
                    "team_id": team_id,
                    "is_home": bool(pick(side, "is_home", "home", default=False)),
                    "period": plabel,
                    "shots": pick(src, "shots", "shots_total"),
                    "shots_on_target": pick(src, "shots_on_target", "shots_on_goal"),
                    "corners": pick(src, "corners", "corner_kicks"),
                    "possession": pick(src, "possession", "possession_pct"),
                    "xg": pick(src, "xg", "expected_goals"),
                    "npxg": pick(src, "npxg", "non_penalty_xg"),
                    "fouls": pick(src, "fouls"),
                    "yellows": pick(src, "yellows", "yellow_cards"),
                    "reds": pick(src, "reds", "red_cards"),
                    "passes": pick(src, "passes", "passes_total"),
                    "source_payload": side if plabel == "FT" else None,
                    "fetched_at": _now(),
                }
            )
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

    # referee context (spec v4.1 §5.4) — patch mapped fixtures that lack one
    ref_patches = 0
    for m in matches:
        mid = str(pick(m, "id", "match_id"))
        ref = pick(m, "referee", "referee_name")
        if isinstance(ref, dict):
            ref = pick(ref, "name")
        if ref and mid in fixture_map:
            sb().table("fixtures").update({"referee": str(ref)}).eq(
                "id", fixture_map[mid]
            ).is_("referee", "null").execute()
            ref_patches += 1
    if ref_patches:
        print(f"[ingest_statsapi] referee recorded for {ref_patches} fixtures")

    # ---- match stats (xG, corners, shots) for finished + live mapped fixtures ----
    finished_states = {"finished", "ft", "full_time", "ended", "live", "in_play"}
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
        is_live = str(pick(m, "status", "state", default="")).lower() in {"live", "in_play"}
        if not is_live and (fixture_id, "FT") in have:
            continue  # finalized stats already stored
        try:
            payload = statsapi.get(f"/matches/{mid}/stats", ttl=60 if is_live else 86400)
        except Exception as e:
            print(f"[ingest_statsapi] stats fetch failed for match {mid}: {e}")
            continue
        rows = _stat_rows(fixture_id, payload, team_map)
        if rows:
            sb().table("match_stats").upsert(rows, on_conflict="fixture_id,team_id,period").execute()
            n_stats += len(rows)
    print(f"[ingest_statsapi] upserted {n_stats} match_stats rows")

    # ---- lineups for fixtures within the next 48h or live ----
    now = datetime.now(timezone.utc)
    soon = [
        m for m in matches
        if str(pick(m, "id", "match_id")) in fixture_map
        and (ko := _parse_dt(pick(m, "kickoff", "utc_date", "date", "start_time"))) is not None
        and -timedelta(hours=3) <= ko - now <= timedelta(hours=48)
    ]
    n_lineups = 0
    for m in soon:
        mid = str(pick(m, "id", "match_id"))
        try:
            payload = statsapi.get(f"/matches/{mid}/lineups", ttl=600)
        except Exception as e:
            print(f"[ingest_statsapi] lineups fetch failed for match {mid}: {e}")
            continue
        for side in as_list(pick(payload, "lineups", default=payload), "lineups"):
            team_id = team_map.get(str(pick(side, "team_id", "id")))
            starters = pick(side, "starters", "starting_xi", "startXI", default=[])
            if team_id is None or not starters:
                continue
            sb().table("lineups").upsert(
                {
                    "fixture_id": fixture_map[mid],
                    "team_id": team_id,
                    "formation": pick(side, "formation"),
                    "starters": starters,
                    "bench": pick(side, "bench", "substitutes"),
                    "confirmed": bool(pick(side, "confirmed", "is_confirmed", default=False)),
                    "fetched_at": _now(),
                },
                on_conflict="fixture_id,team_id",
            ).execute()
            n_lineups += 1
    print(f"[ingest_statsapi] upserted {n_lineups} lineups")


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
