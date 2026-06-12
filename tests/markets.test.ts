/** Phase 2 acceptance test (spec v4.1): every market key produces a
 * non-generic label and the O/U label bug never regresses — `ou05_1h`
 * must never say "2.5". Run: npm test */
import assert from "node:assert/strict";
import test from "node:test";

import { MARKET_ORDER, MARKETS, marketLabel, parseLine, selectionLabel } from "../lib/markets";

test("every catalogue key has a non-generic label", () => {
  for (const key of Object.keys(MARKETS)) {
    const label = marketLabel(key);
    assert.notEqual(label, key, `label for ${key} falls back to the raw key`);
    assert.ok(label.length > 3, `label for ${key} too short: ${label}`);
  }
});

test("lines parse by convention", () => {
  assert.equal(parseLine("ou25"), 2.5);
  assert.equal(parseLine("ou05_1h"), 0.5);
  assert.equal(parseLine("ou15_1h"), 1.5);
  assert.equal(parseLine("corners_o85"), 8.5);
  assert.equal(parseLine("corners_o95"), 9.5);
  assert.equal(parseLine("corners_o105"), 10.5);
  assert.equal(parseLine("team_corners_home_o45"), 4.5);
  assert.equal(parseLine("corners_1h_o45"), 4.5);
  assert.equal(parseLine("1x2"), null);
});

test("the O/U label bug: ou05_1h never says 2.5", () => {
  for (const sel of ["over", "under"]) {
    const label = selectionLabel("ou05_1h", sel);
    assert.ok(!label.includes("2.5"), `ou05_1h ${sel} label leaked 2.5: ${label}`);
    assert.ok(label.includes("0.5"), `ou05_1h ${sel} label missing 0.5: ${label}`);
  }
  assert.equal(selectionLabel("ou15_1h", "over"), "Over 1.5");
  assert.equal(selectionLabel("ou25", "under"), "Under 2.5");
});

test("corners selections carry their own lines", () => {
  assert.equal(selectionLabel("corners_o85", "over"), "Over 8.5");
  assert.equal(selectionLabel("corners_o95", "under"), "Under 9.5");
  assert.equal(selectionLabel("corners_o105", "over"), "Over 10.5");
  assert.equal(selectionLabel("team_corners_away_o45", "under"), "Under 4.5");
});

test("htft and result selections are human", () => {
  assert.equal(selectionLabel("htft", "home_draw"), "Home / Draw");
  assert.equal(selectionLabel("1x2", "away"), "Away");
  assert.equal(selectionLabel("cs", "other"), "Any other score");
});

test("ordering is unique and complete", () => {
  assert.equal(new Set(MARKET_ORDER).size, Object.keys(MARKETS).length);
  assert.equal(MARKET_ORDER[0], "1x2");
});
