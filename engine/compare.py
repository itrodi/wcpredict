"""Model scoreboard (spec v4 §5.4): Brier + log-loss per (pipeline, market)
from prediction_log, including the pipeline='market' baseline (de-vigged
closing odds) — the bar both models must beat.
"""
import math
from collections import defaultdict
from datetime import datetime, timezone

from .db import chunked, sb

EPS = 1e-6


def run():
    rows = []
    offset, page = 0, 1000
    while True:
        batch = (
            sb()
            .table("prediction_log")
            .select("id, pipeline, market, fixture_id, selection, probability, outcome")
            .not_.is_("outcome", "null")
            .order("id")
            .range(offset, offset + page - 1)
            .execute()
            .data
        )
        rows.extend(batch)
        if len(batch) < page:
            break
        offset += page
    if not rows:
        print("[compare] nothing logged yet")
        return

    # defensive dedupe (earliest row wins): duplicated log rows would overweight
    # their fixtures in every Brier/log-loss average on the public scoreboard
    seen: set = set()
    unique_rows = []
    for r in rows:
        key = (r["pipeline"], r["fixture_id"], r["market"], r["selection"])
        if key not in seen:
            seen.add(key)
            unique_rows.append(r)
    if len(unique_rows) < len(rows):
        print(f"[compare] WARNING: dropped {len(rows) - len(unique_rows)} duplicate prediction_log rows")
    rows = unique_rows

    grouped: dict[tuple[str, str], list] = defaultdict(list)
    for r in rows:
        grouped[(r["pipeline"], r["market"])].append(r)

    now = datetime.now(timezone.utc).isoformat()
    scores = []
    for (pipeline, market), items in grouped.items():
        ps = [min(max(float(i["probability"]), EPS), 1 - EPS) for i in items]
        ys = [1.0 if i["outcome"] else 0.0 for i in items]
        brier = sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)
        log_loss = -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in zip(ps, ys)) / len(ps)
        # quality gate input: does the model beat a constant base-rate predictor
        # on the same rows? Sample size alone must not graduate a market out of
        # "experimental" — n>=30 with worse-than-climatology skill stays gated.
        base_p = min(max(sum(ys) / len(ys), EPS), 1 - EPS)
        ll_base = -sum(y * math.log(base_p) + (1 - y) * math.log(1 - base_p) for y in ys) / len(ys)
        beats = None if pipeline == "market" else bool(log_loss <= ll_base)
        scores.append(
            {
                "pipeline": pipeline,
                "market": market,
                "n": len({i["fixture_id"] for i in items}),
                "brier": round(brier, 6),
                "log_loss": round(log_loss, 6),
                "beats_baseline": beats,
                "computed_at": now,
            }
        )
    for batch in chunked(scores):
        sb().table("model_scores").upsert(batch, on_conflict="pipeline,market").execute()
    print(f"[compare] upserted {len(scores)} model_scores rows")


if __name__ == "__main__":
    run()
