"""The Picks engine (spec v4.1 §4) — rule-generated, calibrated-markets-only,
publicly tracked. Runs after compare in main.py.

Discipline rules (non-negotiable):
- generation is pure rules over current predictions; no hand-curation
- only calibrated markets (model_scores.n >= 30), except 1x2/ou25/btts which
  are calibrated from the backtest on day one
- a published pick is immutable: material changes retire the old row (with a
  reason) and publish a new one
- every pick settles publicly (write_db.settle_picks)

Tiers:
  Banker: p >= 0.65 and edge >= -0.01 (model not worse than market), conf = p
  Value:  edge >= 0.04 and p >= 0.25, scored by Kelly f = edge / (odds - 1)
Caps: max 2 picks per fixture, max 10 live per tier.
"""
from datetime import datetime, timedelta, timezone

from . import config
from .db import sb

HORIZON_DAYS = 14
ALWAYS_CALIBRATED = {"1x2", "ou25", "btts"}
BANKER_P, BANKER_EDGE_MIN = 0.65, -0.01
VALUE_EDGE_MIN, VALUE_P_MIN = 0.04, 0.25
MAX_PER_FIXTURE, MAX_LIVE_PER_TIER = 2, 10
# changes smaller than this keep the published pick (immutability with sanity)
P_TOLERANCE, EDGE_TOLERANCE = 0.03, 0.02


def _now():
    return datetime.now(timezone.utc)


def _site_pipelines() -> tuple[str, str]:
    """(blend pipeline for 1x2, model pipeline for other markets) for the site
    view, falling back to the free family when Pipeline B has no rows."""
    has_b = bool(
        sb().table("match_predictions").select("id").eq("pipeline", config.PIPELINE_STATSAPI)
        .limit(1).execute().data
    )
    if has_b:
        return config.PIPELINE_BLEND_STATSAPI, config.PIPELINE_STATSAPI
    return config.PIPELINE_BLEND_FREE, config.PIPELINE_FREE


def _signal(signals: dict, team_id: int | None, name: str):
    return signals.get((team_id, name)) if team_id else None


def _rationale(row: dict, fixture: dict, signals: dict, movement: dict) -> list[str]:
    """2-4 machine-built bullets from real data only (spec §4.4). Templates over
    structured signals; no free text invention."""
    bullets = []
    p, edge = float(row["probability"]), float(row["edge"] or 0)
    p_market = p - edge
    bullets.append(
        f"Model {p * 100:.0f}% vs market {p_market * 100:.0f}% ({edge * 100:+.0f}pt edge)"
    )
    mv = movement.get((row["fixture_id"], row["selection"])) if row["market"] == "1x2" else None
    if mv and abs(mv[0] - mv[1]) >= 0.05:
        bullets.append(f"Line moved {mv[0]:.2f} → {mv[1]:.2f} since opening")
    if row["market"].startswith("corners") or row["market"].startswith("team_corners"):
        cf_h = _signal(signals, fixture.get("home_id"), "corner_pace_for")
        ca_a = _signal(signals, fixture.get("away_id"), "corner_pace_against")
        if cf_h is not None and ca_a is not None:
            bullets.append(
                f"Home side wins {cf_h:.1f} corners/match, opponent concedes {ca_a:.1f}"
            )
    for side_key, side_label in (("home_id", "Home side"), ("away_id", "Away side")):
        over = _signal(signals, fixture.get(side_key), "xg_overperf")
        if over is not None and abs(over) >= 2.0 and len(bullets) < 4:
            direction = "overperforming xG" if over > 0 else "underperforming xG"
            bullets.append(f"{side_label} {direction} by {over:+.1f} goals — priced in")
    return bullets[:4]


def _line_movement(fixture_ids: list[int]) -> dict:
    """(fixture_id, selection) -> (opening_median, latest_median) for h2h."""
    out = {}
    for i in range(0, len(fixture_ids), 50):
        snaps = (
            sb().table("odds_snapshots")
            .select("fixture_id, selection, decimal_odds, fetched_at")
            .in_("fixture_id", fixture_ids[i : i + 50]).eq("market", "h2h")
            .order("fetched_at").execute().data
        )
        series: dict = {}
        for s in snaps:
            series.setdefault((s["fixture_id"], s["selection"]), []).append(float(s["decimal_odds"]))
        for k, v in series.items():
            if len(v) >= 2:
                out[k] = (v[0], v[-1])
    return out


