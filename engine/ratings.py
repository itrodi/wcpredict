"""Elo refresh from finished results (results-driven only in v1, spec §5 step 3).

World Football Elo conventions: K=50, goal-difference multiplier, +100 home
advantage for host nations. Each finished fixture is applied exactly once
(fixtures.elo_applied flag).
"""
from . import config
from .db import sb


def _expected(dr: float) -> float:
    return 1.0 / (10 ** (-dr / 400.0) + 1.0)


def _g_multiplier(goal_diff: int) -> float:
    if goal_diff <= 1:
        return 1.0
    if goal_diff == 2:
        return 1.5
    return (11.0 + goal_diff) / 8.0


def run():
    teams = sb().table("teams").select("id, elo").execute().data
    elo = {t["id"]: float(t["elo"]) for t in teams}

    pending = (
        sb()
        .table("fixtures")
        .select("id, home_id, away_id, home_goals, away_goals, host_home")
        .eq("status", "finished")
        .eq("elo_applied", False)
        .order("kickoff")
        .execute()
        .data
    )
    applied = []
    for f in pending:
        h, a = f["home_id"], f["away_id"]
        hg, ag = f["home_goals"], f["away_goals"]
        if h is None or a is None or hg is None or ag is None:
            continue
        dr = elo[h] - elo[a] + (config.ELO_HOME_ADV if f["host_home"] else 0)
        we = _expected(dr)
        w = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
        delta = config.ELO_K * _g_multiplier(abs(hg - ag)) * (w - we)
        elo[h] += delta
        elo[a] -= delta
        applied.append(f["id"])

    if not applied:
        print("[ratings] no new finished results")
        return

    original = {t["id"]: t["elo"] for t in teams}
    for team_id, rating in elo.items():
        if round(rating) != original[team_id]:
            sb().table("teams").update({"elo": round(rating)}).eq("id", team_id).execute()
    sb().table("fixtures").update({"elo_applied": True}).in_("id", applied).execute()
    print(f"[ratings] applied {len(applied)} results to Elo")


if __name__ == "__main__":
    run()
