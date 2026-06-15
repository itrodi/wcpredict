"""Pipeline B match model (spec v4 §5.2): xG-Elo -> Dixon-Coles Poisson,
first-half markets, and a negative-binomial corners model.

Writes the same FT markets as Pipeline A (1x2/ou25/btts/cs, so the compare view
lines up) plus: ht_1x2, ou05_1h, ou15_1h, htft, corners_o85/o95/o105,
team_corners_home_o45 / team_corners_away_o45. All rows pipeline='statsapi'.
Corners ship "experimental" until model_scores shows >= EXPERIMENTAL_MIN_N.
"""
import math
from collections import defaultdict

import numpy as np

from . import config
from .db import fetch_all, sb
from .match_model import _latest_devigged, _latest_devigged_h2h, elo_lambdas, power_devig

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


def load_corner_model() -> dict:
    """Tournament corner baseline + dispersion + per-team rates for the corners
    model. Three data sources, most-abundant-informs-least:

    - finished-match total corners -> league mean + NB dispersion k
    - per-team corner-for / corner-against rates (team_signals) -> direct signal
    - per-team SHOT volume + the league corners-per-shot ratio -> an *informed
      prior*. Shots accumulate far faster than a stable corner rate, so a team
      that is shooting a lot is expected to win more corners even before its
      own corner sample is reliable. This is what makes the model useful in the
      first week instead of shrinking everything to a flat league average.

    Degrades cleanly: with no shots the prior is the flat league mean; with no
    finished matches the league mean is the formula's implied average."""
    ms = fetch_all(
        lambda: sb().table("match_stats").select("fixture_id, team_id, corners, shots")
        .eq("period", "FT").order("id")
    )
    by_fx: dict[int, list[float]] = defaultdict(list)
    team_shots: dict[int, list[float]] = defaultdict(list)
    tot_corners = tot_shots = 0.0
    for r in ms:
        if r["corners"] is not None:
            by_fx[r["fixture_id"]].append(float(r["corners"]))
            if r["shots"] is not None:
                tot_corners += float(r["corners"])
                tot_shots += float(r["shots"])
        if r["shots"] is not None:
            team_shots[r["team_id"]].append(float(r["shots"]))
    totals = [sum(v) for v in by_fx.values() if len(v) == 2]
    if len(totals) >= 5:
        league_total = float(np.mean(totals))
        var = float(np.var(totals))
        k = league_total ** 2 / (var - league_total) if var > league_total else P["CORNERS_K"]
        k = float(min(max(k, 2.0), 40.0))
    else:
        # implied mean of the formula at an average-tempo, even match
        league_total = max(6.0, P["CORNERS_A"] + P["CORNERS_B"] * config.TOTAL_GOALS)
        k = float(P["CORNERS_K"])
    league_team = league_total / 2.0
    corner_per_shot = (tot_corners / tot_shots) if tot_shots >= 20 else None
    team_shot_avg = {t: float(np.mean(v)) for t, v in team_shots.items() if v}
    sig = {
        (s["team_id"], s["signal"]): float(s["value"])
        for s in sb().table("team_signals").select("team_id, signal, value")
        .in_("signal", ["corner_pace_for", "corner_pace_against", "corner_games"]).execute().data
    }
    return {"league_team": league_team, "k": k, "sig": sig,
            "corner_per_shot": corner_per_shot, "team_shot_avg": team_shot_avg}


def fixture_corner_ctx(model: dict, home_id: int, away_id: int,
                       lam_h: float, lam_a: float) -> dict:
    """Per-team corner means from the attack/defense decomposition, each shrunk
    to a SHOT-informed prior (not a flat league average) and tempo/strength
    adjusted."""
    league, sig = model["league_team"], model["sig"]
    cps, shots_avg = model.get("corner_per_shot"), model.get("team_shot_avg", {})
    k0 = P["CORNER_SHRINK_K0"]

    def prior_for(tid: int) -> float:
        # blend the flat league mean with the team's shots-implied corner rate
        if cps and tid in shots_avg:
            return 0.5 * league + 0.5 * shots_avg[tid] * cps
        return league

    def rate(tid: int, kind: str) -> float:
        prior = prior_for(tid) if kind == "for" else league
        v = sig.get((tid, f"corner_pace_{kind}"))
        if v is None:
            return prior
        n = sig.get((tid, "corner_games"), 0.0)
        return (n * v + k0 * prior) / (n + k0)   # empirical-Bayes shrink to the informed prior

    w = P["CORNER_ATTACK_W"]
    mu_h = w * rate(home_id, "for") + (1 - w) * rate(away_id, "against")
    mu_a = w * rate(away_id, "for") + (1 - w) * rate(home_id, "against")
    tempo = min(max((lam_h + lam_a) / config.TOTAL_GOALS, 0.85), 1.2)
    share_h = lam_h / (lam_h + lam_a)
    tilt = 1.0 + P["CORNER_TILT"] * (2 * share_h - 1)   # favourite forces a few more
    return {"mu_h": max(0.5, mu_h * tempo * tilt),
            "mu_a": max(0.5, mu_a * tempo * (2 - tilt)),
            "k": model["k"]}


