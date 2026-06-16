/** Single source of truth for market labels, grouping, ordering and selection
 * labels (spec v4.1 Phase 2). Components must consume this — no inline label
 * logic anywhere else. The line is parsed from the market key by convention:
 * `ou25` → 2.5 · `ou05_1h` → 0.5 · `corners_o95` → 9.5 · `..._o45` → 4.5. */

export type MarketGroup = "Result" | "Goals" | "First Half" | "Corners" | "Scoreline";

export type MarketDef = {
  label: string;
  group: MarketGroup;
  order: number;
  selectionLabel: (sel: string) => string;
  experimentalUntilN?: number; // stays "experimental" until model_scores.n reaches this
};

/** Parse the O/U line from a market key: trailing digits after `_o` or `ou`, /10. */
export function parseLine(key: string): number | null {
  const m = key.match(/(?:_o|^ou)(\d+)/);
  return m ? Number(m[1]) / 10 : null;
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

const RESULT_SELECTIONS: Record<string, string> = { home: "Home", draw: "Draw", away: "Away" };

const ouSelection = (key: string) => (sel: string) =>
  sel === "over" || sel === "under" ? `${cap(sel)} ${parseLine(key)}` : cap(sel);

const ou = (key: string, label: string, group: MarketGroup, order: number, expN?: number): MarketDef => ({
  label,
  group,
  order,
  selectionLabel: ouSelection(key),
  ...(expN ? { experimentalUntilN: expN } : {}),
});

export const EXPERIMENTAL_MIN_N = 30;

export const MARKETS: Record<string, MarketDef> = {
  "1x2": {
    label: "Match result (1X2)",
    group: "Result",
    order: 10,
    selectionLabel: (sel) => RESULT_SELECTIONS[sel] ?? cap(sel),
  },
  ou15: ou("ou15", "Over/Under 1.5 goals", "Goals", 18),
  ou25: ou("ou25", "Over/Under 2.5 goals", "Goals", 20),
  ou35: ou("ou35", "Over/Under 3.5 goals", "Goals", 22),
  ou45: ou("ou45", "Over/Under 4.5 goals", "Goals", 24),
  ou55: ou("ou55", "Over/Under 5.5 goals", "Goals", 26),
  btts: {
    label: "Both teams to score",
    group: "Goals",
    order: 30,
    selectionLabel: (sel) => (sel === "yes" ? "Yes" : sel === "no" ? "No" : cap(sel)),
  },
  ht_1x2: {
    label: "Half-time result",
    group: "First Half",
    order: 40,
    selectionLabel: (sel) => RESULT_SELECTIONS[sel] ?? cap(sel),
  },
  ou05_1h: ou("ou05_1h", "1st half Over/Under 0.5", "First Half", 50),
  ou15_1h: ou("ou15_1h", "1st half Over/Under 1.5", "First Half", 60),
  htft: {
    label: "Half-time / Full-time",
    group: "First Half",
    order: 70,
    selectionLabel: (sel) => {
      const [ht, ft] = sel.split("_");
      return ht && ft ? `${RESULT_SELECTIONS[ht] ?? cap(ht)} / ${RESULT_SELECTIONS[ft] ?? cap(ft)}` : cap(sel);
    },
  },
  // Corners are a solid, data-driven market (v4.5) — no longer experimental-badged.
  corners_o85: ou("corners_o85", "Total corners 8.5", "Corners", 80),
  corners_o95: ou("corners_o95", "Total corners 9.5", "Corners", 90),
  corners_o105: ou("corners_o105", "Total corners 10.5", "Corners", 100),
  corners_1h_o45: ou("corners_1h_o45", "1st half corners 4.5", "Corners", 105),
  team_corners_home_o45: ou("team_corners_home_o45", "Home team corners 4.5", "Corners", 110),
  team_corners_away_o45: ou("team_corners_away_o45", "Away team corners 4.5", "Corners", 120),
  cs: {
    label: "Correct score",
    group: "Scoreline",
    order: 130,
    selectionLabel: (sel) => (sel === "other" ? "Any other score" : sel),
  },
};

export const MARKET_ORDER = Object.entries(MARKETS)
  .sort(([, a], [, b]) => a.order - b.order)
  .map(([k]) => k);

export function marketLabel(key: string): string {
  return MARKETS[key]?.label ?? key;
}

export function selectionLabel(market: string, sel: string): string {
  return MARKETS[market]?.selectionLabel(sel) ?? sel;
}

/** Experimental = market declares a threshold AND no model_scores row has reached it. */
export function isExperimental(market: string, calibratedMarkets: string[]): boolean {
  const n = MARKETS[market]?.experimentalUntilN;
  return n != null && !calibratedMarkets.includes(market);
}
