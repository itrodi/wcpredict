/** Matchday staking strategies (v4.7): per match day, surface the best single
 * picks, the best cross-match accumulators, and correlation-aware same-game
 * combos — in two modes:
 *
 *   safe   — ranked by the probability the bet lands (legs ≥ MIN_LEG_PROB).
 *   value  — ranked by expected value at the book price (positive edge only).
 *
 * Cross-match accumulators combine ONE leg per fixture and treat distinct
 * fixtures as independent (the correct default) — the safest K-fold is simply
 * the K best legs. Same-game combos pair two markets WITHIN one match and price
 * the dependence with a Gaussian copula (lib/stats.ts + lib/correlation.ts), so
 * the combined probability reflects real correlation rather than a naive product.
 *
 * Probabilities are model probabilities; odds are the book price when one exists
 * (else the model's fair odds, which carry zero edge → Kelly = 0). Pure +
 * unit-tested. */

import { type FittedCorrelations, pairCorrelation, sameGameEligible } from "./correlation";
import { isExperimental } from "./markets";
import { gridJoint, isGridMarket, type ScoreGrid } from "./scoregrid";
import { gaussianCopulaJoint } from "./stats";

export type StrategyMode = "safe" | "value";
export const STRATEGY_MODES: StrategyMode[] = ["safe", "value"];

/** Risk appetite shown side-by-side on the board (v4.9). A play's tier is set
 * by how likely the model thinks it is to land — the legs are drawn from a
 * probability band — and within each tier the picks are ranked to surface
 * value (positive edge at the book price). This is the "safe / medium / risky"
 * spectrum across every market (result, goals overs, BTTS, corners, halves). */
export type RiskTier = "safe" | "medium" | "risky";
export const RISK_TIERS: RiskTier[] = ["safe", "medium", "risky"];

export type StrategyRow = {
  pipeline: string;
  market: string;
  selection: string;
  probability: number | string;
  market_odds: number | string | null;
  fair_odds: number | string | null;
  edge?: number | string | null;
  fixture_id: number;
  fixtures?: {
    id: number;
    kickoff: string;
    status: string;
    home: { name: string } | null;
    away: { name: string } | null;
  } | null;
};

export type StrategyLeg = {
  fixtureId: number;
  home: string;
  away: string;
  kickoff: string;
  market: string;
  selection: string;
  probability: number;
  odds: number;
  oddsIsFair: boolean; // true when no book line exists and we fell back to fair odds
  edge: number | null; // model probability − book-implied probability (null without a book price)
  ev: number;          // expected return per 1u stake = probability × odds
  flatStake: number;   // flat baseline, always 1 unit
  kelly: number;       // fraction of bankroll (fractional + capped); 0 when no edge
};

export type Combo = {
  size: number;                 // number of legs (2 = double, 3 = treble, …)
  legs: StrategyLeg[];
  combinedProbability: number;  // product of leg probabilities (distinct matches ⇒ independent)
  combinedOdds: number;         // product of leg odds
  flatStake: number;            // 1 unit
  kelly: number;                // fraction of bankroll on the combo
  expectedValue: number;        // return per 1u flat stake = combinedProbability × combinedOdds
};

export type SameGameCombo = {
  fixtureId: number;
  home: string;
  away: string;
  kickoff: string;
  legs: [StrategyLeg, StrategyLeg];
  rho: number;                    // correlation between the two legs (exact from the grid, or modeled)
  exact: boolean;                 // true when priced from the exact scoreline grid (goal pairs)
  jointProbability: number;       // P(both land): exact grid joint, else copula
  independentProbability: number; // naive product, for contrast
  combinedOdds: number;           // product of leg odds (the book's combo price)
  flatStake: number;
  kelly: number;
  expectedValue: number;          // jointProbability × combinedOdds
};

export type Matchday = {
  date: string;          // YYYY-MM-DD (UTC)
  mode: StrategyMode;
  legCount: number;      // distinct fixtures with a qualifying leg
  singles: StrategyLeg[];
  combos: Combo[];           // cross-match accumulators
  sameGame: SameGameCombo[]; // correlation-aware same-game pairs
};

