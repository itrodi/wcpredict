"""Elo -> Poisson scoreline matrix -> markets (1X2, O/U 2.5, BTTS, correct score),
plus de-vig of the latest book h2h prices -> edge on the 1X2 market (spec §5 step 4)."""
import math
from collections import defaultdict

import numpy as np

from . import config
from .db import sb


def elo_lambdas(elo_home: float, elo_away: float, host_home: bool) -> tuple[float, float]:
    """Map an Elo gap to Poisson goal rates (v2).

    lambda_home = (TOTAL_GOALS/2) * exp(+beta*dr)
    lambda_away = (TOTAL_GOALS/2) * exp(-beta*dr)

    Even matches keep the TOTAL_GOALS expected total; strength gaps RAISE the
    total (the favourite's rate grows faster than the underdog's shrinks), so
    totals markets vary by matchup. The v1 fixed-total split made P(over 2.5)
    identical for every fixture — sum of independent Poissons depends only on
    lambda_h + lambda_a, which v1 held constant by construction."""
    dr = elo_home - elo_away + (config.ELO_HOME_ADV if host_home else 0)
    base = config.TOTAL_GOALS / 2.0
    beta = config.MODEL_PARAMS["ELO_GOAL_BETA"]
    lam_h = min(max(base * math.exp(beta * dr), config.LAMBDA_MIN), config.LAMBDA_MAX)
    lam_a = min(max(base * math.exp(-beta * dr), config.LAMBDA_MIN), config.LAMBDA_MAX)
    return lam_h, lam_a


def scoreline_matrix(lam_h: float, lam_a: float) -> np.ndarray:
    k = np.arange(config.GOAL_GRID)
    ph = np.exp(-lam_h) * lam_h**k / np.array([math.factorial(int(i)) for i in k])
    pa = np.exp(-lam_a) * lam_a**k / np.array([math.factorial(int(i)) for i in k])
    m = np.outer(ph, pa)
    return m / m.sum()  # renormalise the truncated grid


def markets_from_matrix(m: np.ndarray) -> list[tuple[str, str, float]]:
    """(market, selection, probability) rows from a scoreline matrix."""
    home = float(np.tril(m, -1).sum())  # rows = home goals, i > j
    draw = float(np.trace(m))
    away = float(np.triu(m, 1).sum())
    total = np.add.outer(np.arange(config.GOAL_GRID), np.arange(config.GOAL_GRID))
    over = float(m[total >= 3].sum())
    btts = float(m[1:, 1:].sum())
    rows = [
        ("1x2", "home", home),
        ("1x2", "draw", draw),
        ("1x2", "away", away),
        ("ou25", "over", over),
        ("ou25", "under", 1.0 - over),
        ("btts", "yes", btts),
        ("btts", "no", 1.0 - btts),
    ]
    cs_covered = 0.0
    for i in range(5):
        for j in range(5):
            p = float(m[i, j])
            rows.append(("cs", f"{i}-{j}", p))
            cs_covered += p
    rows.append(("cs", "other", max(0.0, 1.0 - cs_covered)))
    return rows


def _latest_devigged_h2h(fixture_ids: list[int]) -> dict[int, dict[str, tuple[float, float]]]:
    """fixture_id -> selection -> (median_decimal_odds, devigged_probability),
    using only each fixture's most recent fetch batch."""
    out: dict[int, dict[str, tuple[float, float]]] = {}
    for i in range(0, len(fixture_ids), 50):
        batch = fixture_ids[i : i + 50]
        snaps = (
            sb()
            .table("odds_snapshots")
            .select("fixture_id, selection, decimal_odds, fetched_at")
            .in_("fixture_id", batch)
            .eq("market", "h2h")
            .order("fetched_at", desc=True)
            .limit(2000)
            .execute()
            .data
        )
        latest_ts: dict[int, str] = {}
        for s in snaps:
            latest_ts.setdefault(s["fixture_id"], s["fetched_at"])
        by_sel: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for s in snaps:
            if s["fetched_at"] == latest_ts.get(s["fixture_id"]):
                by_sel[s["fixture_id"]][s["selection"]].append(float(s["decimal_odds"]))
        for fid, sels in by_sel.items():
            if set(sels) != {"home", "draw", "away"}:
                continue
            med = {sel: float(np.median(odds)) for sel, odds in sels.items()}
            implied = {sel: 1.0 / o for sel, o in med.items()}
            overround = sum(implied.values())
            out[fid] = {sel: (med[sel], implied[sel] / overround) for sel in med}
    return out


def run() -> list[dict]:
    """Build match_predictions rows for all not-yet-finished fixtures with known teams."""
    teams = {t["id"]: t for t in sb().table("teams").select("id, elo").execute().data}
    fixtures = (
        sb()
        .table("fixtures")
        .select("id, home_id, away_id, host_home, status")
        .neq("status", "finished")
        .execute()
        .data
    )
    fixtures = [f for f in fixtures if f["home_id"] and f["away_id"]]
    book = _latest_devigged_h2h([f["id"] for f in fixtures])

    rows = []
    for f in fixtures:
        lam_h, lam_a = elo_lambdas(
            float(teams[f["home_id"]]["elo"]),
            float(teams[f["away_id"]]["elo"]),
            bool(f["host_home"]),
        )
        m = scoreline_matrix(lam_h, lam_a)
        for market, selection, p in markets_from_matrix(m):
            row = {
                "pipeline": config.PIPELINE_FREE,
                "fixture_id": f["id"],
                "market": market,
                "selection": selection,
                "probability": round(p, 4),
                "fair_odds": round(1.0 / p, 3) if p > 1e-4 else None,
                "market_odds": None,
                "edge": None,
                "model_version": config.MODEL_VERSION,
            }
            if market == "1x2" and f["id"] in book:
                med_odds, devig_p = book[f["id"]][selection]
                row["market_odds"] = round(med_odds, 3)
                row["edge"] = round(p - devig_p, 4)
            rows.append(row)
    print(f"[match_model] {len(rows)} prediction rows for {len(fixtures)} fixtures")
    return rows


if __name__ == "__main__":
    run()
