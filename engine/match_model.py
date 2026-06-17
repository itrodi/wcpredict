"""Elo -> Poisson scoreline matrix -> markets (1X2, O/U 2.5, BTTS, correct score),
plus de-vig of the latest book h2h prices -> edge on the 1X2 market (spec §5 step 4)."""
import math
from collections import defaultdict

import numpy as np

from . import config
from .db import sb


def elo_lambdas(
    elo_home: float, elo_away: float, host_home: bool, beta: float | None = None
) -> tuple[float, float]:
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
    if beta is None:
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
    btts = float(m[1:, 1:].sum())
    rows = [
        ("1x2", "home", home),
        ("1x2", "draw", draw),
        ("1x2", "away", away),
        ("btts", "yes", btts),
        ("btts", "no", 1.0 - btts),
    ]
    for line, key in config.GOAL_LINES:
        over = float(m[total >= line + 0.5].sum())  # over 2.5 -> total >= 3
        rows.append((key, "over", over))
        rows.append((key, "under", 1.0 - over))
    cs_covered = 0.0
    for i in range(5):
        for j in range(5):
            p = float(m[i, j])
            rows.append(("cs", f"{i}-{j}", p))
            cs_covered += p
    rows.append(("cs", "other", max(0.0, 1.0 - cs_covered)))
    return rows


GRID_OUT = 8  # scoreline grid stored for the frontend: 0..7 goals per side (tail ~0)


def score_grid_rows() -> list[dict]:
    """Per-fixture full-time scoreline grids for scheduled fixtures -> score_grids.

    The frontend prices EXACT same-game goal joints (result × goals-over × BTTS)
    from these — one coherent joint distribution over (home, away) goals, free
    pipeline only. Truncated to GRID_OUT×GRID_OUT and renormalised; cells beyond
    7 goals a side are vanishingly small."""
    from datetime import datetime, timezone

    teams = {t["id"]: t for t in sb().table("teams").select("id, elo").execute().data}
    fixtures = (
        sb().table("fixtures").select("id, home_id, away_id, host_home, status")
        .eq("status", "scheduled").execute().data
    )
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for f in fixtures:
        if not (f["home_id"] and f["away_id"]):
            continue
        m = scoreline_matrix(*elo_lambdas(
            float(teams[f["home_id"]]["elo"]), float(teams[f["away_id"]]["elo"]), bool(f["host_home"])
        ))
        sub = m[:GRID_OUT, :GRID_OUT]
        sub = sub / sub.sum()  # renormalise the truncated grid
        grid = [[round(float(sub[i, j]), 6) for j in range(GRID_OUT)] for i in range(GRID_OUT)]
        rows.append({"fixture_id": f["id"], "grid": grid, "computed_at": now})
    print(f"[match_model] {len(rows)} scoreline grids")
    return rows


def power_devig(implied: dict[str, float]) -> dict[str, float]:
    """De-vig by the power method: q_i = p_i^k with k solved so Σq = 1.

    Proportional normalisation spreads the overround evenly, which puts too
    much of it on favourites and manufactures phantom value on draws and
    longshots — exactly the selections a value tier surfaces. The power method
    allocates more of the vig to the longshots (the empirical
    favourite-longshot bias)."""
    lo, hi = 0.3, 8.0
    for _ in range(60):
        k = (lo + hi) / 2
        s = sum(p**k for p in implied.values())
        if s > 1.0:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    q = {sel: p**k for sel, p in implied.items()}
    total = sum(q.values())
    return {sel: v / total for sel, v in q.items()}


def _latest_devigged(
    fixture_ids: list[int], market: str, selections: set[str]
) -> dict[int, dict[str, tuple[float, float]]]:
    """fixture_id -> selection -> (median_decimal_odds, devigged_probability),
    using only each fixture's most recent fetch batch of the given market.

    Small batches: ~20 books x selections x 5 fixtures per pull keeps every
    in-batch fixture's latest pull comfortably inside the 1000-row window (the
    old 50-fixture/2000-row version could silently drop fixtures)."""
    out: dict[int, dict[str, tuple[float, float]]] = {}
    for i in range(0, len(fixture_ids), 5):
        batch = fixture_ids[i : i + 5]
        snaps = (
            sb()
            .table("odds_snapshots")
            .select("fixture_id, selection, decimal_odds, fetched_at")
            .in_("fixture_id", batch)
            .eq("market", market)
            .order("fetched_at", desc=True)
            .limit(1000)
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
            if set(sels) != selections:
                continue
            med = {sel: float(np.median(odds)) for sel, odds in sels.items()}
            devigged = power_devig({sel: 1.0 / o for sel, o in med.items()})
            out[fid] = {sel: (med[sel], devigged[sel]) for sel in med}
    return out


def _latest_devigged_h2h(fixture_ids: list[int]) -> dict[int, dict[str, tuple[float, float]]]:
    return _latest_devigged(fixture_ids, "h2h", {"home", "draw", "away"})


def run() -> list[dict]:
    """Build match_predictions rows for upcoming fixtures with known teams.

    Scheduled only: once a match kicks off (status 'live') its last pre-match
    row is frozen — recomputing mid-match would leak in-play odds into the
    logged edge and the 'market' closing-odds baseline."""
    teams = {t["id"]: t for t in sb().table("teams").select("id, elo").execute().data}
    fixtures = (
        sb()
        .table("fixtures")
        .select("id, home_id, away_id, host_home, status")
        .eq("status", "scheduled")
        .execute()
        .data
    )
    fixtures = [f for f in fixtures if f["home_id"] and f["away_id"]]
    fids = [f["id"] for f in fixtures]
    book = _latest_devigged_h2h(fids)
    book_ou = _latest_devigged(fids, "ou25", {"over", "under"})  # present iff ODDS_MARKETS includes totals

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
            priced = book.get(f["id"]) if market == "1x2" else (
                book_ou.get(f["id"]) if market == "ou25" else None
            )
            if priced:
                med_odds, devig_p = priced[selection]
                row["market_odds"] = round(med_odds, 3)
                row["edge"] = round(p - devig_p, 4)
            rows.append(row)
    print(f"[match_model] {len(rows)} prediction rows for {len(fixtures)} fixtures")
    return rows


if __name__ == "__main__":
    run()
