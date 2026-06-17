"""Upserts into Supabase via the service role key.

`computed_at` is set explicitly on every row — a column default only fires on
insert, so relying on `default now()` would freeze every "last updated" badge
at first write (spec §4).
"""
from datetime import datetime, timezone

from .db import chunked, fetch_all, sb


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def settle_outcome(market, selection, hg, ag, corners_home=None, corners_away=None,
                   duration="REGULAR", ht_hg=None, ht_ag=None):
    """Realised boolean outcome for a selection, or None when unsettleable.

    Every market is priced (and traded) on 90 minutes, but football-data's
    fullTime score includes extra time. When `duration` != 'REGULAR' the 90'
    score was level, so 1x2 settles as a draw and goal-count markets (ou25,
    btts, cs) are unsettleable from the data we have. Corner counts likewise
    cover the whole match, so corners markets only settle on REGULAR.
    First-half markets settle from the stored half-time score.
    """
    went_extra = duration not in (None, "REGULAR")
    if market == "1x2":
        if went_extra:
            return selection == "draw"
        return {"home": hg > ag, "draw": hg == ag, "away": hg < ag}[selection]
    if market in ("ou15", "ou25", "ou35", "ou45", "ou55"):
        if went_extra:
            return None
        line = int(market[2:]) / 10.0   # 'ou35' -> 3.5
        total = hg + ag
        return (total > line) if selection == "over" else (total < line)
    if market == "btts":
        if went_extra:
            return None
        return (hg >= 1 and ag >= 1) if selection == "yes" else not (hg >= 1 and ag >= 1)
    if market == "cs":
        if went_extra:
            return None
        return selection == f"{hg}-{ag}" if selection != "other" else (hg > 4 or ag > 4)
    if market == "ht_1x2":
        if ht_hg is None or ht_ag is None:
            return None
        return {"home": ht_hg > ht_ag, "draw": ht_hg == ht_ag, "away": ht_hg < ht_ag}[selection]
    if market in ("ou05_1h", "ou15_1h"):
        if ht_hg is None or ht_ag is None:
            return None
        line = 0.5 if market == "ou05_1h" else 1.5
        total = ht_hg + ht_ag
        return total > line if selection == "over" else total < line
    if market == "htft":
        if ht_hg is None or ht_ag is None:
            return None
        ht = "home" if ht_hg > ht_ag else "draw" if ht_hg == ht_ag else "away"
        ft = "draw" if went_extra else ("home" if hg > ag else "draw" if hg == ag else "away")
        return selection == f"{ht}_{ft}"
    if went_extra:
        return None  # corners stats cover 120' — not the 90' market
    if market.startswith("corners_o") and corners_home is not None and corners_away is not None:
        line = int(market.removeprefix("corners_o")) / 10.0   # 'corners_o95' -> 9.5
        total = corners_home + corners_away
        return total > line if selection == "over" else total < line
    if market.startswith("team_corners_") and corners_home is not None and corners_away is not None:
        count = corners_home if "_home_" in market else corners_away
        line = int(market.rsplit("_o", 1)[1]) / 10.0          # '..._o45' -> 4.5
        return count > line if selection == "over" else count < line
    return None


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


def record_closing_odds():
    """Upsert the current de-vigged medians for every still-scheduled fixture.

    Runs every refresh; once a fixture leaves 'scheduled' its row stops
    updating, so the last write IS the closing price. This is the CLV record —
    it survives odds_snapshots pruning."""
    from .match_model import _latest_devigged

    fixtures = sb().table("fixtures").select("id").eq("status", "scheduled").execute().data
    fids = [f["id"] for f in fixtures]
    if not fids:
        return
    now = _now()
    rows = []
    for src_market, market, sels in (
        ("h2h", "1x2", {"home", "draw", "away"}),
        ("ou25", "ou25", {"over", "under"}),
    ):
        for fid, sel_map in _latest_devigged(fids, src_market, sels).items():
            for sel, (med, devig_p) in sel_map.items():
                rows.append({
                    "fixture_id": fid,
                    "market": market,
                    "selection": sel,
                    "decimal_odds": round(med, 3),
                    "devigged_p": round(devig_p, 4),
                    "recorded_at": now,
                })
    for batch in chunked(rows):
        sb().table("closing_odds").upsert(batch, on_conflict="fixture_id,market,selection").execute()
    print(f"[write_db] recorded closing odds for {len({r['fixture_id'] for r in rows})} fixtures")


