/** Goal rush ranking (v4.6): high-scoring matches surface first, with the
 * over ladder and the headline call. Run: npm test */
import assert from "node:assert/strict";
import test from "node:test";

import { buildGoalRush, type GoalRow } from "../lib/goals";

const fx = (kickoff: string, home: string, away: string) => ({
  kickoff,
  status: "scheduled",
  home: { name: home },
  away: { name: away },
});

function row(fixture_id: number, market: string, probability: number, f?: GoalRow["fixtures"]): GoalRow {
  return { market, selection: "over", probability, fixture_id, fixtures: f };
}

function rows(): GoalRow[] {
  return [
    // fixture 1 — goal-fest
    row(1, "ou15", 0.92, fx("2026-06-20T18:00:00Z", "Brazil", "Haiti")),
    row(1, "ou25", 0.74),
    row(1, "ou35", 0.52),
    row(1, "ou45", 0.3),
    row(1, "ou55", 0.14),
    // fixture 2 — low-scoring
    row(2, "ou15", 0.6, fx("2026-06-19T18:00:00Z", "Morocco", "Croatia")),
    row(2, "ou25", 0.38),
    row(2, "ou35", 0.18),
    // an under row that must be ignored
    { market: "ou25", selection: "under", probability: 0.62, fixture_id: 2 },
  ];
}

test("matches rank by likelihood of a goal-fest (over 3.5 first)", () => {
  const g = buildGoalRush(rows());
  assert.equal(g.length, 2);
  assert.equal(g[0].fixtureId, 1, "the high-scoring game ranks first");
  assert.equal(g[1].fixtureId, 2);
});

test("over ladder collected from over rows only", () => {
  const g = buildGoalRush(rows());
  const f1 = g.find((m) => m.fixtureId === 1)!;
  assert.equal(f1.overs.ou35, 0.52);
  assert.equal(f1.overs.ou25, 0.74);
  const f2 = g.find((m) => m.fixtureId === 2)!;
  assert.equal(f2.overs.ou25, 0.38); // not the 0.62 under row
});

test("headline is the highest line still ≥ 50%", () => {
  const g = buildGoalRush(rows());
  const f1 = g.find((m) => m.fixtureId === 1)!;
  assert.equal(f1.headline?.market, "ou35"); // 0.52, the highest line over 0.5
  const f2 = g.find((m) => m.fixtureId === 2)!;
  assert.equal(f2.headline?.market, "ou15"); // only 1.5 clears 0.5
});

test("fixtures without the anchor line are dropped", () => {
  const g = buildGoalRush([row(3, "ou15", 0.8, fx("2026-06-20T18:00:00Z", "A", "B"))]);
  assert.equal(g.length, 0); // no ou25 -> excluded
});

test("limit caps the list", () => {
  const many: GoalRow[] = [];
  for (let i = 1; i <= 20; i++) {
    many.push(row(i, "ou25", 0.5 + i / 100, fx("2026-06-20T18:00:00Z", `H${i}`, `A${i}`)));
    many.push(row(i, "ou35", 0.3 + i / 100));
  }
  assert.equal(buildGoalRush(many, 5).length, 5);
});
