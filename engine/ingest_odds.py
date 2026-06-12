"""Ingest 1X2 (h2h) odds from The Odds API.

One bulk call = 1 credit (single market x single region, spec §9b). Credit-aware:
reads x-requests-remaining and skips the refresh entirely when the reserve is low —
the model still runs, only `edge` goes stale. Every pull is appended to odds_snapshots.
"""
import difflib
from datetime import datetime, timedelta, timezone

import requests

from . import cache, config
from .aliases import slugify
from .db import sb


def _resolve_slug(name: str, known_slugs: list[str]) -> str | None:
    s = slugify(name)
    if s in known_slugs:
        return s
    close = difflib.get_close_matches(s, known_slugs, n=1, cutoff=0.8)
    return close[0] if close else None


def run():
    remaining = cache.get("odds:remaining")
    if remaining is not None and int(remaining) < config.ODDS_CREDIT_RESERVE:
        print(f"[ingest_odds] skipping: only {remaining} credits left (< reserve {config.ODDS_CREDIT_RESERVE})")
        return

    r = requests.get(
        f"https://api.the-odds-api.com/v4/sports/{config.ODDS_SPORT_KEY}/odds",
        params={
            "regions": config.ODDS_REGION,
            "markets": "h2h",
            "oddsFormat": "decimal",
            "apiKey": config.ODDS_API_KEY,
        },
        timeout=30,
    )
    rem = r.headers.get("x-requests-remaining")
    if rem is not None:
        cache.set("odds:remaining", int(float(rem)), ttl_seconds=86400 * 31)
        print(f"[ingest_odds] credits remaining this month: {rem}")
    r.raise_for_status()
    events = r.json()
    print(f"[ingest_odds] {len(events)} events with odds")

    teams = sb().table("teams").select("id, slug").execute().data
    slug_to_id = {t["slug"]: t["id"] for t in teams}
    known_slugs = list(slug_to_id)

    fixtures = (
        sb()
        .table("fixtures")
        .select("id, home_id, away_id, kickoff")
        .neq("status", "finished")
        .execute()
        .data
    )
    by_pair = {}
    for f in fixtures:
        if f["home_id"] and f["away_id"]:
            by_pair.setdefault((f["home_id"], f["away_id"]), []).append(f)

    now = datetime.now(timezone.utc).isoformat()
    snapshots = []
    for ev in events:
        hs = _resolve_slug(ev.get("home_team", ""), known_slugs)
        as_ = _resolve_slug(ev.get("away_team", ""), known_slugs)
        if not hs or not as_:
            continue
        commence = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
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
                if mkt.get("key") != "h2h":
                    continue
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
    for i in range(0, len(snapshots), 500):
        sb().table("odds_snapshots").insert(snapshots[i : i + 500]).execute()
    print(f"[ingest_odds] appended {len(snapshots)} snapshot rows")


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
