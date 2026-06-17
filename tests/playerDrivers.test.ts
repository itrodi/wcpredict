/** Player driver aggregation (v4.9). Run: npm test */
import assert from "node:assert/strict";
import test from "node:test";

import {
  aggregatePlayers,
  DRIVER_CATEGORIES,
  type PlayerMatchStat,
  topDrivers,
} from "../lib/playerDrivers";

const blank = (over: Partial<PlayerMatchStat>): PlayerMatchStat => ({
  statsapi_id: "pl_x", name: "X", position: "F", minutes: 90, rating: 7,
  goals: 0, shots: 0, shots_on_target: 0, key_passes: 0, duels_won: 0,
  dribbles: 0, fouls_drawn: 0, fouls_committed: 0, yellows: 0, reds: 0,
  ...over,
});

test("aggregatePlayers sums appearances per player", () => {
  const rows = [
    blank({ statsapi_id: "pl_1", name: "Striker", goals: 1, shots: 4, shots_on_target: 2 }),
    blank({ statsapi_id: "pl_1", name: "Striker", goals: 2, shots: 5, shots_on_target: 3, minutes: 80 }),
    blank({ statsapi_id: "pl_2", name: "Mid", key_passes: 3 }),
  ];
  const aggs = aggregatePlayers(rows);
  const striker = aggs.find((a) => a.id === "pl_1")!;
  assert.equal(striker.matches, 2);
  assert.equal(striker.goals, 3);
  assert.equal(striker.shots, 9);
  assert.equal(striker.minutes, 170);
});

test("aggregatePlayers tolerates null metrics", () => {
  const aggs = aggregatePlayers([blank({ statsapi_id: "pl_3", goals: null, shots: null, minutes: null })]);
  assert.equal(aggs[0].goals, 0);
  assert.equal(aggs[0].minutes, 0);
  assert.equal(aggs[0].matches, 1);
});

test("topDrivers ranks scorers ahead of high-shot non-scorers for the goals metric", () => {
  const aggs = aggregatePlayers([
    blank({ statsapi_id: "pl_scorer", name: "Scorer", goals: 2, shots: 3, shots_on_target: 2 }),
    blank({ statsapi_id: "pl_volume", name: "Volume", goals: 0, shots: 9, shots_on_target: 4 }),
  ]);
  const goals = DRIVER_CATEGORIES.find((c) => c.key === "goals")!;
  const ranked = topDrivers(aggs, goals);
  assert.equal(ranked[0].player.name, "Scorer"); // goals weighted above shot volume
});

test("topDrivers drops zero-contribution players and respects the limit", () => {
  const aggs = aggregatePlayers([
    blank({ statsapi_id: "a", name: "A", fouls_committed: 3, yellows: 1 }),
    blank({ statsapi_id: "b", name: "B", fouls_committed: 1 }),
    blank({ statsapi_id: "c", name: "C" }), // no fouls/cards → excluded from discipline
  ]);
  const discipline = DRIVER_CATEGORIES.find((c) => c.key === "discipline")!;
  const ranked = topDrivers(aggs, discipline, 2);
  assert.equal(ranked.length, 2);
  assert.equal(ranked[0].player.name, "A");
  assert.ok(ranked.every((d) => d.value > 0));
});

test("every driver category maps to a priced market", () => {
  for (const c of DRIVER_CATEGORIES) {
    assert.ok(c.drives.length > 0, `${c.key} should name the market it drives`);
  }
});
