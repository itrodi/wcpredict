"""Upserts into Supabase via the service role key.

`computed_at` is set explicitly on every row — a column default only fires on
insert, so relying on `default now()` would freeze every "last updated" badge
at first write (spec §4).
"""
from datetime import datetime, timezone

from .db import chunked, sb


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        return

    def outcome(market, selection, hg, ag):
        if market == "1x2":
            return {"home": hg > ag, "draw": hg == ag, "away": hg < ag}[selection]
        if market == "ou25":
            return (hg + ag >= 3) if selection == "over" else (hg + ag <= 2)
        if market == "btts":
            return (hg >= 1 and ag >= 1) if selection == "yes" else not (hg >= 1 and ag >= 1)
        if market == "cs":
            return selection == f"{hg}-{ag}" if selection != "other" else (hg > 4 or ag > 4)
        return None

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
                    "outcome": outcome(p["market"], p["selection"], f["home_goals"], f["away_goals"]),
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
                        "outcome": outcome("1x2", p["selection"], f["home_goals"], f["away_goals"]),
                    }
                )
    for batch in chunked(rows):
        sb().table("prediction_log").insert(batch).execute()
    print(f"[write_db] logged {len(rows)} prediction outcomes for {len(todo)} finished fixtures")
