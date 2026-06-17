/** Matchday staking strategies (v4.7): safest singles + safest accumulators per
 * match day, with flat + fractional-Kelly sizing. Run: npm test */
import assert from "node:assert/strict";
import test from "node:test";

import {
  bestLegPerFixture,
  buildStrategies,
  kelly,
  KELLY_CAP,
  matchdayKey,
  safestCombos,
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
