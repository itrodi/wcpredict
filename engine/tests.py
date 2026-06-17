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
from .match_model import elo_lambdas, markets_from_matrix, power_devig, scoreline_matrix
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
    # full goal ladder (v4.6): monotone decreasing over-probability with the line
    for name, by in (("even", by_even), ("big", by_big)):
        ladder = [by[k]["over"] for _, k in config.GOAL_LINES]
        check(f"goal ladder monotone decreasing ({name})",
              all(ladder[i] >= ladder[i + 1] for i in range(len(ladder) - 1)),
              " > ".join(f"{x:.2f}" for x in ladder))
        for _, k in config.GOAL_LINES:
            check(f"{k} complement ({name})", abs(by[k]["over"] + by[k]["under"] - 1) < 1e-9)
    check("high-scoring mismatch lifts over 3.5", by_big["ou35"]["over"] > by_even["ou35"]["over"])
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

    print("== Corners team model (attack/defense + shrinkage) ==")
    from .match_model_b import corners_markets, fixture_corner_ctx
    lam_h0, lam_a0 = elo_lambdas(1800, 1800, False)
    high = {"league_team": 5.0, "k": 9.0, "sig": {
        (1, "corner_pace_for"): 7.5, (1, "corner_pace_against"): 4.0, (1, "corner_games"): 6,
        (2, "corner_pace_for"): 6.5, (2, "corner_pace_against"): 4.5, (2, "corner_games"): 6,
    }}
    ctx = fixture_corner_ctx(high, 1, 2, lam_h0, lam_a0)
    by_ctx = probs(corners_markets(lam_h0, lam_a0, 0.0, ctx))
    by_def = probs(corners_markets(lam_h0, lam_a0, 0.0))
    check("corner ctx complements hold",
          abs(by_ctx["corners_o95"]["over"] + by_ctx["corners_o95"]["under"] - 1) < 1e-9)
    check("high-pace teams lift corners overs vs the formula",
          by_ctx["corners_o95"]["over"] > by_def["corners_o95"]["over"],
          f"ctx={by_ctx['corners_o95']['over']:.3f} def={by_def['corners_o95']['over']:.3f}")
    # with no signals the per-team means shrink to the league prior (~5 each)
    ctx0 = fixture_corner_ctx({"league_team": 5.0, "k": 9.0, "sig": {}}, 1, 2, lam_h0, lam_a0)
    check("no-data corners shrink to league prior",
          abs(ctx0["mu_h"] - 5.0) < 0.6 and abs(ctx0["mu_a"] - 5.0) < 0.6,
          f"mu_h={ctx0['mu_h']:.2f} mu_a={ctx0['mu_a']:.2f}")
    # a low-corner side drags the line down
    low = {"league_team": 5.0, "k": 9.0, "sig": {
        (1, "corner_pace_for"): 2.5, (1, "corner_pace_against"): 3.0, (1, "corner_games"): 6,
        (2, "corner_pace_for"): 2.8, (2, "corner_pace_against"): 3.2, (2, "corner_games"): 6,
    }}
    by_low = probs(corners_markets(lam_h0, lam_a0, 0.0, fixture_corner_ctx(low, 1, 2, lam_h0, lam_a0)))
    check("low-pace teams suppress corners overs",
          by_low["corners_o95"]["over"] < by_def["corners_o95"]["over"])
    # shot-informed prior: with NO corner signals, a heavy-shooting team still
    # gets a higher corner line than a low-shooting one (shots inform the prior)
    shot_model = {"league_team": 5.0, "k": 9.0, "sig": {},
                  "corner_per_shot": 0.4, "team_shot_avg": {1: 16.0, 2: 16.0}}
    quiet_model = {"league_team": 5.0, "k": 9.0, "sig": {},
                   "corner_per_shot": 0.4, "team_shot_avg": {1: 8.0, 2: 8.0}}
    ctx_shooty = fixture_corner_ctx(shot_model, 1, 2, lam_h0, lam_a0)
    ctx_quiet = fixture_corner_ctx(quiet_model, 1, 2, lam_h0, lam_a0)
    check("shot volume informs the corner prior (no corner data)",
          ctx_shooty["mu_h"] > ctx_quiet["mu_h"],
          f"shooty_mu={ctx_shooty['mu_h']:.2f} quiet_mu={ctx_quiet['mu_h']:.2f}")

    print("== Picks candidate classification ==")
    from .picks import _candidate_tiers
    cal = {"1x2", "ou25", "btts"}

    def mkrow(**kw):
        return {"fixture_id": 1, "edge": None, "market_odds": None, **kw}

    c = _candidate_tiers(mkrow(market="corners_o85", selection="over", probability=0.70), cal)
    check("experimental corners over -> model-only banker",
          len(c) == 1 and c[0]["tier"] == "banker" and c[0]["model_only"] and c[0]["category"] == "overs")
    c = _candidate_tiers(mkrow(market="corners_o85", selection="under", probability=0.70), cal)
    check("corners under is not a model banker (overs side only)", c == [])
    c = _candidate_tiers(mkrow(market="ou25", selection="over", probability=0.66, edge=0.06, market_odds=1.9), cal)
    check("priced ou25 over -> both banker and value candidates",
          sorted(x["tier"] for x in c) == ["banker", "value"])
    c = _candidate_tiers(mkrow(market="corners_o105", selection="over", probability=0.50), cal)
    check("low-confidence corners over -> nothing", c == [])
    c = _candidate_tiers(mkrow(market="ou05_1h", selection="over", probability=0.95), cal)
    check("trivially short model banker filtered by odds floor", c == [])
    c = _candidate_tiers(mkrow(market="1x2", selection="home", probability=0.70, edge=0.02, market_odds=1.5), cal)
    check("strong 1x2 from blend -> result banker",
          any(x["tier"] == "banker" and x["category"] == "result" for x in c))
    c = _candidate_tiers(mkrow(market="btts", selection="yes", probability=0.64), cal)
    check("btts yes with no book -> model banker (overs)",
          len(c) == 1 and c[0]["tier"] == "banker" and c[0]["category"] == "overs")

    print("== StatsAPI stats payload parsing (documented shape) ==")
    from .ingest_statsapi import _stat_rows
    sample = {
        "match_id": "mt_1",
        "overview": {"possession": {"all": {"home": 54, "away": 46}},
                     "fouls": {"all": {"home": 11, "away": 13}}},
        "shots": {"total": {"all": {"home": 14, "away": 9}},
                  "on_target": {"all": {"home": 6, "away": 4}}},
        "attack": {"corners": {"all": {"home": 7, "away": 3}}},
        "passes": {"total": {"all": {"home": 512, "away": 438}}},
        "np_expected_goals": {"all": {"home": 1.82, "away": 0.94},
                              "first_half": {"home": 0.76, "away": 0.41},
                              "second_half": {"home": 1.06, "away": 0.53}},
    }
    srows = {(r["team_id"], r["period"]): r for r in _stat_rows(99, sample, 10, 20)}
    check("FT corners parsed home/away", srows[(10, "FT")]["corners"] == 7 and srows[(20, "FT")]["corners"] == 3)
    check("FT shots parsed", srows[(10, "FT")]["shots"] == 14 and srows[(20, "FT")]["shots"] == 9)
    check("npxG used as the xg signal", abs(srows[(10, "FT")]["xg"] - 1.82) < 1e-9
          and abs(srows[(10, "FT")]["npxg"] - 1.82) < 1e-9)
    check("possession + fouls parsed", srows[(10, "FT")]["possession"] == 54 and srows[(10, "FT")]["fouls"] == 11)
    check("is_home flag correct", srows[(10, "FT")]["is_home"] is True and srows[(20, "FT")]["is_home"] is False)
    check("half-period kept when xg present", abs(srows[(10, "1H")]["xg"] - 0.76) < 1e-9)
    check("absent half stat is None", srows[(10, "1H")]["corners"] is None)
    check("no mapped team -> no rows", _stat_rows(99, sample, None, None) == [])

    print("== StatsAPI player-stats parsing (documented shape) ==")
    from .ingest_statsapi_extra import _player_row
    psample = {
        "player_id": "pl_6241", "player_name": "Mohamed Salah", "team_id": "tm_8923",
        "position": "F", "rating": 8.2, "minutes_played": 90, "started": True, "played": True,
        "passing": {"total": 42, "accurate": 38, "key_passes": 3},
        "shooting": {"total": 5, "on_target": 3, "goals": 1},
        "duels": {"total": 12, "won": 8},
        "defending": {"tackles": 2, "interceptions": 1},
        "goalkeeping": None,
        "general": {"dribbles_attempted": 6, "dribbles_succeeded": 4,
                    "fouls_drawn": 3, "fouls_committed": 1, "yellow_cards": 0, "red_cards": 0},
    }
    pr = _player_row(psample, 99, 10, "2026-06-17T00:00:00Z")
    check("player identity parsed", pr["statsapi_id"] == "pl_6241" and pr["name"] == "Mohamed Salah")
    check("scoring/shots parsed", pr["goals"] == 1 and pr["shots"] == 5 and pr["shots_on_target"] == 3)
    check("creation + duels parsed", pr["key_passes"] == 3 and pr["duels_won"] == 8)
    check("dribbles + fouls drawn parsed", pr["dribbles"] == 4 and pr["fouls_drawn"] == 3)
    check("discipline parsed", pr["fouls_committed"] == 1 and pr["yellows"] == 0 and pr["reds"] == 0)
    check("minutes + rating parsed", pr["minutes"] == 90 and abs(pr["rating"] - 8.2) < 1e-9)
    missing = _player_row({"player_id": "pl_x"}, 99, 10, "2026-06-17T00:00:00Z")
    check("absent blocks -> None metrics", missing["goals"] is None and missing["key_passes"] is None)

    print("== Odds parsing + Value-mode edge attachment ==")
    from .ingest_odds import _market_snapshots
    ev = {"home_team": "Brazil", "away_team": "Serbia"}
    h2h = _market_snapshots(
        {"key": "h2h", "_bm": "pin", "outcomes": [
            {"name": "Brazil", "price": 1.5}, {"name": "Serbia", "price": 7.0}, {"name": "Draw", "price": 4.2}]},
        ev, 1, "now")
    check("h2h maps home/away/draw", {r["selection"] for r in h2h} == {"home", "away", "draw"})
    check("h2h home price kept", next(r for r in h2h if r["selection"] == "home")["decimal_odds"] == 1.5)
    totals = _market_snapshots(
        {"key": "totals", "_bm": "pin", "outcomes": [
            {"name": "Over", "point": 2.5, "price": 1.9}, {"name": "Under", "point": 2.5, "price": 1.95},
            {"name": "Over", "point": 3.5, "price": 3.1}]},
        ev, 1, "now")
    check("totals maps every line present", {r["market"] for r in totals} == {"ou25", "ou35"})
    check("totals carries over/under", {r["selection"] for r in totals if r["market"] == "ou25"} == {"over", "under"})
    btts = _market_snapshots(
        {"key": "btts", "_bm": "pin", "outcomes": [{"name": "Yes", "price": 1.8}, {"name": "No", "price": 2.0}]},
        ev, 1, "now")
    check("btts maps yes/no", {(r["market"], r["selection"]) for r in btts} == {("btts", "yes"), ("btts", "no")})
    check("unknown market -> no rows", _market_snapshots({"key": "spreads", "_bm": "x", "outcomes": []}, ev, 1, "now") == [])

    from .match_model import apply_book
    books = {"ou25": {1: {"over": (1.90, 0.50)}}}  # devigged book P(over)=0.50
    over_row = apply_book({"fixture_id": 1, "market": "ou25", "selection": "over", "probability": 0.62}, books)
    check("ou25 over gets a book price (Value-eligible now, not just 1X2)", over_row["market_odds"] == 1.9)
    check("edge = model − devig", abs(over_row["edge"] - 0.12) < 1e-9)
    no_book = apply_book({"fixture_id": 2, "market": "ou25", "selection": "over", "probability": 0.62}, books)
    check("no book price -> no edge (stays out of Value)", no_book.get("market_odds") is None and no_book.get("edge") is None)

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

    print("== De-vig (power method) ==")
    # symmetric two-way book: power de-vig must split 50/50
    sym = power_devig({"over": 1 / 1.91, "under": 1 / 1.91})
    check("power devig normalises to 1", abs(sum(sym.values()) - 1) < 1e-9)
    check("symmetric book devigs to 0.5", abs(sym["over"] - 0.5) < 1e-6)
    # favourite-longshot: vs proportional, power shifts overround onto the longshot,
    # so the favourite's fair prob is HIGHER and the longshot's LOWER
    implied = {"home": 1 / 1.30, "draw": 1 / 5.5, "away": 1 / 11.0}
    over = sum(implied.values())
    prop = {k: v / over for k, v in implied.items()}
    pw = power_devig(implied)
    check("power devig sums to 1", abs(sum(pw.values()) - 1) < 1e-9)
    check("favourite fair prob >= proportional", pw["home"] > prop["home"],
          f"power={pw['home']:.3f} prop={prop['home']:.3f}")
    check("longshot fair prob <= proportional", pw["away"] < prop["away"],
          f"power={pw['away']:.3f} prop={prop['away']:.3f}")

    print("== Settlement ==")
    check("1x2 settle", settle_outcome("1x2", "home", 2, 1) is True
          and settle_outcome("1x2", "draw", 1, 1) is True)
    check("ou25 settle", settle_outcome("ou25", "over", 2, 1) is True
          and settle_outcome("ou25", "under", 1, 1) is True)
    check("goal ladder settles by line",
          settle_outcome("ou35", "over", 2, 2) is True       # 4 > 3.5
          and settle_outcome("ou35", "under", 2, 1) is True  # 3 < 3.5
          and settle_outcome("ou55", "over", 4, 2) is True   # 6 > 5.5
          and settle_outcome("ou15", "under", 1, 0) is True) # 1 < 1.5
    check("goal ladder unsettleable after extra time",
          settle_outcome("ou35", "over", 2, 2, duration="EXTRA_TIME") is None)
    check("corners settle with stats", settle_outcome("corners_o95", "over", 1, 0, 6, 5) is True
          and settle_outcome("team_corners_home_o45", "over", 1, 0, 5, 2) is True)
    check("unsettleable markets return None", settle_outcome("corners_o95", "over", 1, 0) is None
          and settle_outcome("ht_1x2", "home", 2, 1) is None)
    # 90' markets vs football-data fullTime that includes extra time
    check("ET match settles 1x2 as the 90' draw",
          settle_outcome("1x2", "draw", 2, 1, duration="EXTRA_TIME") is True
          and settle_outcome("1x2", "home", 2, 1, duration="EXTRA_TIME") is False)
    check("ET match goal markets unsettleable (no 90' score)",
          settle_outcome("ou25", "over", 2, 1, duration="EXTRA_TIME") is None
          and settle_outcome("cs", "2-1", 2, 1, duration="PENALTY_SHOOTOUT") is None
          and settle_outcome("corners_o95", "over", 1, 1, 6, 5, "EXTRA_TIME") is None)
    # first-half markets settle from the stored HT score
    check("ht_1x2 settles with HT score",
          settle_outcome("ht_1x2", "home", 2, 1, ht_hg=1, ht_ag=0) is True
          and settle_outcome("ht_1x2", "draw", 2, 1, ht_hg=0, ht_ag=0) is True)
    check("1H totals settle with HT score",
          settle_outcome("ou05_1h", "over", 2, 1, ht_hg=1, ht_ag=0) is True
          and settle_outcome("ou15_1h", "under", 2, 1, ht_hg=1, ht_ag=0) is True)
    check("htft settles (incl. ET as 90' draw)",
          settle_outcome("htft", "draw_home", 2, 1, ht_hg=0, ht_ag=0) is True
          and settle_outcome("htft", "home_draw", 2, 1, ht_hg=1, ht_ag=0, duration="EXTRA_TIME") is True)

    print("== Knockout advancement decomposition ==")
    from . import simulate
    # P(advance) = P(win 90') + P(draw 90')*p_et with p_et = 0.5 + (W_e-0.5)*shrink.
    # The shrink reduces a favourite's advance prob versus crediting them the
    # full win-prob on a 90' draw (the old raw-W_e coin), and is exact at parity.
    shrink = config.MODEL_PARAMS["KO_ET_SHRINK"]
    for dr in (0.0, 200.0, 500.0):
        mat = dc_scoreline_matrix(*elo_lambdas(1600 + dr, 1600, False), config.MODEL_PARAMS["DC_RHO"])
        pw, pd = float(np.tril(mat, -1).sum()), float(np.trace(mat))
        we = 1.0 / (1.0 + 10 ** (-dr / 400.0))
        p_et = 0.5 + (we - 0.5) * shrink
        p_adv = pw + pd * p_et
        p_adv_noshrink = pw + pd * we
        check(f"ET shrink between 0.5 and We (dr={dr:.0f})", 0.5 - 1e-9 <= p_et <= we + 1e-9)
        if dr == 0:
            check("KO advance = 0.5 at parity", abs(p_adv - 0.5) < 1e-6)
        else:
            check(f"shrink lowers favourite advance (dr={dr:.0f})", p_adv < p_adv_noshrink,
                  f"shrunk={p_adv:.3f} full-credit={p_adv_noshrink:.3f}")
    # conditional_advancement: buckets a result-conditioned advance array
    rng_t = np.random.default_rng(0)
    hg_t = rng_t.poisson(1.6, 4000); ag_t = rng_t.poisson(1.0, 4000)
    adv_t = (hg_t > ag_t).astype(float)  # advance iff won — so P(adv|win)=1, P(adv|loss)=0
    w, d, l = simulate.conditional_advancement(adv_t, hg_t, ag_t)
    check("conditional advance|win = 1", w is not None and abs(w - 1.0) < 1e-9)
    check("conditional advance|loss = 0", l is not None and abs(l - 0.0) < 1e-9)

    print("== Official bracket (R32 slots + thirds allocation) ==")
    from itertools import combinations
    slot_strings = [s for pair in simulate.R32_SLOTS.values() for s in pair]
    check("every group winner appears exactly once",
          sorted(s[1] for s in slot_strings if s.startswith("1")) == list("ABCDEFGHIJKL"))
    check("every runner-up appears exactly once",
          sorted(s[1] for s in slot_strings if s.startswith("2")) == list("ABCDEFGHIJKL"))
    check("exactly 8 third-place slots",
          sum(1 for s in slot_strings if s == "T") == 8
          and set(simulate.R32_THIRD_GROUPS) == {m for m, p in simulate.R32_SLOTS.items() if "T" in p})
    r16_sources = sorted(m for pair in simulate.R16_MAP.values() for m in pair)
    check("R16 consumes each R32 match once", r16_sources == sorted(simulate.R32_SLOTS))
    qf_sources = sorted(m for pair in simulate.QF_MAP.values() for m in pair)
    check("QF consumes each R16 match once", qf_sources == sorted(simulate.R16_MAP))
    sf_sources = sorted(m for pair in simulate.SF_MAP.values() for m in pair)
    check("SF consumes each QF match once", sf_sources == sorted(simulate.QF_MAP))
    check("final consumes both semis", sorted(simulate.FINAL) == sorted(simulate.SF_MAP))
    ok_combos = 0
    for combo in combinations("ABCDEFGHIJKL", 8):
        assign = simulate.third_assignment(tuple(combo))
        if (sorted(assign.values()) == sorted(combo)
                and all(g in simulate.R32_THIRD_GROUPS[m] for m, g in assign.items())):
            ok_combos += 1
    check("thirds allocation valid for all 495 scenarios", ok_combos == 495, f"{ok_combos}/495")

    print("== Same-game correlation fit (tetrachoric) ==")
    from .correlations import _binorm_cdf, _inv_norm_cdf, tetrachoric
    # round-trip: build a joint from a known ρ, recover ρ from the marginals + joint
    for p1, p2, rho0 in ((0.6, 0.5, 0.55), (0.4, 0.7, 0.3), (0.55, 0.45, -0.4)):
        z1, z2 = _inv_norm_cdf(p1), _inv_norm_cdf(p2)
        p12 = _binorm_cdf(z1, z2, rho0)
        check(f"tetrachoric recovers ρ={rho0}", abs(tetrachoric(p1, p2, p12) - rho0) < 5e-3,
              f"got {tetrachoric(p1, p2, p12):.4f}")
    check("independence (p12 = p1·p2) -> ρ ≈ 0",
          abs(tetrachoric(0.6, 0.5, 0.6 * 0.5)) < 5e-3)
    check("degenerate marginal -> None", tetrachoric(1.0, 0.5, 0.5) is None)

    print("== Simulation invariants ==")
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
    check("16 teams reach R16 on average", abs(sum(r["reach_r16"] for r in rows) - 16) < 0.1)
    check("8 quarter-finalists on average", abs(sum(r["reach_qf"] for r in rows) - 8) < 0.1)
    check("2 finalists on average", abs(sum(r["reach_final"] for r in rows) - 2) < 0.05)
    check("monotone funnel per team", all(
        r["advance_grp"] >= r["reach_r16"] >= r["reach_qf"] >= r["reach_sf"]
        >= r["reach_final"] >= r["champion"] for r in rows))
    strongest = next(r for r in rows if r["team_id"] == 1)
    weakest = next(r for r in rows if r["team_id"] == 48)
    check("strength ordering respected", strongest["champion"] > weakest["champion"]
          and strongest["advance_grp"] > 0.85)

    print(f"\nALL {PASS} MODEL-MATH CHECKS PASSED")


if __name__ == "__main__":
    main()
