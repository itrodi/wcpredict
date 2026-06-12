"""Upserts into Supabase via the service role key.

`computed_at` is set explicitly on every row — a column default only fires on
insert, so relying on `default now()` would freeze every "last updated" badge
at first write (spec §4).
"""
from datetime import datetime, timezone

from .db import chunked, sb


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def settle_outcome(market, selection, hg, ag, corners_home=None, corners_away=None):
    """Realised boolean outcome for a selection, or None when unsettleable
    (first-half markets need HT data we don't store; corners need match_stats)."""
    if market == "1x2":
        return {"home": hg > ag, "draw": hg == ag, "away": hg < ag}[selection]
    if market == "ou25":
        return (hg + ag >= 3) if selection == "over" else (hg + ag <= 2)
    if market == "btts":
        return (hg >= 1 and ag >= 1) if selection == "yes" else not (hg >= 1 and ag >= 1)
    if market == "cs":
        return selection == f"{hg}-{ag}" if selection != "other" else (hg > 4 or ag > 4)
    if market.startswith("corners_o") and corners_home is not None and corners_away is not None:
        line = int(market.removeprefix("corners_o")) / 10.0   # 'corners_o95' -> 9.5
        total = corners_home + corners_away
        return total > line if selection == "over" else total < line
    if market.startswith("team_corners_") and corners_home is not None and corners_away is not None:
        count = corners_home if "_home_" in market else corners_away
        line = int(market.rsplit("_o", 1)[1]) / 10.0          # '..._o45' -> 4.5
        return count > line if selection == "over" else count < line
    return None  # ht_1x2 / ou*_1h / htft: no half-time record stored


def _corner_counts(fixture_ids: list[int]) -> dict[int, tuple]:
    """fixture_id -> (home corners, away corners) from match_stats FT rows."""
    out: dict[int, tuple] = {}
    for i in range(0, len(fixture_ids), 100):
        rows = (
            sb().table("match_stats")
            .select("fixture_id, corners, is_home")
            .in_("fixture_id", fixture_ids[i : i + 100]).eq("period", "FT")
            .execute().data
        )
        agg: dict[int, dict] = {}
        for r in rows:
            if r["corners"] is not None:
                agg.setdefault(r["fixture_id"], {})["h" if r["is_home"] else "a"] = r["corners"]
        for fid, d in agg.items():
            if "h" in d and "a" in d:
                out[fid] = (d["h"], d["a"])
    return out


def settle_picks():
    """Settle published, non-retired picks on finished fixtures (spec v4.1 §4.2)."""
    open_picks = (
        sb().table("picks").select("id, fixture_id, market, selection")
        .is_("outcome", "null").is_("retired_at", "null")
        .execute().data
    )
    if not open_picks:
        return
    fids = list({p["fixture_id"] for p in open_picks})
    fixtures = {
        f["id"]: f
        for f in sb().table("fixtures").select("id, status, home_goals, away_goals")
        .in_("id", fids).eq("status", "finished").execute().data
        if f["home_goals"] is not None
    }
    corners = _corner_counts(list(fixtures))
    settled = 0
    for p in open_picks:
        f = fixtures.get(p["fixture_id"])
        if not f:
            continue
        ch, ca = corners.get(p["fixture_id"], (None, None))
        out = settle_outcome(p["market"], p["selection"], f["home_goals"], f["away_goals"], ch, ca)
        if out is not None:
            sb().table("picks").update({"outcome": out}).eq("id", p["id"]).execute()
            settled += 1
    if settled:
        print(f"[write_db] settled {settled} picks")


def upsert_match_predictions(rows: list[dict]):
    now = _now()
    for r in rows:
        r["computed_at"] = now
        r.setdefault("pipeline", "free")
    for batch in chunked(rows):
        sb().table("match_predictions").upsert(
            batch, on_conflict="pipeline,fixture_id,market,selection"
        ).execute()
    print(f"[write_db] upserted {len(rows)} match_predictions")


def upsert_tournament_odds(rows: list[dict]):
    now = _now()
    for r in rows:
        r["computed_at"] = now
        r.setdefault("pipeline", "free")
    for batch in chunked(rows):
        sb().table("tournament_odds").upsert(
            batch, on_conflict="pipeline,team_id,model_version"
        ).execute()
    # model_version is part of the unique key, so a version bump would leave
    # stale rows behind and double the tournament table — prune superseded ones
    if rows:
        pipelines = {r["pipeline"] for r in rows}
        version = rows[0]["model_version"]
        for p in pipelines:
            sb().table("tournament_odds").delete().eq("pipeline", p).neq(
                "model_version", version
            ).execute()
    print(f"[write_db] upserted {len(rows)} tournament_odds")


def log_finished_predictions():
    """Calibration harness feed: when a fixture finishes, copy its final pre-match
    predictions into prediction_log with the realised outcome (spec §12)."""
    logged = {
        r["fixture_id"]
        for r in sb().table("prediction_log").select("fixture_id").execute().data
    }
    finished = (
        sb()
        .table("fixtures")
        .select("id, home_goals, away_goals")
        .eq("status", "finished")
        .execute()
        .data
    )
    todo = [f for f in finished if f["id"] not in logged and f["home_goals"] is not None]
    if not todo:
        settle_picks()
        return

    corners = _corner_counts([f["id"] for f in todo])

    def outcome(market, selection, hg, ag, fid=None):
        ch, ca = corners.get(fid, (None, None))
        return settle_outcome(market, selection, hg, ag, ch, ca)

    rows = []
    for f in todo:
        preds = (
            sb()
            .table("match_predictions")
            .select("pipeline, market, selection, probability, edge")
            .eq("fixture_id", f["id"])
            .execute()
            .data
        )
        for p in preds:
            rows.append(
                {
                    "pipeline": p["pipeline"],
                    "fixture_id": f["id"],
                    "market": p["market"],
                    "selection": p["selection"],
                    "probability": p["probability"],
                    "outcome": outcome(p["market"], p["selection"], f["home_goals"], f["away_goals"], f["id"]),
                }
            )
            # market baseline (spec v4 §5.4): de-vigged closing odds, recovered
            # from edge = p_model - p_market on the free pipeline's 1x2 rows
            if p["pipeline"] == "free" and p["market"] == "1x2" and p["edge"] is not None:
                p_market = float(p["probability"]) - float(p["edge"])
                rows.append(
                    {
                        "pipeline": "market",
                        "fixture_id": f["id"],
                        "market": "1x2",
                        "selection": p["selection"],
                        "probability": round(min(max(p_market, 0.0), 1.0), 4),
                        "outcome": outcome("1x2", p["selection"], f["home_goals"], f["away_goals"], f["id"]),
                    }
                )
    for batch in chunked(rows):
        sb().table("prediction_log").insert(batch).execute()
    print(f"[write_db] logged {len(rows)} prediction outcomes for {len(todo)} finished fixtures")
    settle_picks()
