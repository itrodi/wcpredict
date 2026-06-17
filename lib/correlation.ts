/** Same-game market correlation model (v4.7): how strongly two markets within
 * the SAME match move together, used to price same-game combos via the Gaussian
 * copula in lib/stats.ts.
 *
 * We model only the goal-driven markets, where the correlation is structural and
 * well understood (more goals → over hits AND both-teams-score hits AND more
 * corners). Result (1X2), correct score and HT/FT are deliberately left out:
 * their dependence on goals is not a clean scalar, so we don't pretend to price
 * it. Cross-MATCH legs are treated as independent (ρ = 0) by the strategy
 * builder — that is the correct default for distinct fixtures.
 *
 * Coefficients are deliberately conservative, documented assumptions — not
 * fitted — and the UI shows the resulting correlation effect explicitly. */

type Category = "ftgoals" | "btts" | "corners" | "hfgoals";

/** Map a market key to its correlation category, or null if not modeled. */
function category(market: string): Category | null {
  if (/^ou\d+$/.test(market)) return "ftgoals"; // ou15…ou55 (full-time goal lines)
  if (market === "btts") return "btts";
  if (market.startsWith("corners_") || market.startsWith("team_corners_")) return "corners";
  if (market === "ou05_1h" || market === "ou15_1h") return "hfgoals";
  return null;
}

/** Selection direction on the "more events" axis: over/yes = +1, under/no = −1. */
function direction(selection: string): 1 | -1 | null {
  if (selection === "over" || selection === "yes") return 1;
  if (selection === "under" || selection === "no") return -1;
  return null;
}

/** Base correlation magnitude per unordered category pair (all positive on the
 * "more events" axis; sign is applied from the selection directions). */
const BASE_RHO: Record<string, number> = {
  "btts|ftgoals": 0.55,
  "corners|ftgoals": 0.3,
  "ftgoals|hfgoals": 0.45,
  "btts|corners": 0.2,
  "btts|hfgoals": 0.3,
  "corners|hfgoals": 0.2,
};

/** Correlation ρ between two same-game legs, or null when the pair isn't
 * eligible for a same-game combo (unmodeled market, same category — which would
 * be a nested/duplicate bet — or a non-directional selection). */
export function pairCorrelation(
  a: { market: string; selection: string },
  b: { market: string; selection: string }
): number | null {
  const ca = category(a.market);
  const cb = category(b.market);
  if (!ca || !cb || ca === cb) return null; // same category ⇒ nested/duplicate
  const da = direction(a.selection);
  const db = direction(b.selection);
  if (da == null || db == null) return null;
  const base = BASE_RHO[[ca, cb].sort().join("|")];
  if (base == null) return null;
  return base * da * db;
}

/** Whether a market can take part in a same-game combo at all. */
export function sameGameEligible(market: string): boolean {
  return category(market) != null;
}
