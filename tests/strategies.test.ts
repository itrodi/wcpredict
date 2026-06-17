/** Matchday staking strategies (v4.7): safest singles + safest accumulators per
 * match day, with flat + fractional-Kelly sizing. Run: npm test */
import assert from "node:assert/strict";
import test from "node:test";

import { pairCorrelation, sameGameEligible } from "../lib/correlation";
import { gridJoint, isGridMarket } from "../lib/scoregrid";
import {
  bestLegPerFixture,
  buildStrategies,
  crossMatchCombos,
  kelly,
  KELLY_CAP,
  matchdayKey,
  safestCombos,
  sameGameCombos,
  type StrategyLeg,
  type StrategyRow,
} from "../lib/strategies";

const RESOLVED = { blendPipeline: "blend_free", modelPipeline: "free" };
const ALL_CALIBRATED: string[] = []; // empty list => only always-calibrated markets are non-experimental

const fx = (id: number, kickoff: string, home: string, away: string) => ({
  id,
  kickoff,
  status: "scheduled",
  home: { name: home },
  away: { name: away },
});

function row(
  fixture_id: number,
  market: string,
  selection: string,
  probability: number,
  market_odds: number | null,
  pipeline = "free",
  f?: StrategyRow["fixtures"]
): StrategyRow {
  return {
    pipeline,
    market,
    selection,
    probability,
    market_odds,
    fair_odds: probability > 0 ? 1 / probability : null,
    fixture_id,
    fixtures: f,
  };
}

test("kelly: positive edge sizes a fractional, capped fraction", () => {
  // p=0.6 at 2.0 → full Kelly f = (1*0.6 - 0.4)/1 = 0.2; quarter Kelly = 0.05
  assert.ok(Math.abs(kelly(0.6, 2.0) - 0.05) < 1e-9);
});

test("kelly: fair odds (p = 1/odds) carry zero edge", () => {
  assert.equal(kelly(0.5, 2.0), 0);
});

test("kelly: never exceeds the cap", () => {
  assert.ok(kelly(0.95, 5) <= KELLY_CAP + 1e-9);
});

test("bestLegPerFixture keeps the single safest priced leg per fixture", () => {
  const rows: StrategyRow[] = [
    row(1, "1x2", "home", 0.7, 1.5, "blend_free", fx(1, "2026-06-20T18:00:00Z", "Brazil", "Haiti")),
    row(1, "ou15", "over", 0.82, 1.3, "free"), // higher prob → should win the fixture
    row(1, "btts", "yes", 0.55, 1.9, "free"),
  ];
  const legs = bestLegPerFixture(rows, RESOLVED, ALL_CALIBRATED);
  assert.equal(legs.length, 1);
  assert.equal(legs[0].market, "ou15");
  assert.equal(legs[0].probability, 0.82);
});

test("bestLegPerFixture enforces view ownership (blend owns 1X2, model owns rest)", () => {
  const rows: StrategyRow[] = [
    // wrong-pipeline 1X2 row must be ignored (free does not own 1X2 in this view)
    row(1, "1x2", "home", 0.9, 1.4, "free", fx(1, "2026-06-20T18:00:00Z", "A", "B")),
    row(1, "ou15", "over", 0.6, 1.6, "free"),
  ];
  const legs = bestLegPerFixture(rows, RESOLVED, ALL_CALIBRATED);
  assert.equal(legs.length, 1);
  assert.equal(legs[0].market, "ou15");
});

test("bestLegPerFixture drops sub-threshold, unpriced and excluded markets", () => {
  const rows: StrategyRow[] = [
    row(1, "ou25", "over", 0.45, 2.1, "free", fx(1, "2026-06-20T18:00:00Z", "A", "B")), // below MIN_LEG_PROB
    row(2, "ou25", "over", 0.7, null, "free", fx(2, "2026-06-20T18:00:00Z", "C", "D")), // no price (fair also null)
    row(3, "cs", "1-0", 0.6, 6.0, "free", fx(3, "2026-06-20T18:00:00Z", "E", "F")), // excluded market
  ];
  // null fair_odds for the unpriced one
  rows[1].fair_odds = null;
  const legs = bestLegPerFixture(rows, RESOLVED, ALL_CALIBRATED);
  assert.equal(legs.length, 0);
});

