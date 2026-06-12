"""Model-math regression tests — run with: python -m engine.tests

Codifies the invariants every model component must hold, including the v4.1
totals bug ("over 2.5 identical for every fixture"): the v1 Elo->lambda mapping
split a FIXED expected total, so every totals-based market was constant across
matchups. These tests fail loudly if that ever regresses.

No network, no database: a stub replaces supabase when it isn't installed, and
the simulation test injects a fake client.
"""
import sys
import types

try:  # the engine imports supabase at module load; tests never need a real client.
    # NB: the repo's supabase/ migrations dir is a namespace package, so check
    # for the symbol, not the module name.
    from supabase import create_client  # noqa: F401
except ImportError:
    fake = types.ModuleType("supabase")
    fake.create_client = lambda *a, **k: None
    sys.modules["supabase"] = fake

import numpy as np

from . import config
from .blend import run as blend_run
from .match_model import elo_lambdas, markets_from_matrix, scoreline_matrix
from .match_model_b import _1x2, dc_scoreline_matrix, markets_for_fixture, nb_pmf_vector
from .ratings import _expected, _g_multiplier
from .ratings_xg import xg_result
from .write_db import settle_outcome

PASS = 0


def check(name, cond, detail=""):
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}" + (f"  ({detail})" if detail else ""))


def probs(rows):
    by = {}
    for mk, sel, p in rows:
        by.setdefault(mk, {})[sel] = p
    return by