def run():
    blend_pipe, model_pipe = _site_pipelines()
    now = _now()
    horizon = (now + timedelta(days=HORIZON_DAYS)).isoformat()

    fixtures = (
        sb().table("fixtures").select("id, home_id, away_id, kickoff")
        .eq("status", "scheduled").gte("kickoff", now.isoformat()).lte("kickoff", horizon)
        .execute().data
    )
    fx = {f["id"]: f for f in fixtures}
    if not fx:
        print("[picks] no fixtures in horizon")
        return

    calibrated = set(ALWAYS_CALIBRATED) | {
        s["market"]
        for s in sb().table("model_scores").select("market, n")
        .gte("n", config.EXPERIMENTAL_MIN_N).execute().data
    }

    preds = (
        sb().table("match_predictions").select("*")
        .in_("pipeline", [blend_pipe, model_pipe])
        .in_("fixture_id", list(fx)).not_.is_("edge", "null")
        .execute().data
    )
    # site view: blend rows own 1x2; the model pipeline owns everything else
    preds = [
        r for r in preds
        if (r["market"] == "1x2") == (r["pipeline"] == blend_pipe) and r["market"] in calibrated
    ]

    candidates = []
    for r in preds:
        p, edge = float(r["probability"]), float(r["edge"])
        odds = float(r["market_odds"]) if r["market_odds"] else None
        if p >= BANKER_P and edge >= BANKER_EDGE_MIN:
            candidates.append({**r, "tier": "banker", "score": p})
        elif edge >= VALUE_EDGE_MIN and p >= VALUE_P_MIN and odds and odds > 1:
            candidates.append({**r, "tier": "value", "score": edge / (odds - 1)})

    candidates.sort(key=lambda c: c["score"], reverse=True)
    chosen, per_fixture, per_tier = [], {}, {"banker": 0, "value": 0}
    for c in candidates:
        if per_fixture.get(c["fixture_id"], 0) >= MAX_PER_FIXTURE:
            continue
        if per_tier[c["tier"]] >= MAX_LIVE_PER_TIER:
            continue
        chosen.append(c)
        per_fixture[c["fixture_id"]] = per_fixture.get(c["fixture_id"], 0) + 1
        per_tier[c["tier"]] += 1

    live = (
        sb().table("picks").select("*")
        .is_("retired_at", "null").is_("outcome", "null")
        .execute().data
    )
    live_by_key = {(p["fixture_id"], p["market"], p["selection"]): p for p in live}
    chosen_keys = {(c["fixture_id"], c["market"], c["selection"]) for c in chosen}

    signals = {
        (s["team_id"], s["signal"]): float(s["value"])
        for s in sb().table("team_signals").select("team_id, signal, value").execute().data
    }
    movement = _line_movement(list(fx))

    published, retired, kept = 0, 0, 0
    now_iso = now.isoformat()
    for c in chosen:
        key = (c["fixture_id"], c["market"], c["selection"])
        old = live_by_key.get(key)
        if old is not None:
            if (
                abs(float(old["probability"]) - float(c["probability"])) <= P_TOLERANCE
                and abs(float(old["edge"] or 0) - float(c["edge"] or 0)) <= EDGE_TOLERANCE
                and old["tier"] == c["tier"]
            ):
                kept += 1
                continue  # immutable: numbers haven't moved materially
            sb().table("picks").update({
                "retired_at": now_iso,
                "rationale": (old.get("rationale") or []) + ["[retired: superseded by updated numbers]"],
            }).eq("id", old["id"]).execute()
            retired += 1
        sb().table("picks").insert({
            "fixture_id": c["fixture_id"],
            "pipeline": "site",
            "market": c["market"],
            "selection": c["selection"],
            "tier": c["tier"],
            "probability": c["probability"],
            "market_odds": c["market_odds"],
            "edge": c["edge"],
            "rationale": _rationale(c, fx[c["fixture_id"]], signals, movement),
            "published_at": now_iso,
        }).execute()
        published += 1

    for key, old in live_by_key.items():
        if key not in chosen_keys and old["fixture_id"] in fx:
            sb().table("picks").update({
                "retired_at": now_iso,
                "rationale": (old.get("rationale") or []) + ["[retired: no longer qualifies]"],
            }).eq("id", old["id"]).execute()
            retired += 1

    print(f"[picks] published={published} kept={kept} retired={retired} "
          f"(bankers={per_tier['banker']}, value={per_tier['value']})")


if __name__ == "__main__":
    run()