test("safestCombos = the K highest-probability legs, products computed", () => {
  const rows: StrategyRow[] = [
    row(1, "ou15", "over", 0.9, 1.2, "free", fx(1, "2026-06-20T18:00:00Z", "A", "B")),
    row(2, "ou15", "over", 0.8, 1.3, "free", fx(2, "2026-06-20T18:00:00Z", "C", "D")),
    row(3, "ou15", "over", 0.7, 1.5, "free", fx(3, "2026-06-20T18:00:00Z", "E", "F")),
  ];
  const legs = bestLegPerFixture(rows, RESOLVED, ALL_CALIBRATED);
  const combos = safestCombos(legs);
  const double = combos.find((c) => c.size === 2)!;
  assert.ok(Math.abs(double.combinedProbability - 0.9 * 0.8) < 1e-9);
  assert.ok(Math.abs(double.combinedOdds - 1.2 * 1.3) < 1e-9);
  // doubles are safer than trebles → higher combined probability
  const treble = combos.find((c) => c.size === 3)!;
  assert.ok(double.combinedProbability > treble.combinedProbability);
});

test("safestCombos skips fold sizes larger than the leg pool", () => {
  const rows: StrategyRow[] = [
    row(1, "ou15", "over", 0.9, 1.2, "free", fx(1, "2026-06-20T18:00:00Z", "A", "B")),
    row(2, "ou15", "over", 0.8, 1.3, "free", fx(2, "2026-06-20T18:00:00Z", "C", "D")),
  ];
  const legs = bestLegPerFixture(rows, RESOLVED, ALL_CALIBRATED);
  const combos = safestCombos(legs);
  assert.deepEqual(combos.map((c) => c.size), [2]); // no treble/4-fold with only 2 legs
});

test("matchdayKey buckets by UTC calendar day", () => {
  assert.equal(matchdayKey("2026-06-20T23:30:00Z"), "2026-06-20");
});

test("buildStrategies groups legs by matchday, sorted ascending", () => {
  const rows: StrategyRow[] = [
    row(1, "ou15", "over", 0.85, 1.25, "free", fx(1, "2026-06-21T18:00:00Z", "A", "B")),
    row(2, "ou15", "over", 0.8, 1.3, "free", fx(2, "2026-06-20T15:00:00Z", "C", "D")),
    row(3, "ou15", "over", 0.75, 1.4, "free", fx(3, "2026-06-20T21:00:00Z", "E", "F")),
  ];
  const days = buildStrategies(rows, RESOLVED, ALL_CALIBRATED);
  assert.deepEqual(days.map((d) => d.date), ["2026-06-20", "2026-06-21"]);
  const d20 = days[0];
  assert.equal(d20.legCount, 2);
  assert.equal(d20.combos.find((c) => c.size === 2)?.legs.length, 2);
  assert.equal(days[1].combos.length, 0); // only one fixture that day → no accumulators
});

// ── correlation model ────────────────────────────────────────────────────────

test("pairCorrelation: over goals + BTTS yes is strongly positive", () => {
  const rho = pairCorrelation({ market: "ou25", selection: "over" }, { market: "btts", selection: "yes" });
  assert.equal(rho, 0.55);
});

test("pairCorrelation: opposing directions flip the sign", () => {
  const rho = pairCorrelation({ market: "ou25", selection: "over" }, { market: "btts", selection: "no" });
  assert.equal(rho, -0.55);
});

