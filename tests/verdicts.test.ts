/** Match-verdict logic (v4.5): per fixture the model's strongest call in result,
 * goals and corners + the dominant pick. Run: npm test */
import assert from "node:assert/strict";
import test from "node:test";

import { buildVerdicts, confLabel, type VerdictRow } from "../lib/verdicts";

const fx = (kickoff: string) => ({
  kickoff,
  status: "scheduled",
  home: { name: "Brazil" },
  away: { name: "Haiti" },
});

function rows(): VerdictRow[] {
  return [
    // fixture 1 — strong favourite, lean over, corners over
    { pipeline: "blend_statsapi", market: "1x2", selection: "home", probability: 0.74, fixture_id: 1, fixtures: fx("2026-06-20T18:00:00Z") },
    { pipeline: "blend_statsapi", market: "1x2", selection: "draw", probability: 0.16, fixture_id: 1 },
    { pipeline: "blend_statsapi", market: "1x2", selection: "away", probability: 0.10, fixture_id: 1 },
    { pipeline: "statsapi", market: "ou25", selection: "over", probability: 0.61, fixture_id: 1 },
    { pipeline: "statsapi", market: "ou25", selection: "under", probability: 0.39, fixture_id: 1 },
    { pipeline: "statsapi", market: "corners_o85", selection: "over", probability: 0.63, fixture_id: 1 },
    { pipeline: "statsapi", market: "corners_o85", selection: "under", probability: 0.37, fixture_id: 1 },
    // a model 1x2 row that must be ignored when a blend row exists
    { pipeline: "statsapi", market: "1x2", selection: "home", probability: 0.99, fixture_id: 1 },
    // fixture 2 — earlier kickoff, no corners data
    { pipeline: "blend_statsapi", market: "1x2", selection: "away", probability: 0.52, fixture_id: 2, fixtures: fx("2026-06-18T15:00:00Z") },
    { pipeline: "statsapi", market: "ou25", selection: "under", probability: 0.58, fixture_id: 2 },
  ];
}

test("one verdict per fixture, sorted by kickoff", () => {
  const v = buildVerdicts(rows(), "blend_statsapi", "statsapi");
  assert.equal(v.length, 2);
  assert.equal(v[0].fixtureId, 2, "earlier kickoff comes first");
  assert.equal(v[1].fixtureId, 1);
});

test("result uses the blend row, not the inflated model 1x2 row", () => {
  const v = buildVerdicts(rows(), "blend_statsapi", "statsapi").find((x) => x.fixtureId === 1)!;
  assert.equal(v.result?.selection, "home");
  assert.equal(v.result?.prob, 0.74); // blend, not the 0.99 model row
});

test("goals and corners pick the higher-probability side", () => {
  const v = buildVerdicts(rows(), "blend_statsapi", "statsapi").find((x) => x.fixtureId === 1)!;
  assert.equal(v.goals?.selection, "over");
  assert.equal(v.corners?.market, "corners_o85");
  assert.equal(v.corners?.selection, "over");
});

test("top pick is the dominant call across markets", () => {
  const v = buildVerdicts(rows(), "blend_statsapi", "statsapi").find((x) => x.fixtureId === 1)!;
  assert.equal(v.top?.market, "1x2"); // 0.74 beats goals 0.61 and corners 0.63
  assert.equal(v.top?.prob, 0.74);
});

test("missing markets degrade to null, not a crash", () => {
  const v = buildVerdicts(rows(), "blend_statsapi", "statsapi").find((x) => x.fixtureId === 2)!;
  assert.equal(v.corners, null);
  assert.equal(v.result?.selection, "away");
  assert.equal(v.goals?.selection, "under");
});

test("confidence labels band correctly", () => {
  assert.equal(confLabel(0.72), "High");
  assert.equal(confLabel(0.63), "Medium");
  assert.equal(confLabel(0.56), "Lean");
  assert.equal(confLabel(0.51), "Slight");
});