/** Markets too granular or self-correlated to belong in a combo. */
export const EXCLUDED_MARKETS = new Set(["cs", "htft"]);

/** A leg counts as "safe" only if the model makes it more likely than not. */
export const MIN_LEG_PROB = 0.5;
/** Value legs may be underdogs, but not longshots, and need a positive edge. */
export const MIN_VALUE_PROB = 0.25;

/** Risk-tier bands, keyed on the model probability that a leg lands. The floor
 * of the riskiest tier (MIN_VALUE_PROB) is the global cutoff — below it the
 * model is too uncertain to surface. `minJoint` is the same-game joint floor
 * for that tier (riskier tiers tolerate longer same-game pairs). `rankBy`
 * decides the in-tier ordering: the safe tier leads with the likeliest pick,
 * the riskier tiers lead with the best expected value. */
export const TIER_BANDS: Record<
  RiskTier,
  { minProb: number; maxProb: number; minJoint: number; rankBy: StrategyMode }
> = {
  safe: { minProb: 0.6, maxProb: 1.01, minJoint: 0.42, rankBy: "safe" },
  medium: { minProb: 0.42, maxProb: 0.6, minJoint: 0.25, rankBy: "value" },
  risky: { minProb: MIN_VALUE_PROB, maxProb: 0.42, minJoint: 0.12, rankBy: "value" },
};

/** Accumulator fold sizes built per matchday, smallest first. */
export const COMBO_SIZES = [2, 3, 4];

/** Fractional Kelly + an absolute cap — standard guards against over-betting. */
export const KELLY_FRACTION = 0.25;
export const KELLY_CAP = 0.05; // never suggest more than 5% of bankroll on one bet

/** How many singles / same-game combos to list per matchday. */
export const SINGLES_LIMIT = 6;

/** Same-game combos must be at least this likely to be worth showing (safe mode). */
export const SAME_GAME_MIN_JOINT = 0.35;

