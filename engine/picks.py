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
  Banker: p >= 0.65 and edge >= -0.01 (model not worse than market), conf = p.
          1x2 bankers come from the blend (market-anchored confidence).
  Value:  model-pipeline probability vs the QUOTED odds — a pick must clear the
          vig, not just the de-vigged price: p * odds > 1, plus edge >= 0.04
          and p >= 0.25. Scored by the true Kelly fraction
          f = (p*odds - 1) / (odds - 1).
Caps: max 2 picks per fixture, max 10 live per tier.

Leak plugs (v4.3) — fixtures the ratings are structurally blind to are
suppressed entirely (existing picks retired with the reason):
- a confirmed lineup showing >= 2 key absences (rotation: the market reprices
  on the team sheet within minutes; an Elo model does not)
- dead rubbers / mutual-draw fixtures, from fixture_incentives: where
  P(advance | result) barely moves for both teams, or a draw is near-optimal
  for both (classic matchday-3 incentive traps)
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import config
from .db import fetch_all, sb

HORIZON_DAYS = 14
ALWAYS_CALIBRATED = {"1x2", "ou25", "btts"}
BANKER_EDGE_MIN = -0.01            # never banker a selection the market rates materially worse
VALUE_EDGE_MIN, VALUE_P_MIN = 0.04, 0.25
MAX_PER_FIXTURE = 2
MAX_PER_CATEGORY = 6               # per tier, per {result, overs} — guarantees overs get airtime
# changes smaller than this keep the published pick (immutability with sanity)
P_TOLERANCE, EDGE_TOLERANCE = 0.03, 0.02
# leak plugs (v4.3)
KEY_ABSENCE_LIMIT = 2     # confirmed XI missing >= this many top players -> suppress fixture
LOW_LEVERAGE = 0.10       # max-min P(advance|result) below this for BOTH teams -> dead rubber

# v4.4 totals/overs picks. These are the "overs" the bettor asked for; a banker
# here is a high-confidence model play (priced at fair odds when no book exists),
# a value pick still needs a book edge. Experimental corners markets are
# banker-eligible (badged experimental in the UI) but never value-eligible —
# we don't claim value against a price we can't see or a model we haven't proven.
OVERS_MARKETS = {
    "ou25", "btts", "ou05_1h", "ou15_1h",
    "corners_o85", "corners_o95", "corners_o105", "corners_1h_o45",
    "team_corners_home_o45", "team_corners_away_o45",
}
OVERS_SELECTIONS = {"over", "yes"}     # the "overs" side; unders only ever ride the value tier
# model-only banker probability floor per market (overdispersed corners sit lower)
BANKER_MARKET_P = {
    "1x2": 0.65,
    "ou25": 0.62, "btts": 0.62, "ou05_1h": 0.66, "ou15_1h": 0.60,
    "corners_o85": 0.62, "corners_o95": 0.60, "corners_o105": 0.58, "corners_1h_o45": 0.60,
    "team_corners_home_o45": 0.60, "team_corners_away_o45": 0.60,
}
BANKER_MODEL_MIN_ODDS = 1.25       # skip trivially short model-only bankers (no book to anchor)


def _category(market: str) -> str:
    return "result" if market == "1x2" else "overs"


