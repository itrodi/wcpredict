"""Ingest 1X2 (h2h) odds from The Odds API.

One bulk call = 1 credit (single market x single region, spec §9b). Credit-aware:
reads x-requests-remaining and skips the refresh entirely when the reserve is low —
the model still runs, only `edge` goes stale. Every pull is appended to odds_snapshots.
"""
import difflib
from datetime import datetime, timedelta, timezone

import requests

from . import cache, config, ops
from .aliases import slugify
from .db import sb


def _resolve_slug(name: str, known_slugs: list[str]) -> str | None:
    s = slugify(name)
    if s in known_slugs:
        return s
    close = difflib.get_close_matches(s, known_slugs, n=1, cutoff=0.8)
    return close[0] if close else None


def _pull_gate(fixtures, now_dt) -> str | None:
    """Reason to skip this pull, or None to proceed. The cron is hourly but
    credits are finite (~500/month free tier vs ~720 hourly pulls over the
    tournament): pull on a kickoff-aware cadence — dense near kickoffs (that's
    where the closing line and the edges live), sparse otherwise."""
    upcoming = [
        datetime.fromisoformat(f["kickoff"].replace("Z", "+00:00"))
        for f in fixtures
        if datetime.fromisoformat(f["kickoff"].replace("Z", "+00:00")) > now_dt
    ]
    if not upcoming:
        return "no upcoming fixtures"
    nearest = min(upcoming) - now_dt
    if nearest > timedelta(hours=config.ODDS_PULL_WINDOW_H):
        return f"nearest kickoff {nearest} away (> {config.ODDS_PULL_WINDOW_H}h window)"
    last = cache.get("odds:last_pull")
    if last is None:
        return None
    since = now_dt - datetime.fromisoformat(last)
    if nearest <= timedelta(hours=2):
        required = timedelta(minutes=config.ODDS_PULL_SPACING_NEAR_M)
    elif nearest <= timedelta(hours=12):
        required = timedelta(minutes=config.ODDS_PULL_SPACING_MID_M)
    else:
        required = timedelta(minutes=config.ODDS_PULL_SPACING_FAR_M)
    if since < required:
        return f"last pull {since} ago (< {required} for kickoff in {nearest})"
    return None