const num = (v: number | string | null | undefined): number | null => {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

/** Ranking metric for a leg under each mode. */
const legScore = (l: StrategyLeg, mode: StrategyMode) => (mode === "value" ? l.ev : l.probability);

/** Fractional, capped Kelly fraction. Returns 0 when there is no positive edge
 * (which includes fair-odds bets, where p = 1/odds exactly). */
export function kelly(
  p: number,
  odds: number,
  fraction = KELLY_FRACTION,
  cap = KELLY_CAP
): number {
  const b = odds - 1;
  if (b <= 0) return 0;
  const f = (b * p - (1 - p)) / b; // full Kelly
  if (f <= 0) return 0;
  return Math.min(f * fraction, cap);
}

/** Every qualifying leg per fixture, keyed by fixture id, with fixture meta.
 *  - applies view ownership (blend owns 1X2, the model pipeline owns the rest)
 *  - drops excluded + still-experimental markets and unpriced selections
 *  - safe mode keeps prob ≥ MIN_LEG_PROB; value mode needs a book price,
 *    prob ≥ MIN_VALUE_PROB and a positive edge */
type LegBucket = { home: string; away: string; kickoff: string; legs: StrategyLeg[]; grid?: ScoreGrid };

function legBuckets(
  rows: StrategyRow[],
  resolved: { blendPipeline: string; modelPipeline: string },
  calibratedMarkets: string[],
  mode: StrategyMode,
  grids?: Map<number, ScoreGrid>,
  safeFloor = MIN_LEG_PROB
): Map<number, LegBucket> {
  const byFixture = new Map<number, StrategyRow[]>();
  for (const r of rows) {
    const list = byFixture.get(r.fixture_id) ?? [];
    list.push(r);
    byFixture.set(r.fixture_id, list);
  }

  const out = new Map<number, LegBucket>();
  for (const [fixtureId, list] of byFixture) {
    const fx = list.find((r) => r.fixtures)?.fixtures;
    if (!fx || fx.status === "finished") continue;

    const legs: StrategyLeg[] = [];
    for (const r of list) {
      const owner = r.market === "1x2" ? resolved.blendPipeline : resolved.modelPipeline;
      if (r.pipeline !== owner) continue;
      if (EXCLUDED_MARKETS.has(r.market)) continue;
      if (isExperimental(r.market, calibratedMarkets)) continue;

      const probability = num(r.probability);
      if (probability == null) continue;

      const book = num(r.market_odds);
      const fair = num(r.fair_odds);
      const odds = book ?? fair;
      if (odds == null || odds <= 1) continue;

      let edge = num(r.edge);
      if (edge == null && book != null) edge = probability - 1 / book;

      if (mode === "safe") {
        if (probability < safeFloor) continue;
      } else {
        if (book == null) continue; // value needs a real price
        if (probability < MIN_VALUE_PROB) continue;
        if (edge == null || edge <= 0) continue;
      }

      legs.push({
        fixtureId,
        home: fx.home?.name ?? "TBD",
        away: fx.away?.name ?? "TBD",
        kickoff: fx.kickoff,
        market: r.market,
        selection: r.selection,
        probability,
        odds,
        oddsIsFair: book == null,
        edge,
        ev: probability * odds,
        flatStake: 1,
        kelly: kelly(probability, odds),
      });
    }

    if (legs.length)
      out.set(fixtureId, {
        home: fx.home?.name ?? "TBD",
        away: fx.away?.name ?? "TBD",
        kickoff: fx.kickoff,
        legs,
        grid: grids?.get(fixtureId),
      });
  }
  return out;
}

const bestLeg = (legs: StrategyLeg[], mode: StrategyMode): StrategyLeg =>
  legs.reduce((best, l) =>
    legScore(l, mode) > legScore(best, mode) ||
    (legScore(l, mode) === legScore(best, mode) && l.odds > best.odds)
      ? l
      : best
  );

/** One best leg per fixture (by the mode's ranking metric). */
export function bestLegPerFixture(
  rows: StrategyRow[],
  resolved: { blendPipeline: string; modelPipeline: string },
  calibratedMarkets: string[],
  mode: StrategyMode = "safe"
): StrategyLeg[] {
  return [...legBuckets(rows, resolved, calibratedMarkets, mode).values()].map((b) =>
    bestLeg(b.legs, mode)
  );
}

/** The best cross-match accumulator at each fold size: take the K top-ranked
 * legs. For "safe" that maximises combined probability; for "value" it maximises
 * combined EV. */
export function crossMatchCombos(
  legs: StrategyLeg[],
  mode: StrategyMode = "safe",
  sizes = COMBO_SIZES
): Combo[] {
  const ranked = [...legs].sort((a, b) => legScore(b, mode) - legScore(a, mode) || b.odds - a.odds);
  const combos: Combo[] = [];
  for (const size of sizes) {
    if (ranked.length < size) continue;
    const picked = ranked.slice(0, size);
    const combinedProbability = picked.reduce((acc, l) => acc * l.probability, 1);
    const combinedOdds = picked.reduce((acc, l) => acc * l.odds, 1);
    combos.push({
      size,
      legs: picked,
      combinedProbability,
      combinedOdds,
      flatStake: 1,
      kelly: kelly(combinedProbability, combinedOdds),
      expectedValue: combinedProbability * combinedOdds,
    });
  }
  return combos;
}

/** Back-compat alias: the safe-mode cross-match accumulators. */
export const safestCombos = (legs: StrategyLeg[], sizes = COMBO_SIZES): Combo[] =>
  crossMatchCombos(legs, "safe", sizes);

/** Best correlation-aware same-game combo (a pair of markets within one match)
 * per fixture. Pairs must be eligible (modeled, non-nested markets); the joint
 * probability comes from a Gaussian copula on the two marginals.
 *   safe  — keep the highest-joint-probability pair (≥ SAME_GAME_MIN_JOINT).
 *   value — keep the highest-EV pair with a positive combined edge and real
 *           book prices on both legs. */
export function sameGameCombos(
  buckets: { home: string; away: string; kickoff: string; legs: StrategyLeg[]; grid?: ScoreGrid }[],
  mode: StrategyMode = "safe",
  fitted?: FittedCorrelations,
  minJoint: number = SAME_GAME_MIN_JOINT
): SameGameCombo[] {
  const out: SameGameCombo[] = [];
  for (const b of buckets) {
    // a leg can pair if it's a copula category (goals/btts/corners/1H) OR a
    // grid market (result/goals/btts) when we have this fixture's scoreline grid
    const eligible = b.legs.filter(
      (l) => sameGameEligible(l.market) || (b.grid && isGridMarket(l.market))
    );
    let best: SameGameCombo | null = null;
    let bestScore = -Infinity;

    for (let i = 0; i < eligible.length; i++) {
      for (let j = i + 1; j < eligible.length; j++) {
        const a = eligible[i];
        const c = eligible[j];

        // Exact path: both legs are goal-derived and we have the grid. Compute
        // the joint directly from the coherent scoreline distribution — this is
        // what lets RESULT pair with goals-over/BTTS (no clean copula axis).
        let rho: number;
        let exact: boolean;
        let jointProbability: number;
        let independentProbability: number;
        if (b.grid && isGridMarket(a.market) && isGridMarket(c.market)) {
          const g = gridJoint(b.grid, a, c);
          if (!g || g.pA <= 0 || g.pB <= 0) continue;
          rho = g.rho;
          exact = true;
          jointProbability = g.joint;
          independentProbability = g.pA * g.pB; // grid marginals, consistent with the joint
        } else {
          const r = pairCorrelation(a, c, fitted);
          if (r == null) continue; // e.g. result × corners has no defined coupling
          rho = r;
          exact = false;
          jointProbability = gaussianCopulaJoint(a.probability, c.probability, rho);
          independentProbability = a.probability * c.probability;
        }

        const combinedOdds = a.odds * c.odds;
        const expectedValue = jointProbability * combinedOdds;

        if (mode === "value") {
          if (a.oddsIsFair || c.oddsIsFair || expectedValue <= 1) continue;
        } else if (jointProbability < minJoint) {
          continue;
        }

        const score = mode === "value" ? expectedValue : jointProbability;
        if (score <= bestScore) continue;
        bestScore = score;
        best = {
          fixtureId: a.fixtureId,
          home: b.home,
          away: b.away,
          kickoff: b.kickoff,
          legs: [a, c],
          rho,
          exact,
          jointProbability,
          independentProbability,
          combinedOdds,
          flatStake: 1,
          kelly: kelly(jointProbability, combinedOdds),
          expectedValue,
        };
      }
    }
    if (best) out.push(best);
  }
  return out.sort((x, y) =>
    mode === "value"
      ? y.expectedValue - x.expectedValue
      : y.jointProbability - x.jointProbability
  );
}

/** UTC calendar day (YYYY-MM-DD) of a kickoff timestamp. */
export function matchdayKey(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

/** Full per-matchday strategy board: singles + cross-match accumulators +
 * correlation-aware same-game combos, all in the chosen mode. */
export function buildStrategies(
  rows: StrategyRow[],
  resolved: { blendPipeline: string; modelPipeline: string },
  calibratedMarkets: string[],
  mode: StrategyMode = "safe",
  fitted?: FittedCorrelations,
  grids?: Map<number, ScoreGrid>,
  singlesLimit = SINGLES_LIMIT
): Matchday[] {
  const buckets = legBuckets(rows, resolved, calibratedMarkets, mode, grids);

  const byDay = new Map<string, { bestLegs: StrategyLeg[]; buckets: LegBucket[] }>();
  for (const bucket of buckets.values()) {
    const key = matchdayKey(bucket.kickoff);
    const slot = byDay.get(key) ?? { bestLegs: [], buckets: [] };
    slot.bestLegs.push(bestLeg(bucket.legs, mode));
    slot.buckets.push(bucket);
    byDay.set(key, slot);
  }

  const days: Matchday[] = [];
  for (const [date, slot] of byDay) {
    const ranked = [...slot.bestLegs].sort(
      (a, b) => legScore(b, mode) - legScore(a, mode) || b.odds - a.odds
    );
    days.push({
      date,
      mode,
      legCount: ranked.length,
      singles: ranked.slice(0, singlesLimit),
      combos: crossMatchCombos(ranked, mode),
      sameGame: sameGameCombos(slot.buckets, mode, fitted).slice(0, singlesLimit),
    });
  }
  days.sort((a, b) => a.date.localeCompare(b.date));
  return days;
}

// ── Risk tiers (v4.9) ────────────────────────────────────────────────────────

export type TierBoard = {
  tier: RiskTier;
  legCount: number;          // fixtures contributing a leg to this tier
  singles: StrategyLeg[];
  combos: Combo[];           // cross-match accumulators within the tier's band
  sameGame: SameGameCombo[]; // correlation-aware same-game pairs within the band
};

export type MatchdayTiers = {
  date: string;     // YYYY-MM-DD (UTC)
  legCount: number; // distinct fixtures with any qualifying leg that day
  tiers: TierBoard[]; // safe → medium → risky
};

/** Best leg per fixture under a tier's ranking metric, restricted to legs whose
 * probability sits inside the tier's band. */
function tierLegs(bucket: LegBucket, tier: RiskTier): StrategyLeg[] {
  const band = TIER_BANDS[tier];
  return bucket.legs.filter((l) => l.probability >= band.minProb && l.probability < band.maxProb);
}

/** The full safe→medium→risky board for every upcoming match day. Unlike
 * `buildStrategies` (one mode at a time) this surfaces all three risk appetites
 * at once: for each tier, the best single per fixture in that probability band,
 * cross-match accumulators built from those legs, and correlation-aware
 * same-game combos — every market in scope (result, goals overs, BTTS, corners,
 * first half), value-ranked so positive-edge prices rise to the top. */
export function buildRiskTiers(
  rows: StrategyRow[],
  resolved: { blendPipeline: string; modelPipeline: string },
  calibratedMarkets: string[],
  fitted?: FittedCorrelations,
  grids?: Map<number, ScoreGrid>,
  singlesLimit = SINGLES_LIMIT
): MatchdayTiers[] {
  // collect every priced leg down to the riskiest tier's floor, then band it
  const buckets = legBuckets(rows, resolved, calibratedMarkets, "safe", grids, MIN_VALUE_PROB);

  const byDay = new Map<string, LegBucket[]>();
  for (const bucket of buckets.values()) {
    const key = matchdayKey(bucket.kickoff);
    const list = byDay.get(key) ?? [];
    list.push(bucket);
    byDay.set(key, list);
  }

  const days: MatchdayTiers[] = [];
  for (const [date, dayBuckets] of byDay) {
    const tiers: TierBoard[] = RISK_TIERS.map((tier) => {
      const band = TIER_BANDS[tier];
      const mode = band.rankBy;

      // per-fixture buckets restricted to this tier's probability band
      const bandBuckets = dayBuckets
        .map((b) => ({ ...b, legs: tierLegs(b, tier) }))
        .filter((b) => b.legs.length > 0);

      const bestLegs = bandBuckets.map((b) => bestLeg(b.legs, mode));
      const singles = [...bestLegs]
        .sort((a, b) => legScore(b, mode) - legScore(a, mode) || b.odds - a.odds)
        .slice(0, singlesLimit);

      return {
        tier,
        legCount: bandBuckets.length,
        singles,
        combos: crossMatchCombos(bestLegs, mode),
        sameGame: sameGameCombos(bandBuckets, "safe", fitted, band.minJoint).slice(0, singlesLimit),
      };
    });

    days.push({
      date,
      legCount: dayBuckets.length,
      tiers,
    });
  }
  days.sort((a, b) => a.date.localeCompare(b.date));
  return days;
}