def _candidate_tiers(r: dict, calibrated: set) -> list[dict]:
    """Pure classification of one prediction row into banker/value candidate(s).

    Banker: high model probability — calibrated markets, or any OVERS market on
    the over/yes side (model-only, priced at fair odds when there's no book).
    Value:  calibrated market with a book edge that is positive-EV at the quoted
            price. Returns 0, 1 or 2 candidate dicts (deduped later)."""
    mkt, sel = r["market"], r["selection"]
    cat = _category(mkt)
    p = float(r["probability"])
    edge = float(r["edge"]) if r.get("edge") is not None else None
    odds = float(r["market_odds"]) if r.get("market_odds") else None
    out = []

    if mkt in calibrated and edge is not None and odds and odds > 1:
        if edge >= VALUE_EDGE_MIN and p >= VALUE_P_MIN and p * odds > 1:
            out.append({"tier": "value", "category": cat, "score": (p * odds - 1) / (odds - 1),
                        "model_only": False})

    banker_market = (mkt in calibrated) or (mkt in OVERS_MARKETS)
    banker_side = (cat == "result") or (sel in OVERS_SELECTIONS)
    if banker_market and banker_side and p >= BANKER_MARKET_P.get(mkt, 0.65):
        if edge is None or edge >= BANKER_EDGE_MIN:           # don't fight a confident market
            eff_odds = odds if odds else round(1.0 / p, 3)
            if odds is not None or eff_odds >= BANKER_MODEL_MIN_ODDS:
                out.append({"tier": "banker", "category": cat, "score": p, "model_only": odds is None})
    return out


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
    p = float(row["probability"])
    if row.get("market_odds"):
        edge = float(row["edge"] or 0)
        p_market = p - edge
        bullets.append(
            f"Model {p * 100:.0f}% vs market {p_market * 100:.0f}% ({edge * 100:+.0f}pt edge)"
        )
    else:
        # model-only overs play: no book to compare against, so quote fair odds
        bullets.append(f"Model rates this {p * 100:.0f}% — fair odds {1 / p:.2f} (no book price yet)")
    mv = movement.get((row["fixture_id"], row["selection"])) if row["market"] == "1x2" else None
    if mv and abs(mv[0] - mv[1]) >= 0.05:
        bullets.append(f"Line moved {mv[0]:.2f} → {mv[1]:.2f} since opening")
    if row["market"].startswith("team_corners"):
        # name the team the market is about, and the opponent it attacks
        atk, dfn = ("home_id", "away_id") if "_home_" in row["market"] else ("away_id", "home_id")
        cf = _signal(signals, fixture.get(atk), "corner_pace_for")
        ca = _signal(signals, fixture.get(dfn), "corner_pace_against")
        if cf is not None and ca is not None:
            side = "Home" if atk == "home_id" else "Away"
            bullets.append(f"{side} side wins {cf:.1f} corners/match, opponent concedes {ca:.1f}")
    elif row["market"].startswith("corners"):
        cf_h = _signal(signals, fixture.get("home_id"), "corner_pace_for")
        cf_a = _signal(signals, fixture.get("away_id"), "corner_pace_for")
        if cf_h is not None and cf_a is not None:
            bullets.append(f"Sides average {cf_h:.1f} and {cf_a:.1f} corners won per match")
    for side_key, side_label in (("home_id", "Home side"), ("away_id", "Away side")):
        over = _signal(signals, fixture.get(side_key), "xg_overperf")
        if over is not None and abs(over) >= 2.0 and len(bullets) < 4:
            direction = "overperforming xG" if over > 0 else "underperforming xG"
            bullets.append(f"{side_label} {direction} by {over:+.1f} goals — priced in")
    return bullets[:4]


def _line_movement(fixture_ids: list[int]) -> dict:
    """(fixture_id, selection) -> (opening_odds, latest_odds) for h2h.

    One asc + one desc page per small batch: the snapshot table outgrows the
    1000-row cap fast, so 'first 1000 ascending' alone misses the latest pull."""
    out = {}
    for i in range(0, len(fixture_ids), 5):
        batch = fixture_ids[i : i + 5]

        def _page(desc: bool):
            return (
                sb().table("odds_snapshots")
                .select("fixture_id, selection, decimal_odds, fetched_at")
                .in_("fixture_id", batch).eq("market", "h2h")
                .order("fetched_at", desc=desc).limit(1000).execute().data
            )

        opening: dict = {}
        for s in _page(desc=False):
            opening.setdefault((s["fixture_id"], s["selection"]), float(s["decimal_odds"]))
        latest: dict = {}
        for s in _page(desc=True):
            latest.setdefault((s["fixture_id"], s["selection"]), float(s["decimal_odds"]))
        for k, opening_odds in opening.items():
            if k in latest and latest[k] != opening_odds:
                out[k] = (opening_odds, latest[k])
    return out


