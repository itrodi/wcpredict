"""Pipeline B match model (spec v4 §5.2): xG-Elo -> Dixon-Coles Poisson,
first-half markets, and a negative-binomial corners model.

Writes the same FT markets as Pipeline A (1x2/ou25/btts/cs, so the compare view
lines up) plus: ht_1x2, ou05_1h, ou15_1h, htft, corners_o85/o95/o105,
team_corners_home_o45 / team_corners_away_o45. All rows pipeline='statsapi'.
Corners ship "experimental" until model_scores shows >= EXPERIMENTAL_MIN_N.
"""
import math

import numpy as np

from . import config
from .db import sb
from .match_model import _latest_devigged_h2h, elo_lambdas

P = config.MODEL_PARAMS


def dc_scoreline_matrix(lam_h: float, lam_a: float, rho: float) -> np.ndarray:
    """Independent Poisson grid with the Dixon-Coles tau correction on {0,1}x{0,1}."""
    k = np.arange(config.GOAL_GRID)
    fact = np.array([math.factorial(int(i)) for i in k])
    ph = np.exp(-lam_h) * lam_h**k / fact
    pa = np.exp(-lam_a) * lam_a**k / fact
    m = np.outer(ph, pa)
    m[0, 0] *= 1 - lam_h * lam_a * rho
    m[0, 1] *= 1 + lam_h * rho
    m[1, 0] *= 1 + lam_a * rho
    m[1, 1] *= 1 - rho
    np.clip(m, 0, None, out=m)
    return m / m.sum()


def _1x2(m: np.ndarray) -> tuple[float, float, float]:
    return float(np.tril(m, -1).sum()), float(np.trace(m)), float(np.triu(m, 1).sum())


def nb_pmf_vector(mu: float, k: float, n_max: int = 30) -> np.ndarray:
    """NB2 pmf for 0..n_max: var = mu + mu^2/k."""
    p = k / (k + mu)
    out = np.array(
        [
            math.exp(
                math.lgamma(x + k) - math.lgamma(k) - math.lgamma(x + 1)
                + k * math.log(p) + x * math.log(1 - p)
            )
            for x in range(n_max + 1)
        ]
    )
    return out / out.sum()


def corners_markets(lam_h: float, lam_a: float, elo_diff: float) -> list[tuple[str, str, float]]:
    mu_total = max(2.0, P["CORNERS_A"] + P["CORNERS_B"] * (lam_h + lam_a) + P["CORNERS_C"] * abs(elo_diff))
    k = P["CORNERS_K"]
    pmf = nb_pmf_vector(mu_total, k)
    rows = []
    for line, name in ((8.5, "corners_o85"), (9.5, "corners_o95"), (10.5, "corners_o105")):
        over = float(pmf[int(line) + 1 :].sum())
        rows.append((name, "over", over))
        rows.append((name, "under", 1.0 - over))
    # team corners: split mu by attacking share (lambda share), same dispersion
    share_h = lam_h / (lam_h + lam_a)
    for mu_team, name in ((mu_total * share_h, "team_corners_home_o45"),
                          (mu_total * (1 - share_h), "team_corners_away_o45")):
        pmf_t = nb_pmf_vector(max(0.5, mu_team), k)
        over = float(pmf_t[5:].sum())
        rows.append((name, "over", over))
        rows.append((name, "under", 1.0 - over))
    return rows


def markets_for_fixture(elo_h: float, elo_a: float, host_home: bool) -> list[tuple[str, str, float]]:
    lam_h, lam_a = elo_lambdas(elo_h, elo_a, host_home)
    rho = P["DC_RHO"]
    m = dc_scoreline_matrix(lam_h, lam_a, rho)
    home, draw, away = _1x2(m)
    total = np.add.outer(np.arange(config.GOAL_GRID), np.arange(config.GOAL_GRID))
    over25 = float(m[total >= 3].sum())
    btts = float(m[1:, 1:].sum())

    rows = [
        ("1x2", "home", home), ("1x2", "draw", draw), ("1x2", "away", away),
        ("ou25", "over", over25), ("ou25", "under", 1 - over25),
        ("btts", "yes", btts), ("btts", "no", 1 - btts),
    ]
    cs_covered = 0.0
    for i in range(5):
        for j in range(5):
            p = float(m[i, j])
            rows.append(("cs", f"{i}-{j}", p))
            cs_covered += p
    rows.append(("cs", "other", max(0.0, 1.0 - cs_covered)))

    # ---- first half: lambda_1H = share * lambda_FT ----
    share = P["FH_GOAL_SHARE"]
    m1 = dc_scoreline_matrix(lam_h * share, lam_a * share, rho)
    ht_home, ht_draw, ht_away = _1x2(m1)
    rows += [
        ("ht_1x2", "home", ht_home), ("ht_1x2", "draw", ht_draw), ("ht_1x2", "away", ht_away),
        ("ou05_1h", "over", 1.0 - float(m1[0, 0])), ("ou05_1h", "under", float(m1[0, 0])),
    ]
    over15_1h = float(m1[total >= 2].sum())
    rows += [("ou15_1h", "over", over15_1h), ("ou15_1h", "under", 1 - over15_1h)]

    # HT/FT: 2H as an independent DC matrix at the remaining goal share
    m2 = dc_scoreline_matrix(lam_h * (1 - share), lam_a * (1 - share), rho)
    ht_p = {"home": ht_home, "draw": ht_draw, "away": ht_away}
    # P(FT outcome | HT outcome) approximated by convolving 2H goals over a
    # representative HT scoreline of each HT outcome (v1 simplification)
    rep = {"home": (1, 0), "draw": (0, 0), "away": (0, 1)}
    for ht_sel, (h0, a0) in rep.items():
        g = np.arange(config.GOAL_GRID)
        ft_h = h0 + np.add.outer(g, np.zeros(config.GOAL_GRID, dtype=int))
        ft_a = a0 + np.add.outer(np.zeros(config.GOAL_GRID, dtype=int), g)
        for ft_sel, mask in (
            ("home", ft_h > ft_a), ("draw", ft_h == ft_a), ("away", ft_h < ft_a),
        ):
            rows.append(("htft", f"{ht_sel}_{ft_sel}", ht_p[ht_sel] * float(m2[mask].sum())))

    rows += corners_markets(lam_h, lam_a, elo_h - elo_a)
    return rows


def run() -> list[dict]:
    teams = {t["id"]: t for t in sb().table("teams").select("id, elo, elo_xg").execute().data}
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
        th, ta = teams[f["home_id"]], teams[f["away_id"]]
        elo_h = float(th["elo_xg"] if th["elo_xg"] is not None else th["elo"])
        elo_a = float(ta["elo_xg"] if ta["elo_xg"] is not None else ta["elo"])
        for market, selection, p in markets_for_fixture(elo_h, elo_a, bool(f["host_home"])):
            p = min(max(p, 0.0), 1.0)
            row = {
                "pipeline": config.PIPELINE_STATSAPI,
                "fixture_id": f["id"],
                "market": market,
                "selection": selection,
                "probability": round(p, 4),
                "fair_odds": round(1.0 / p, 3) if p > 1e-4 else None,
                "market_odds": None,
                "edge": None,
                "model_version": config.MODEL_VERSION_B,
            }
            if market == "1x2" and f["id"] in book:
                med_odds, devig_p = book[f["id"]][selection]
                row["market_odds"] = round(med_odds, 3)
                row["edge"] = round(p - devig_p, 4)
            rows.append(row)
    print(f"[match_model_b] {len(rows)} prediction rows for {len(fixtures)} fixtures")
    return rows


if __name__ == "__main__":
    run()