def main():
    print("== Elo -> lambda mapping (v2) ==")
    lam_even = elo_lambdas(1800, 1800, False)
    check("even matchup splits evenly", abs(lam_even[0] - lam_even[1]) < 1e-12)
    check("even matchup total = TOTAL_GOALS",
          abs(sum(lam_even) - config.TOTAL_GOALS) < 1e-9, f"total={sum(lam_even):.3f}")
    lam_mid = elo_lambdas(1950, 1750, False)
    lam_big = elo_lambdas(2150, 1550, False)
    check("favourite rate grows with Elo gap", lam_big[0] > lam_mid[0] > lam_even[0])
    check("underdog rate shrinks with Elo gap", lam_big[1] < lam_mid[1] < lam_even[1])
    check("mismatch raises expected total (the totals-bug fix)",
          sum(lam_big) > sum(lam_mid) > sum(lam_even),
          f"{sum(lam_even):.2f} -> {sum(lam_mid):.2f} -> {sum(lam_big):.2f}")
    h0, _ = elo_lambdas(1800, 1800, True)
    check("host home advantage lifts home rate", h0 > lam_even[0])
    swapped = elo_lambdas(1750, 1950, False)
    check("symmetry: swapping teams swaps rates",
          abs(lam_mid[0] - swapped[1]) < 1e-12 and abs(lam_mid[1] - swapped[0]) < 1e-12)

    print("== Pipeline A markets ==")
    by_even = probs(markets_from_matrix(scoreline_matrix(*lam_even)))
    by_mid = probs(markets_from_matrix(scoreline_matrix(*lam_mid)))
    by_big = probs(markets_from_matrix(scoreline_matrix(*lam_big)))
    for name, by in (("even", by_even), ("mid", by_mid), ("big", by_big)):
        check(f"1x2 sums to 1 ({name})", abs(sum(by["1x2"].values()) - 1) < 1e-9)
        check(f"cs sums to 1 ({name})", abs(sum(by["cs"].values()) - 1) < 1e-6)
        check(f"ou25 complement ({name})", abs(by["ou25"]["over"] + by["ou25"]["under"] - 1) < 1e-9)
        check(f"btts complement ({name})", abs(by["btts"]["yes"] + by["btts"]["no"] - 1) < 1e-9)
    # THE reported bug: over 2.5 must differ across matchups
    spread = abs(by_big["ou25"]["over"] - by_even["ou25"]["over"])
    check("over 2.5 VARIES across matchups", spread > 0.03,
          f"even={by_even['ou25']['over']:.3f} big={by_big['ou25']['over']:.3f}")
    check("bigger mismatch -> more goals -> higher over 2.5",
          by_big["ou25"]["over"] > by_mid["ou25"]["over"] > by_even["ou25"]["over"])
    check("favourite's win prob rises with gap",
          by_big["1x2"]["home"] > by_mid["1x2"]["home"] > by_even["1x2"]["home"])
    check("draw prob falls with gap",
          by_big["1x2"]["draw"] < by_mid["1x2"]["draw"] < by_even["1x2"]["draw"])
    check("even matchup is home/away symmetric",
          abs(by_even["1x2"]["home"] - by_even["1x2"]["away"]) < 1e-9)
    # 1X2 should still track the Elo win expectancy: home + half-draw ~ w
    for dr, by in ((200, by_mid), (600, by_big)):
        w = 1 / (1 + 10 ** (-dr / 400))
        got = by["1x2"]["home"] + by["1x2"]["draw"] / 2
        check(f"Poisson 1x2 tracks Elo expectancy (dr={dr})", abs(got - w) < 0.06,
              f"poisson={got:.3f} elo={w:.3f}")

    print("== Pipeline B (Dixon-Coles, 1H, corners) ==")
    m_p = dc_scoreline_matrix(1.3, 1.3, 0.0)
    m_dc = dc_scoreline_matrix(1.3, 1.3, config.MODEL_PARAMS["DC_RHO"])
    check("DC matrix sums to 1", abs(m_dc.sum() - 1) < 1e-9)
    check("negative rho lifts the draw", _1x2(m_dc)[1] > _1x2(m_p)[1])
    b_even = probs(markets_for_fixture(1800, 1800, False))
    b_big = probs(markets_for_fixture(2150, 1550, False))
    for mk in ("1x2", "ht_1x2"):
        check(f"B {mk} sums to 1", abs(sum(b_big[mk].values()) - 1) < 1e-6)
    for mk in ("ou25", "ou05_1h", "ou15_1h", "btts", "corners_o85", "corners_o95",
               "corners_o105", "team_corners_home_o45", "team_corners_away_o45"):
        check(f"B {mk} complement", abs(sum(b_big[mk].values()) - 1) < 1e-6)
    check("B htft approximately sums to 1", abs(sum(b_big["htft"].values()) - 1) < 0.02)
    check("B 1H totals vary across matchups",
          abs(b_big["ou05_1h"]["over"] - b_even["ou05_1h"]["over"]) > 0.02)
    check("B corners mean varies across matchups",
          abs(b_big["corners_o95"]["over"] - b_even["corners_o95"]["over"]) > 0.02)
    check("corners monotone in the line",
          b_big["corners_o85"]["over"] > b_big["corners_o95"]["over"] > b_big["corners_o105"]["over"])
    check("1H has fewer goals than FT", b_big["ou05_1h"]["over"] < 1 - 1e-9
          and b_big["ht_1x2"]["draw"] > b_big["1x2"]["draw"])
    pmf = nb_pmf_vector(9.7, 9.0)
    check("NB pmf sums to 1", abs(pmf.sum() - 1) < 1e-9)
    check("NB pmf mean matches mu", abs(float((np.arange(len(pmf)) * pmf).sum()) - 9.7) < 0.3)

    print("== Ratings ==")
    check("Elo expectancy at 0 is 0.5", abs(_expected(0) - 0.5) < 1e-12)
    check("Elo expectancy monotone", _expected(200) > _expected(100) > _expected(0))
    check("Elo expectancies sum to 1", abs(_expected(150) + _expected(-150) - 1) < 1e-12)
    check("goal-diff multiplier ladder", _g_multiplier(1) == 1.0 and _g_multiplier(2) == 1.5
          and _g_multiplier(3) == 1.75 and _g_multiplier(5) > _g_multiplier(3))
    check("xg_result symmetric at equality", abs(xg_result(1.2, 1.2) - 0.5) < 1e-9)
    check("xg_result rewards xG dominance", xg_result(2.5, 0.4) > 0.85 > xg_result(0.4, 2.5) + 0.7)

    print("== Blend ==")
    out = blend_run([
        {"pipeline": "free", "fixture_id": 1, "market": "1x2", "selection": "home",
         "probability": 0.50, "market_odds": 2.10, "edge": 0.04, "model_version": "x"},
    ])
    w = config.MODEL_PARAMS["BLEND_W_MARKET"]
    expected = w * 0.46 + (1 - w) * 0.50
    check("blend arithmetic", abs(out[0]["probability"] - round(expected, 4)) < 1e-9,
          f"p={out[0]['probability']}")
    check("blend edge vs market", abs(out[0]["edge"] - round(expected - 0.46, 4)) < 1e-9)

    print("== Settlement ==")
    check("1x2 settle", settle_outcome("1x2", "home", 2, 1) is True
          and settle_outcome("1x2", "draw", 1, 1) is True)
    check("ou25 settle", settle_outcome("ou25", "over", 2, 1) is True
          and settle_outcome("ou25", "under", 1, 1) is True)
    check("corners settle with stats", settle_outcome("corners_o95", "over", 1, 0, 6, 5) is True
          and settle_outcome("team_corners_home_o45", "over", 1, 0, 5, 2) is True)
    check("unsettleable markets return None", settle_outcome("corners_o95", "over", 1, 0) is None
          and settle_outcome("ht_1x2", "home", 2, 1) is None)

    print("== Simulation invariants ==")
    from . import simulate
    teams = [{"id": gi * 4 + k + 1, "elo": 1950 - gi * 10 - k * 80, "elo_xg": None,
              "group_code": chr(ord("A") + gi)} for gi in range(12) for k in range(4)]

    class Q:
        def __init__(self, data): self.d = data
        def select(self, *a, **k): return self
        def neq(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def execute(self):
            r = types.SimpleNamespace(); r.data = self.d; return r

    class SB:
        def table(self, name): return Q(teams if name == "teams" else [])

    simulate.sb = lambda: SB()
    rows = simulate.run(n_sims=8000, pipeline="free")
    check("48 teams simulated", len(rows) == 48)
    check("champion probs sum to 1", abs(sum(r["champion"] for r in rows) - 1) < 0.01)
    check("32 teams advance on average", abs(sum(r["advance_grp"] for r in rows) - 32) < 0.1)
    check("2 finalists on average", abs(sum(r["reach_final"] for r in rows) - 2) < 0.05)
    strongest = next(r for r in rows if r["team_id"] == 1)
    weakest = next(r for r in rows if r["team_id"] == 48)
    check("strength ordering respected", strongest["champion"] > weakest["champion"]
          and strongest["advance_grp"] > 0.85)

    print(f"\nALL {PASS} MODEL-MATH CHECKS PASSED")


if __name__ == "__main__":
    main()
