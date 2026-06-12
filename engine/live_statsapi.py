"""Live mode (spec v4 §5.5): poll TheStatsAPI for in-play WC fixtures, upsert
running match_stats totals + fixture score/status. Realtime pushes the rows to
open match pages. Self-exits immediately when no fixture is live or imminent.

Run by .github/workflows/live.yml every 5 minutes on matchdays.
"""
from datetime import datetime, timedelta, timezone

from . import statsapi
from .db import sb
from .ingest_statsapi import _stat_rows
from .statsapi import pick

LIVE_STATES = {"live", "in_play", "ht", "paused"}
FINISHED_STATES = {"finished", "ft", "full_time", "ended"}


def run():
    now = datetime.now(timezone.utc)
    window = (
        sb()
        .table("fixtures")
        .select("id, status, kickoff")
        .neq("status", "finished")
        .gte("kickoff", (now - timedelta(hours=3)).isoformat())
        .lte("kickoff", (now + timedelta(minutes=10)).isoformat())
        .execute()
        .data
    )
    if not window:
        print("[live] no live or imminent fixtures — exiting")
        return

    xmap = {
        x["fixture_id"]: str(x["statsapi_id"])
        for x in sb().table("xmap_fixtures").select("fixture_id, statsapi_id").execute().data
        if x["statsapi_id"]
    }
    team_map = {
        str(x["statsapi_id"]): x["team_id"]
        for x in sb().table("xmap_teams").select("team_id, statsapi_id").execute().data
        if x["statsapi_id"]
    }

    for f in window:
        mid = xmap.get(f["id"])
        if not mid:
            continue
        try:
            match = statsapi.get(f"/matches/{mid}", ttl=0)
            stats = statsapi.get(f"/matches/{mid}/stats", ttl=0)
        except Exception as e:
            print(f"[live] poll failed for fixture {f['id']}: {e}")
            continue

        state = str(pick(match, "status", "state", default="")).lower()
        score = pick(match, "score", default={}) or {}
        patch = {}
        if state in LIVE_STATES:
            patch["status"] = "live"
        elif state in FINISHED_STATES:
            patch["status"] = "finished"
        hg = pick(score, "home", "home_goals", "fulltime_home")
        ag = pick(score, "away", "away_goals", "fulltime_away")
        if hg is not None:
            patch["home_goals"], patch["away_goals"] = hg, ag
        if patch:
            sb().table("fixtures").update(patch).eq("id", f["id"]).execute()

        rows = _stat_rows(f["id"], stats, team_map)
        if rows:
            sb().table("match_stats").upsert(rows, on_conflict="fixture_id,team_id,period").execute()
        print(f"[live] fixture {f['id']}: state={state} score={hg}-{ag} stats_rows={len(rows)}")


if __name__ == "__main__":
    run()
