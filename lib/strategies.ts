/** Matchday staking strategies (v4.7): group upcoming fixtures by match day and
 * surface, per day, the safest single picks and the safest accumulators
 * (doubles → 4-folds), each with flat-stake and fractional-Kelly sizing.
 *
 * "Safety only" ranking: a combo's strength is the combined probability that
 * EVERY leg lands. To maximise that for a given fold size you simply take the K
 * highest-probability legs — so no combinatorial search is needed.
 *
 * Probabilities are model probabilities; odds are the book price when one exists
 * (else the model's fair odds, which carry zero edge → Kelly = 0). Pure +
 * unit-tested; the combined-probability product assumes leg independence, which
 * the UI flags as a caveat. */

import { isExperimental } from "./markets";

export type StrategyRow = {
  pipeline: string;
  market: string;
  selection: string;
  probability: number | string;
  market_odds: number | string | null;
  fair_odds: number | string | null;
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
  flatStake: number;   // flat baseline, always 1 unit
  kelly: number;       // fraction of bankroll (fractional + capped); 0 when no edge
};

export type Combo = {
  size: number;                 // number of legs (2 = double, 3 = treble, …)
  legs: StrategyLeg[];
  combinedProbability: number;  // product of leg probabilities (independence assumed)
  combinedOdds: number;         // product of leg odds
  flatStake: number;            // 1 unit
  kelly: number;                // fraction of bankroll on the combo
  expectedValue: number;        // return per 1u flat stake = combinedProbability × combinedOdds
};

export type Matchday = {
  date: string;          // YYYY-MM-DD (UTC)
  legCount: number;      // distinct fixtures with a qualifying leg
  singles: StrategyLeg[];
  combos: Combo[];
};

/** Markets too granular or self-correlated to belong in a "safe" combo. */
export const EXCLUDED_MARKETS = new Set(["cs", "htft"]);

/** A leg only counts as "safe" if the model makes it more likely than not. */
export const MIN_LEG_PROB = 0.5;

/** Accumulator fold sizes built per matchday, smallest (safest) first. */
export const COMBO_SIZES = [2, 3, 4];

/** Fractional Kelly (stake a fraction of the full Kelly bet) + an absolute cap,
 * both standard guards against Kelly's notorious over-betting. */
export const KELLY_FRACTION = 0.25;
export const KELLY_CAP = 0.05; // never suggest more than 5% of bankroll on one bet

/** How many single picks to list per matchday. */
export const SINGLES_LIMIT = 6;

const num = (v: number | string | null | undefined): number | null => {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

/** Fractional, capped Kelly fraction. Returns 0 when there is no positive edge
 * (which includes fair-odds legs, where p = 1/odds exactly). */
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

/** Pick the single safest qualifying selection for each upcoming fixture.
 *  - applies view ownership (blend owns 1X2, the model pipeline owns the rest)
 *  - drops excluded + still-experimental markets
 *  - requires a usable price and probability ≥ MIN_LEG_PROB
 *  - keeps the highest-probability leg per fixture (higher odds breaks ties) */
export function bestLegPerFixture(
  rows: StrategyRow[],
  resolved: { blendPipeline: string; modelPipeline: string },
  calibratedMarkets: string[]
): StrategyLeg[] {
  // Group by fixture so fixture metadata can come from any row carrying the join
  // (matches buildGoalRush — joined rows don't all need to repeat the embed).
  const byFixture = new Map<number, StrategyRow[]>();
  for (const r of rows) {
    const list = byFixture.get(r.fixture_id) ?? [];
    list.push(r);
    byFixture.set(r.fixture_id, list);
  }

  const best: StrategyLeg[] = [];
  for (const [fixtureId, list] of byFixture) {
    const fx = list.find((r) => r.fixtures)?.fixtures;
    if (!fx || fx.status === "finished") continue;

    let leg: StrategyLeg | null = null;
    for (const r of list) {
      // view ownership: the blend owns 1X2, the model pipeline owns everything else
      const owner = r.market === "1x2" ? resolved.blendPipeline : resolved.modelPipeline;
      if (r.pipeline !== owner) continue;

      if (EXCLUDED_MARKETS.has(r.market)) continue;
      if (isExperimental(r.market, calibratedMarkets)) continue;

      const probability = num(r.probability);
      if (probability == null || probability < MIN_LEG_PROB) continue;

      const book = num(r.market_odds);
      const fair = num(r.fair_odds);
      const odds = book ?? fair;
      if (odds == null || odds <= 1) continue;

      if (
        leg &&
        !(probability > leg.probability || (probability === leg.probability && odds > leg.odds))
      ) {
        continue;
      }

      leg = {
        fixtureId,
        home: fx.home?.name ?? "TBD",
        away: fx.away?.name ?? "TBD",
        kickoff: fx.kickoff,
        market: r.market,
        selection: r.selection,
        probability,
        odds,
        oddsIsFair: book == null,
        flatStake: 1,
        kelly: kelly(probability, odds),
      };
    }

    if (leg) best.push(leg);
  }
  return best;
}

/** Build the safest accumulator at each fold size from a day's legs: the safest
 * K-fold is exactly the K highest-probability legs. */
export function safestCombos(legs: StrategyLeg[], sizes = COMBO_SIZES): Combo[] {
  const ranked = [...legs].sort((a, b) => b.probability - a.probability || b.odds - a.odds);
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

/** UTC calendar day (YYYY-MM-DD) of a kickoff timestamp. */
export function matchdayKey(iso: string): string {
  return new Date(iso).toISOString().slice(0, 10);
}

/** Full per-matchday strategy board: safest singles + safest accumulators. */
export function buildStrategies(
  rows: StrategyRow[],
  resolved: { blendPipeline: string; modelPipeline: string },
  calibratedMarkets: string[],
  singlesLimit = SINGLES_LIMIT
): Matchday[] {
  const legs = bestLegPerFixture(rows, resolved, calibratedMarkets);

  const byDay = new Map<string, StrategyLeg[]>();
  for (const leg of legs) {
    const key = matchdayKey(leg.kickoff);
    const list = byDay.get(key) ?? [];
    list.push(leg);
    byDay.set(key, list);
  }

  const days: Matchday[] = [];
  for (const [date, dayLegs] of byDay) {
    const ranked = [...dayLegs].sort((a, b) => b.probability - a.probability || b.odds - a.odds);
    days.push({
      date,
      legCount: ranked.length,
      singles: ranked.slice(0, singlesLimit),
      combos: safestCombos(ranked),
    });
  }
  days.sort((a, b) => a.date.localeCompare(b.date));
  return days;
}
