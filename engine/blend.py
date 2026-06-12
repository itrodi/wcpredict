"""Market blend (spec v4 §5.3): p = w*p_market + (1-w)*p_model.

Applies to markets where The Odds API has prices (1X2 in v1). Consumes the two
model pipelines' rows (which already carry de-vigged market info via edge =
p_model - p_market) and emits pipeline='blend_free' / 'blend_statsapi' rows.
"""
from . import config

P = config.MODEL_PARAMS
BLENDS = {
    config.PIPELINE_FREE: config.PIPELINE_BLEND_FREE,
    config.PIPELINE_STATSAPI: config.PIPELINE_BLEND_STATSAPI,
}


def run(model_rows: list[dict]) -> list[dict]:
    """model_rows: match_predictions rows from pipelines A and B (pre-upsert)."""
    w = P["BLEND_W_MARKET"]
    out = []
    for r in model_rows:
        if r["market"] != "1x2" or r.get("edge") is None or r["pipeline"] not in BLENDS:
            continue
        p_model = float(r["probability"])
        p_market = p_model - float(r["edge"])  # edge = p_model - devigged p_market
        p = w * p_market + (1 - w) * p_model
        out.append(
            {
                "pipeline": BLENDS[r["pipeline"]],
                "fixture_id": r["fixture_id"],
                "market": "1x2",
                "selection": r["selection"],
                "probability": round(p, 4),
                "fair_odds": round(1.0 / p, 3) if p > 1e-4 else None,
                "market_odds": r["market_odds"],
                "edge": round(p - p_market, 4),
                "model_version": f"blend-w{w:g}",
            }
        )
    print(f"[blend] {len(out)} blended rows (w_market={w})")
    return out
