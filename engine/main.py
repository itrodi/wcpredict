"""Engine entry point — runs the full pipeline (spec §5).

Each stage is fenced so a single upstream failure degrades gracefully instead of
killing the run (e.g. exhausted odds credits never block fresh predictions).
Run with: python -m engine.main
"""
import sys
import traceback

from . import ingest_fd, ingest_odds, match_model, ratings, simulate, write_db


def _stage(name, fn):
    try:
        return fn()
    except Exception:
        print(f"[main] stage '{name}' FAILED:", file=sys.stderr)
        traceback.print_exc()
        return None


def main():
    _stage("ingest_fd", ingest_fd.run)                       # 1. fixtures + results
    _stage("ingest_odds", ingest_odds.run)                   # 2. h2h odds -> odds_snapshots
    _stage("ratings", ratings.run)                           # 3. Elo refresh
    _stage("prediction_log", write_db.log_finished_predictions)

    rows = _stage("match_model", match_model.run)            # 4. per-fixture markets
    if rows:
        _stage("write_predictions", lambda: write_db.upsert_match_predictions(rows))

    odds = _stage("simulate", simulate.run)                  # 5. Monte Carlo tournament
    if odds:
        _stage("write_tournament_odds", lambda: write_db.upsert_tournament_odds(odds))

    _stage("prune_snapshots", ingest_odds.prune_snapshots)   # keep 500MB DB lean
    print("[main] run complete")


if __name__ == "__main__":
    main()
