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
            .select("pipeline, market, fixture_id, probability, outcome")
            .not_.is_("outcome", "null")
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
        scores.append(
            {
                "pipeline": pipeline,
                "market": market,
                "n": len({i["fixture_id"] for i in items}),
                "brier": round(brier, 6),
                "log_loss": round(log_loss, 6),
                "computed_at": now,
            }
        )
    for batch in chunked(scores):
        sb().table("model_scores").upsert(batch, on_conflict="pipeline,market").execute()
    print(f"[compare] upserted {len(scores)} model_scores rows")


if __name__ == "__main__":
    run()