def corners_markets(lam_h: float, lam_a: float, elo_diff: float,
                    corner_ctx: dict | None = None) -> list[tuple[str, str, float]]:
    if corner_ctx is not None:
        mu_h, mu_a, k = corner_ctx["mu_h"], corner_ctx["mu_a"], corner_ctx["k"]
        mu_total = max(2.0, mu_h + mu_a)
    else:
        # fallback: single total split by attacking (lambda) share
        mu_total = max(2.0, P["CORNERS_A"] + P["CORNERS_B"] * (lam_h + lam_a) + P["CORNERS_C"] * abs(elo_diff))
        k = P["CORNERS_K"]
        share_h = lam_h / (lam_h + lam_a)
        mu_h, mu_a = mu_total * share_h, mu_total * (1 - share_h)
    pmf = nb_pmf_vector(mu_total, k)
    rows = []
    for line, name in ((8.5, "corners_o85"), (9.5, "corners_o95"), (10.5, "corners_o105")):
        over = float(pmf[int(line) + 1 :].sum())
        rows.append((name, "over", over))
        rows.append((name, "under", 1.0 - over))
    for mu_team, name in ((mu_h, "team_corners_home_o45"), (mu_a, "team_corners_away_o45")):
        pmf_t = nb_pmf_vector(max(0.5, mu_team), k)
        over = float(pmf_t[5:].sum())
        rows.append((name, "over", over))
        rows.append((name, "under", 1.0 - over))
    # 1st-half corners (v4.1 §5.6): ships only once calibrate.py produces a
    # satisfactory share and it is set via the CORNERS_1H_SHARE env override
    fh_share = P["CORNERS_1H_SHARE"]
    if fh_share > 0:
        pmf_1h = nb_pmf_vector(max(0.5, mu_total * fh_share), k)
        over = float(pmf_1h[5:].sum())
        rows.append(("corners_1h_o45", "over", over))
        rows.append(("corners_1h_o45", "under", 1.0 - over))
    return rows


def markets_for_fixture(elo_h: float, elo_a: float, host_home: bool,
                        corner_ctx: dict | None = None) -> list[tuple[str, str, float]]:
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

    rows += corners_markets(lam_h, lam_a, elo_h - elo_a, corner_ctx)
    return rows


def _latest_corners_odds(fixture_ids: list[int]) -> dict:
    """(fixture_id, market) -> {selection: (median_odds, devigged_p)} from
    TheStatsAPI corners prices, when the plan turned out to include odds
    (v4.1 §5.0). Empty dict when no such snapshots exist."""
    from collections import defaultdict
    out: dict = {}
    for i in range(0, len(fixture_ids), 5):
        snaps = (
            sb().table("odds_snapshots")
            .select("fixture_id, market, selection, decimal_odds, fetched_at")
            .in_("fixture_id", fixture_ids[i : i + 5])
            .eq("source", "statsapi").like("market", "corners%")
            .order("fetched_at", desc=True).limit(1000)
            .execute().data
        )
        latest: dict = {}
        for s in snaps:
            latest.setdefault((s["fixture_id"], s["market"]), s["fetched_at"])
        by_sel: dict = defaultdict(lambda: defaultdict(list))
        for s in snaps:
            key = (s["fixture_id"], s["market"])
            if s["fetched_at"] == latest[key]:
                by_sel[key][s["selection"]].append(float(s["decimal_odds"]))
        for key, sels in by_sel.items():
            if set(sels) != {"over", "under"}:
                continue
            med = {sel: float(np.median(v)) for sel, v in sels.items()}
            devigged = power_devig({sel: 1.0 / o for sel, o in med.items()})
            out[key] = {sel: (med[sel], devigged[sel]) for sel in med}
    return out


def run() -> list[dict]:
    teams = {t["id"]: t for t in sb().table("teams").select("id, elo, elo_xg").execute().data}
    fixtures = (
        sb()
        .table("fixtures")
        .select("id, home_id, away_id, host_home, status")
        .eq("status", "scheduled")  # freeze at kickoff — see match_model.run
        .execute()
        .data
    )
    fixtures = [f for f in fixtures if f["home_id"] and f["away_id"]]
    fids = [f["id"] for f in fixtures]
    book = _latest_devigged_h2h(fids)
    book_ou = _latest_devigged(fids, "ou25", {"over", "under"})
    corners_book = _latest_corners_odds(fids)
    corner_model = load_corner_model()

    rows = []
    for f in fixtures:
        th, ta = teams[f["home_id"]], teams[f["away_id"]]
        elo_h = float(th["elo_xg"] if th["elo_xg"] is not None else th["elo"])
        elo_a = float(ta["elo_xg"] if ta["elo_xg"] is not None else ta["elo"])
        lam_h, lam_a = elo_lambdas(elo_h, elo_a, bool(f["host_home"]))
        cctx = fixture_corner_ctx(corner_model, f["home_id"], f["away_id"], lam_h, lam_a)
        for market, selection, p in markets_for_fixture(elo_h, elo_a, bool(f["host_home"]), cctx):
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
            elif market == "ou25" and f["id"] in book_ou:
                med_odds, devig_p = book_ou[f["id"]][selection]
                row["market_odds"] = round(med_odds, 3)
                row["edge"] = round(p - devig_p, 4)
            elif (f["id"], market) in corners_book and selection in corners_book[(f["id"], market)]:
                med_odds, devig_p = corners_book[(f["id"], market)][selection]
                row["market_odds"] = round(med_odds, 3)
                row["edge"] = round(p - devig_p, 4)
            rows.append(row)
    print(f"[match_model_b] {len(rows)} prediction rows for {len(fixtures)} fixtures")
    return rows


if __name__ == "__main__":
    run()