test("pairCorrelation: nested same-category and unmodeled pairs are ineligible", () => {
  assert.equal(pairCorrelation({ market: "ou15", selection: "over" }, { market: "ou25", selection: "over" }), null);
  assert.equal(pairCorrelation({ market: "1x2", selection: "home" }, { market: "ou25", selection: "over" }), null);
});

test("pairCorrelation: a fitted estimate overrides the static prior", () => {
  const fitted = new Map([["btts|ftgoals", 0.4]]); // engine-fit value, below the 0.55 prior
  const rho = pairCorrelation(
    { market: "ou25", selection: "over" },
    { market: "btts", selection: "yes" },
    fitted
  );
  assert.equal(rho, 0.4);
  // direction still flips the sign on the fitted base
  const flipped = pairCorrelation(
    { market: "ou25", selection: "over" },
    { market: "btts", selection: "no" },
    fitted
  );
  assert.equal(flipped, -0.4);
});

test("pairCorrelation: falls back to the prior when fitted lacks the pair", () => {
  const fitted = new Map([["corners|ftgoals", 0.1]]);
  const rho = pairCorrelation(
    { market: "ou25", selection: "over" },
    { market: "btts", selection: "yes" },
    fitted
  );
  assert.equal(rho, 0.55); // unchanged prior
});

test("sameGameEligible covers goal markets, excludes result/scoreline", () => {
  assert.ok(sameGameEligible("ou25"));
  assert.ok(sameGameEligible("btts"));
  assert.ok(sameGameEligible("corners_o95"));
  assert.ok(!sameGameEligible("1x2"));
  assert.ok(!sameGameEligible("cs"));
});

// ── value mode ───────────────────────────────────────────────────────────────

test("value mode keeps positive-edge underdogs and drops fair-odds legs", () => {
  const rows: StrategyRow[] = [
    // model 0.45 but priced at 2.5 → implied 0.40 → +edge, below the safe floor
    { ...row(1, "ou25", "over", 0.45, 2.5, "free", fx(1, "2026-06-20T18:00:00Z", "A", "B")), edge: 0.05 },
    // no book price (fair only) → excluded from value mode
    row(2, "ou15", "over", 0.9, null, "free", fx(2, "2026-06-20T18:00:00Z", "C", "D")),
  ];
  const legs = bestLegPerFixture(rows, RESOLVED, ALL_CALIBRATED, "value");
  assert.equal(legs.length, 1);
  assert.equal(legs[0].fixtureId, 1);
  assert.ok(legs[0].ev > 1, "kept because EV = 0.45 × 2.5 > 1");
});

test("value mode ranks accumulators by combined EV, not probability", () => {
  const lowProbHighEv: StrategyLeg = {
    fixtureId: 1, home: "A", away: "B", kickoff: "2026-06-20T18:00:00Z",
    market: "ou25", selection: "over", probability: 0.5, odds: 2.4, oddsIsFair: false,
    edge: 0.08, ev: 1.2, flatStake: 1, kelly: 0,
  };
  const highProbLowEv: StrategyLeg = {
    ...lowProbHighEv, fixtureId: 2, probability: 0.85, odds: 1.2, ev: 1.02,
  };
  const [combo] = crossMatchCombos([highProbLowEv, lowProbHighEv], "value", [2]);
  // first leg listed is the higher-EV one, not the higher-probability one
  assert.equal(combo.legs[0].fixtureId, 1);
  assert.ok(Math.abs(combo.expectedValue - 1.2 * 1.02) < 1e-9);
});

// ── same-game combos (correlation-aware) ─────────────────────────────────────