def run():
    remaining = cache.get("odds:remaining")
    if remaining is not None and int(remaining) < config.ODDS_CREDIT_RESERVE:
        print(f"[ingest_odds] skipping: only {remaining} credits left (< reserve {config.ODDS_CREDIT_RESERVE})")
        return

    now_dt = datetime.now(timezone.utc)
    fixtures = (
        sb()
        .table("fixtures")
        .select("id, home_id, away_id, kickoff")
        .eq("status", "scheduled")
        .execute()
        .data
    )
    skip = _pull_gate(fixtures, now_dt)
    if skip:
        print(f"[ingest_odds] skipping pull: {skip}")
        ops.set_status("odds_pull", {"skipped": skip, "at": now_dt.isoformat()})
        return

    r = requests.get(
        f"https://api.the-odds-api.com/v4/sports/{config.ODDS_SPORT_KEY}/odds",
        params={
            "regions": config.ODDS_REGION,
            "markets": config.ODDS_MARKETS,
            "oddsFormat": "decimal",
            "apiKey": config.ODDS_API_KEY,
        },
        timeout=30,
    )
    rem = r.headers.get("x-requests-remaining")
    if rem is not None:
        cache.set("odds:remaining", int(float(rem)), ttl_seconds=86400 * 31)
        ops.set_status("odds_credits_remaining", {"remaining": int(float(rem))})
        print(f"[ingest_odds] credits remaining this month: {rem}")
    r.raise_for_status()
    events = r.json()
    print(f"[ingest_odds] {len(events)} events with odds")

    teams = sb().table("teams").select("id, slug").execute().data
    slug_to_id = {t["slug"]: t["id"] for t in teams}
    known_slugs = list(slug_to_id)

    by_pair = {}
    for f in fixtures:
        if f["home_id"] and f["away_id"]:
            by_pair.setdefault((f["home_id"], f["away_id"]), []).append(f)

    now = now_dt.isoformat()
    snapshots = []
    for ev in events:
        hs = _resolve_slug(ev.get("home_team", ""), known_slugs)
        as_ = _resolve_slug(ev.get("away_team", ""), known_slugs)
        if not hs or not as_:
            continue
        commence = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if commence <= now_dt:
            continue  # already kicked off: these are in-play prices, not closing odds
        candidates = by_pair.get((slug_to_id[hs], slug_to_id[as_]), [])
        fixture = next(
            (
                f
                for f in candidates
                if abs(
                    datetime.fromisoformat(f["kickoff"].replace("Z", "+00:00")) - commence
                )
                <= timedelta(days=2)
            ),
            None,
        )
        if not fixture:
            continue
        for bm in ev.get("bookmakers", []):
            for mkt in bm.get("markets", []):
                if mkt.get("key") == "h2h":
                    for out in mkt.get("outcomes", []):
                        if out["name"] == ev["home_team"]:
                            sel = "home"
                        elif out["name"] == ev["away_team"]:
                            sel = "away"
                        else:
                            sel = "draw"
                        snapshots.append(
                            {
                                "fixture_id": fixture["id"],
                                "bookmaker": bm["key"],
                                "market": "h2h",
                                "selection": sel,
                                "decimal_odds": out["price"],
                                "fetched_at": now,
                            }
                        )
                elif mkt.get("key") == "totals":
                    # only the 2.5 line — that's the market the models price
                    for out in mkt.get("outcomes", []):
                        if out.get("point") != 2.5:
                            continue
                        sel = str(out.get("name", "")).lower()
                        if sel not in ("over", "under"):
                            continue
                        snapshots.append(
                            {
                                "fixture_id": fixture["id"],
                                "bookmaker": bm["key"],
                                "market": "ou25",
                                "selection": sel,
                                "decimal_odds": out["price"],
                                "fetched_at": now,
                            }
                        )
    for i in range(0, len(snapshots), 500):
        sb().table("odds_snapshots").insert(snapshots[i : i + 500]).execute()
    cache.set("odds:last_pull", now, ttl_seconds=86400 * 7)
    ops.set_status("odds_pull", {"at": now, "rows": len(snapshots), "markets": config.ODDS_MARKETS})
    print(f"[ingest_odds] appended {len(snapshots)} snapshot rows")

    # coverage audit (spec v4.1 §1.1): every fixture kicking off in the next 7
    # days should have odds; log + record the ones that don't
    week_ahead = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    covered = {s["fixture_id"] for s in snapshots}
    missing = [
        f["id"]
        for f in fixtures
        if f["kickoff"] <= week_ahead and f["id"] not in covered
    ]
    if missing:
        print(f"[ingest_odds] WARNING: {len(missing)} fixtures in the next 7 days "
              f"have no odds rows this pull: {missing}")
    ops.set_status("fixtures_missing_odds", {"fixture_ids": missing, "window_days": 7})


def prune_snapshots():
    """Keep the 500MB DB lean: drop snapshots older than the retention window
    for fixtures that are already finished (spec §12)."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=config.ODDS_SNAPSHOT_RETENTION_DAYS)
    ).isoformat()
    finished = sb().table("fixtures").select("id").eq("status", "finished").execute().data
    ids = [f["id"] for f in finished]
    if not ids:
        return
    for i in range(0, len(ids), 100):
        sb().table("odds_snapshots").delete().in_("fixture_id", ids[i : i + 100]).lt(
            "fetched_at", cutoff
        ).execute()
    print("[ingest_odds] pruned old snapshots for finished fixtures")


if __name__ == "__main__":
    run()
