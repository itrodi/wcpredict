/** Exact same-game goal joints (v4.8). The engine stores a per-fixture
 * full-time scoreline grid (grid[i][j] = P(home i, away j) under the free
 * Elo→Poisson model); from it we compute the EXACT joint probability of any two
 * goal-derived markets (result, full-time goal lines, BTTS) without a copula
 * approximation. Pairs that touch corners or first-half goals aren't in the grid
 * and stay on the copula in lib/correlation.ts. Pure + unit-tested. */

export type ScoreGrid = number[][]; // [home goals][away goals]

/** Outcome predicate over (home goals i, away goals j) for a grid-derived
 * market+selection, or null if the market isn't on the scoreline grid. */
function gridOutcome(market: string, selection: string): ((i: number, j: number) => boolean) | null {
  if (market === "1x2") {
    if (selection === "home") return (i, j) => i > j;
    if (selection === "draw") return (i, j) => i === j;
    if (selection === "away") return (i, j) => i < j;
    return null;
  }
  const ou = /^ou(\d+)$/.exec(market); // ou15…ou55
  if (ou) {
    const line = Number(ou[1]) / 10; // "25" → 2.5
    if (selection === "over") return (i, j) => i + j > line;
    if (selection === "under") return (i, j) => i + j < line;
    return null;
  }
  if (market === "btts") {
    if (selection === "yes") return (i, j) => i >= 1 && j >= 1;
    if (selection === "no") return (i, j) => i < 1 || j < 1;
    return null;
  }
  return null;
}

/** Whether a market is priced off the scoreline grid (result / FT goals / BTTS). */
export function isGridMarket(market: string): boolean {
  return market === "1x2" || market === "btts" || /^ou\d+$/.test(market);
}

/** Exact joint of two grid-derived legs from the scoreline grid, plus each
 * leg's grid marginal (for the naive-product contrast and the implied
 * correlation). null when either market isn't on the grid. */
export function gridJoint(
  grid: ScoreGrid,
  a: { market: string; selection: string },
  b: { market: string; selection: string }
): { joint: number; pA: number; pB: number; rho: number } | null {
  const fa = gridOutcome(a.market, a.selection);
  const fb = gridOutcome(b.market, b.selection);
  if (!fa || !fb) return null;
  let joint = 0,
    pA = 0,
    pB = 0;
  for (let i = 0; i < grid.length; i++) {
    for (let j = 0; j < grid[i].length; j++) {
      const p = grid[i][j];
      const oa = fa(i, j);
      const ob = fb(i, j);
      if (oa) pA += p;
      if (ob) pB += p;
      if (oa && ob) joint += p;
    }
  }
  // implied correlation of the two binary outcomes (display/transparency only)
  const denom = Math.sqrt(pA * (1 - pA) * pB * (1 - pB));
  const rho = denom > 1e-9 ? (joint - pA * pB) / denom : 0;
  return { joint, pA, pB, rho };
}