def settle_picks():
    """Settle every published pick on finished fixtures (spec v4.1 §4.2) —
    INCLUDING retired ones: a pick that was live for days before being retired
    was followable, and excluding it would select losers out of the public
    record. Also stamps each pick's CLV (published odds vs the closing price)."""
    open_picks = (
        sb().table("picks").select("id, fixture_id, market, selection, market_odds")
        .is_("outcome", "null")
        .execute().data
    )
    if not open_picks:
        return
    fids = list({p["fixture_id"] for p in open_picks})
    fixtures = {
        f["id"]: f
        for f in sb().table("fixtures")
        .select("id, status, home_goals, away_goals, duration, ht_home_goals, ht_away_goals")
        .in_("id", fids).eq("status", "finished").execute().data
        if f["home_goals"] is not None
    }
    corners = _corner_counts(list(fixtures))
    closing: dict[tuple, float] = {}
    for i in range(0, len(fids), 100):
        for c in (
            sb().table("closing_odds").select("fixture_id, market, selection, decimal_odds")
            .in_("fixture_id", fids[i : i + 100]).execute().data
        ):
            closing[(c["fixture_id"], c["market"], c["selection"])] = float(c["decimal_odds"])
    settled = 0
    for p in open_picks:
        f = fixtures.get(p["fixture_id"])
        if not f:
            continue
        ch, ca = corners.get(p["fixture_id"], (None, None))
        out = settle_outcome(
            p["market"], p["selection"], f["home_goals"], f["away_goals"], ch, ca,
            f.get("duration") or "REGULAR", f.get("ht_home_goals"), f.get("ht_away_goals"),
        )
        if out is not None:
            patch = {"outcome": out}
            close = closing.get((p["fixture_id"], p["market"], p["selection"]))
            if close and close > 1 and p["market_odds"]:
                patch["closing_odds"] = round(close, 3)
                patch["clv"] = round(float(p["market_odds"]) / close - 1, 4)
            sb().table("picks").update(patch).eq("id", p["id"]).execute()
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


def upsert_score_grids(rows: list[dict]):
    if not rows:
        return
    for batch in chunked(rows):
        sb().table("score_grids").upsert(batch, on_conflict="fixture_id").execute()
    print(f"[write_db] upserted {len(rows)} score_grids")


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
    # paginate: this table exceeds PostgREST's 1000-row cap within days, and an
    # incomplete set here re-inserts duplicates that corrupt model_scores
    logged = {
        r["fixture_id"]
        for r in fetch_all(lambda: sb().table("prediction_log").select("fixture_id"))
    }
    finished = (
        sb()
        .table("fixtures")
        .select("id, home_goals, away_goals, duration, ht_home_goals, ht_away_goals")
        .eq("status", "finished")
        .execute()
        .data
    )
    todo = [f for f in finished if f["id"] not in logged and f["home_goals"] is not None]
    if not todo:
        settle_picks()
        return

    corners = _corner_counts([f["id"] for f in todo])
    fx_by_id = {f["id"]: f for f in todo}

    def outcome(market, selection, hg, ag, fid=None):
        ch, ca = corners.get(fid, (None, None))
        f = fx_by_id.get(fid, {})
        return settle_outcome(
            market, selection, hg, ag, ch, ca,
            f.get("duration") or "REGULAR", f.get("ht_home_goals"), f.get("ht_away_goals"),
        )

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
            # from edge = p_model - p_market on the free pipeline's priced rows
            if p["pipeline"] == "free" and p["market"] in ("1x2", "ou25") and p["edge"] is not None:
                p_market = float(p["probability"]) - float(p["edge"])
                rows.append(
                    {
                        "pipeline": "market",
                        "fixture_id": f["id"],
                        "market": p["market"],
                        "selection": p["selection"],
                        "probability": round(min(max(p_market, 0.0), 1.0), 4),
                        "outcome": outcome(p["market"], p["selection"], f["home_goals"], f["away_goals"], f["id"]),
                    }
                )
    for batch in chunked(rows):
        sb().table("prediction_log").insert(batch).execute()
    print(f"[write_db] logged {len(rows)} prediction outcomes for {len(todo)} finished fixtures")
    settle_picks()