test("sameGameCombos pairs correlated markets and beats the naive product", () => {
  const bucket = {
    home: "Brazil", away: "Serbia", kickoff: "2026-06-20T18:00:00Z",
    legs: [
      { fixtureId: 1, home: "Brazil", away: "Serbia", kickoff: "2026-06-20T18:00:00Z",
        market: "ou25", selection: "over", probability: 0.65, odds: 1.6, oddsIsFair: false,
        edge: 0.02, ev: 1.04, flatStake: 1, kelly: 0 },
      { fixtureId: 1, home: "Brazil", away: "Serbia", kickoff: "2026-06-20T18:00:00Z",
        market: "btts", selection: "yes", probability: 0.6, odds: 1.7, oddsIsFair: false,
        edge: 0.02, ev: 1.02, flatStake: 1, kelly: 0 },
    ] as StrategyLeg[],
  };
  const [sgc] = sameGameCombos([bucket], "safe");
  assert.equal(sgc.rho, 0.55);
  assert.ok(sgc.jointProbability > sgc.independentProbability, "correlation lifts the joint above 0.39");
  assert.ok(sgc.jointProbability <= 0.6 + 1e-9, "bounded by the smaller leg");
});

test("sameGameCombos needs an eligible pair", () => {
  const bucket = {
    home: "A", away: "B", kickoff: "2026-06-20T18:00:00Z",
    legs: [
      { fixtureId: 1, home: "A", away: "B", kickoff: "2026-06-20T18:00:00Z",
        market: "1x2", selection: "home", probability: 0.7, odds: 1.5, oddsIsFair: false,
        edge: 0.02, ev: 1.05, flatStake: 1, kelly: 0 },
      { fixtureId: 1, home: "A", away: "B", kickoff: "2026-06-20T18:00:00Z",
        market: "ou25", selection: "over", probability: 0.6, odds: 1.7, oddsIsFair: false,
        edge: 0.02, ev: 1.02, flatStake: 1, kelly: 0 },
    ] as StrategyLeg[],
  };
  // without a scoreline grid, 1x2 isn't same-game eligible, so no pair can form
  assert.equal(sameGameCombos([bucket], "safe").length, 0);
});

test("sameGameCombos: result × goals-over is priced EXACTLY from the scoreline grid", () => {
  // home-favourite, high-scoring grid (rows = home goals, cols = away goals)
  const grid = [
    [0.02, 0.02, 0.02],
    [0.06, 0.1, 0.05],
    [0.15, 0.3, 0.28],
  ];
  const exactJoint = gridJoint(grid, { market: "1x2", selection: "home" }, { market: "ou15", selection: "over" })!;
  assert.ok(Math.abs(exactJoint.joint - 0.45) < 1e-9, "home AND over-1.5 = 0.45 by hand");

  const bucket = {
    home: "A", away: "B", kickoff: "2026-06-20T18:00:00Z", grid,
    legs: [
      { fixtureId: 1, home: "A", away: "B", kickoff: "2026-06-20T18:00:00Z",
        market: "1x2", selection: "home", probability: 0.51, odds: 1.9, oddsIsFair: false,
        edge: 0.02, ev: 1.05, flatStake: 1, kelly: 0 },
      { fixtureId: 1, home: "A", away: "B", kickoff: "2026-06-20T18:00:00Z",
        market: "ou15", selection: "over", probability: 0.9, odds: 1.25, oddsIsFair: false,
        edge: 0.02, ev: 1.12, flatStake: 1, kelly: 0 },
    ] as StrategyLeg[],
  };
  const [sgc] = sameGameCombos([bucket], "safe");
  assert.equal(sgc.exact, true);
  assert.ok(Math.abs(sgc.jointProbability - 0.45) < 1e-9, "joint comes straight from the grid");
  assert.ok(Math.abs(sgc.independentProbability - 0.51 * 0.9) < 1e-9, "indep. uses grid marginals");
});

test("isGridMarket: result/goals/BTTS are grid markets; corners/1H are not", () => {
  assert.equal(isGridMarket("1x2"), true);
  assert.equal(isGridMarket("ou25"), true);
  assert.equal(isGridMarket("btts"), true);
  assert.equal(isGridMarket("corners_o95"), false);
  assert.equal(isGridMarket("ou15_1h"), false);
});