def _suppressed_fixtures(fixture_ids: list[int]) -> dict[int, str]:
    """fixture_id -> reason for fixtures the picks engine must step aside from."""
    out: dict[int, str] = {}
    for i in range(0, len(fixture_ids), 100):
        batch = fixture_ids[i : i + 100]
        for lu in (
            sb().table("lineups").select("fixture_id, key_absences")
            .in_("fixture_id", batch).eq("confirmed", True).execute().data
        ):
            if len(lu.get("key_absences") or []) >= KEY_ABSENCE_LIMIT:
                out[lu["fixture_id"]] = (
                    f"confirmed lineup missing {len(lu['key_absences'])} key players"
                )
        for r in (
            sb().table("fixture_incentives").select("*")
            .in_("fixture_id", batch).execute().data
        ):
            h_vals = [r[f"home_adv_{k}"] for k in ("win", "draw", "loss") if r[f"home_adv_{k}"] is not None]
            a_vals = [r[f"away_adv_{k}"] for k in ("win", "draw", "loss") if r[f"away_adv_{k}"] is not None]
            h_vals = [float(v) for v in h_vals]
            a_vals = [float(v) for v in a_vals]
            if (
                len(h_vals) >= 2 and len(a_vals) >= 2
                and max(h_vals) - min(h_vals) < LOW_LEVERAGE
                and max(a_vals) - min(a_vals) < LOW_LEVERAGE
            ):
                out[r["fixture_id"]] = "low-stakes fixture: advancement barely depends on the result"
            elif r["mutual_draw"]:
                out[r["fixture_id"]] = "a draw likely suits both teams"
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

    # calibrated = enough sample AND beats the base-rate predictor (quality
    # gate, v4.3) — sample size alone must not promote a bad market
    calibrated = set(ALWAYS_CALIBRATED) | {
        s["market"]
        for s in sb().table("model_scores").select("market, n")
        .gte("n", config.EXPERIMENTAL_MIN_N).eq("beats_baseline", True).execute().data
    }

    suppressed = _suppressed_fixtures(list(fx))

    # Fetch the markets picks can be built from (no edge filter — model-only
    # overs bankers don't need a book price). Ownership: the blend owns 1x2
    # (market-anchored), the model pipeline owns every overs market.
    whitelist = ["1x2", *OVERS_MARKETS]
    preds = fetch_all(
        lambda: sb().table("match_predictions").select("*")
        .in_("pipeline", [blend_pipe, model_pipe])
        .in_("fixture_id", list(fx)).in_("market", whitelist)
        .order("id")
    )
    rows = [
        r for r in preds
        if (r["pipeline"] == blend_pipe if r["market"] == "1x2" else r["pipeline"] == model_pipe)
        and r["fixture_id"] not in suppressed
    ]

    candidates = []
    for r in rows:
        for c in _candidate_tiers(r, calibrated):
            candidates.append({**r, **c})
    # one pick per selection: a banker outranks a value claim on the same key
    banker_keys = {
        (c["fixture_id"], c["market"], c["selection"]) for c in candidates if c["tier"] == "banker"
    }
    candidates = [
        c for c in candidates
        if not (c["tier"] == "value" and (c["fixture_id"], c["market"], c["selection"]) in banker_keys)
    ]

    candidates.sort(key=lambda c: c["score"], reverse=True)
    chosen, per_fixture, per_bucket = [], {}, defaultdict(int)
    for c in candidates:
        bucket = (c["tier"], c["category"])
        if per_fixture.get(c["fixture_id"], 0) >= MAX_PER_FIXTURE:
            continue
        if per_bucket[bucket] >= MAX_PER_CATEGORY:
            continue
        chosen.append(c)
        per_fixture[c["fixture_id"]] = per_fixture.get(c["fixture_id"], 0) + 1
        per_bucket[bucket] += 1
    per_tier = {
        "banker": per_bucket[("banker", "result")] + per_bucket[("banker", "overs")],
        "value": per_bucket[("value", "result")] + per_bucket[("value", "overs")],
    }

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
            reason = suppressed.get(old["fixture_id"], "no longer qualifies")
            sb().table("picks").update({
                "retired_at": now_iso,
                "rationale": (old.get("rationale") or []) + [f"[retired: {reason}]"],
            }).eq("id", old["id"]).execute()
            retired += 1

    print(f"[picks] published={published} kept={kept} retired={retired} "
          f"suppressed_fixtures={len(suppressed)} "
          f"(bankers={per_tier['banker']}, value={per_tier['value']})")


if __name__ == "__main__":
    run()
