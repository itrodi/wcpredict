"""Engine entry point — dual-pipeline run (spec v4 §1).

Sequence: A ingest -> B ingest -> ratings -> models -> blend -> sims -> compare.
Failure isolation: every stage is fenced; Pipeline B failing (vendor outage,
key revoked) logs and skips B while A completes untouched — the site must keep
working on A alone. Run with: python -m engine.main
"""
import sys
import traceback
from datetime import datetime, timezone

from . import (
    blend,
    compare,
    config,
    ingest_fd,
    ingest_odds,
    ingest_statsapi,
    ingest_statsapi_extra,
    match_model,
    match_model_b,
    ops,
    picks,
    ratings,
    ratings_xg,
    signals,
    simulate,
    write_db,
)


def _stage(name, fn):
    try:
        return fn()
    except Exception:
        print(f"[main] stage '{name}' FAILED:", file=sys.stderr)
        traceback.print_exc()
        return None


def main():
    statsapi_enabled = bool(config.STATSAPI_KEY)
    if not statsapi_enabled:
        print("[main] STATSAPI_KEY not set — Pipeline B skipped, running A only")

    # ---- ingest ----
    _stage("ingest_fd", ingest_fd.run)                        # A: fixtures + results
    _stage("ingest_odds", ingest_odds.run)                    # shared: h2h odds for BOTH pipelines
    if statsapi_enabled:
        _stage("ingest_statsapi", ingest_statsapi.run)        # B: xmap, match_stats, lineups
        _stage("ingest_statsapi_extra", ingest_statsapi_extra.run)  # B: odds probe, shotmaps, players

    # ---- ratings ----
    _stage("ratings", ratings.run)                            # A: results-Elo
    if statsapi_enabled:
        _stage("ratings_xg", ratings_xg.run)                  # B: xG-Elo

    _stage("prediction_log", write_db.log_finished_predictions)

    # ---- match models + blend ----
    rows_a = _stage("match_model", match_model.run) or []
    rows_b = (_stage("match_model_b", match_model_b.run) or []) if statsapi_enabled else []
    blended = _stage("blend", lambda: blend.run(rows_a + rows_b)) or []
    all_rows = rows_a + rows_b + blended
    if all_rows:
        _stage("write_predictions", lambda: write_db.upsert_match_predictions(all_rows))

    # ---- tournament sims, one per model pipeline ----
    odds_a = _stage("simulate_free", lambda: simulate.run(pipeline=config.PIPELINE_FREE))
    if odds_a:
        _stage("write_todds_free", lambda: write_db.upsert_tournament_odds(odds_a))
    if statsapi_enabled:
        odds_b = _stage("simulate_statsapi", lambda: simulate.run(pipeline=config.PIPELINE_STATSAPI))
        if odds_b:
            _stage("write_todds_statsapi", lambda: write_db.upsert_tournament_odds(odds_b))

    # ---- scoreboard, signals, picks + housekeeping ----
    _stage("compare", compare.run)
    if statsapi_enabled:
        _stage("signals", signals.run)                        # team + referee signals (v4.1 §5.2/5.4)
    _stage("picks", picks.run)                                # picks AFTER compare (v4.1 §4.1)
    _stage("prune_snapshots", ingest_odds.prune_snapshots)
    if statsapi_enabled:
        _stage("prune_payloads", ingest_statsapi.prune_payloads)

    # ---- CI-style coverage assertion (v4.1 §1.2) + run stamp ----
    def coverage():
        from .db import sb
        n_fx = len(sb().table("fixtures").select("id").execute().data)
        n_pred = len({
            r["fixture_id"]
            for r in sb().table("match_predictions").select("fixture_id")
            .eq("pipeline", config.PIPELINE_FREE).execute().data
        })
        ok = n_fx >= ops.EXPECTED_FIXTURES
        ops.set_status("pipeline_a_coverage", {
            "fixtures": n_fx, "fixtures_with_free_predictions": n_pred,
            "expected": ops.EXPECTED_FIXTURES, "ok": ok,
        })
        if not ok:
            print(f"[main] ERROR: only {n_fx}/{ops.EXPECTED_FIXTURES} fixtures in DB — "
                  f"see /admin/health")
    _stage("coverage_assertion", coverage)
    _stage("last_refresh", lambda: ops.set_status(
        "last_refresh", {"at": datetime.now(timezone.utc).isoformat()}
    ))
    print("[main] run complete")


if __name__ == "__main__":
    main()
