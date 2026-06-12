"""Pipeline B ratings: xG-adjusted Elo (spec v4 §5.1).

Standard Elo update where the "result" is blended:
    score_eff = w_result * actual_result + w_xg * xg_result
xg_result converts the teams' match xG into a win expectation by treating the
xG values as Poisson rates: P(win) + 0.5*P(draw). Falls back to the pure
result when xG is missing. Maintains teams.elo_xg (initialised from elo) and
fixtures.elo_xg_applied — fully independent of Pipeline A's elo/elo_applied.
"""
import math

from . import config
from .db import sb

P = config.MODEL_PARAMS


def _expected(dr: float) -> float:
    return 1.0 / (10 ** (-dr / 400.0) + 1.0)


def _g_multiplier(goal_diff: float) -> float:
    if goal_diff <= 1:
        return 1.0
    if goal_diff == 2:
        return 1.5
    return (11.0 + goal_diff) / 8.0


def xg_result(xg_h: float, xg_a: float, grid: int = 11) -> float:
    """P(home win) + 0.5*P(draw) under independent Poisson at the xG rates."""
    xg_h, xg_a = max(xg_h, 0.05), max(xg_a, 0.05)
    ph = [math.exp(-xg_h) * xg_h**k / math.factorial(k) for k in range(grid)]
    pa = [math.exp(-xg_a) * xg_a**k / math.factorial(k) for k in range(grid)]
    win = sum(ph[i] * pa[j] for i in range(grid) for j in range(i))
    draw = sum(ph[i] * pa[i] for i in range(grid))
    total = sum(ph) * sum(pa)
    return (win + 0.5 * draw) / total


def run():
    teams = sb().table("teams").select("id, elo, elo_xg").execute().data
    elo = {t["id"]: float(t["elo_xg"] if t["elo_xg"] is not None else t["elo"]) for t in teams}

    pending = (
        sb()
        .table("fixtures")
        .select("id, home_id, away_id, home_goals, away_goals, host_home")
        .eq("status", "finished")
        .eq("elo_xg_applied", False)
        .order("kickoff")
        .execute()
        .data
    )
    if not pending:
        print("[ratings_xg] no new finished results")
        return

    # xG per (fixture, team) from match_stats FT rows
    fids = [f["id"] for f in pending]
    xg_map: dict[tuple[int, int], float] = {}
    for i in range(0, len(fids), 100):
        for s in (
            sb()
            .table("match_stats")
            .select("fixture_id, team_id, xg")
            .in_("fixture_id", fids[i : i + 100])
            .eq("period", "FT")
            .execute()
            .data
        ):
            if s["xg"] is not None:
                xg_map[(s["fixture_id"], s["team_id"])] = float(s["xg"])

    applied = []
    for f in pending:
        h, a = f["home_id"], f["away_id"]
        hg, ag = f["home_goals"], f["away_goals"]
        if h is None or a is None or hg is None or ag is None:
            continue
        actual = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
        xg_h, xg_a = xg_map.get((f["id"], h)), xg_map.get((f["id"], a))
        if xg_h is not None and xg_a is not None:
            score_eff = P["XG_ELO_W_RESULT"] * actual + P["XG_ELO_W_XG"] * xg_result(xg_h, xg_a)
            gd = abs((xg_h - xg_a) if hg == ag else (hg - ag))
        else:
            score_eff = actual  # fallback: pure results-Elo
            gd = abs(hg - ag)
        dr = elo[h] - elo[a] + (config.ELO_HOME_ADV if f["host_home"] else 0)
        delta = P["XG_ELO_K"] * _g_multiplier(gd) * (score_eff - _expected(dr))
        elo[h] += delta
        elo[a] -= delta
        applied.append(f["id"])

    for t in teams:
        new = round(elo[t["id"]])
        if t["elo_xg"] is None or new != t["elo_xg"]:
            sb().table("teams").update({"elo_xg": new}).eq("id", t["id"]).execute()
    if applied:
        sb().table("fixtures").update({"elo_xg_applied": True}).in_("id", applied).execute()
    print(f"[ratings_xg] applied {len(applied)} results to xG-Elo")


if __name__ == "__main__":
    run()
