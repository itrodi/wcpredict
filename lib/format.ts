export const pct = (p: number | null | undefined) =>
  p == null ? "–" : `${(p * 100).toFixed(p >= 0.1 ? 0 : 1)}%`;

export const odds = (o: number | null | undefined) =>
  o == null ? "–" : Number(o).toFixed(2);

export const STAGE_LABELS: Record<string, string> = {
  group: "Group stage",
  R32: "Round of 32",
  R16: "Round of 16",
  QF: "Quarter-final",
  SF: "Semi-final",
  "3P": "Third place",
  F: "Final",
};

export const MARKET_LABELS: Record<string, string> = {
  "1x2": "Match result (1X2)",
  ou25: "Over / Under 2.5 goals",
  btts: "Both teams to score",
  cs: "Correct score",
  ht_1x2: "Half-time result",
  ou05_1h: "1st half Over / Under 0.5",
  ou15_1h: "1st half Over / Under 1.5",
  htft: "Half-time / Full-time",
  corners_o85: "Total corners 8.5",
  corners_o95: "Total corners 9.5",
  corners_o105: "Total corners 10.5",
  team_corners_home_o45: "Home corners 4.5",
  team_corners_away_o45: "Away corners 4.5",
};

/** Corners markets ship "experimental" until model_scores shows >=30 scored matches (spec v4 §5.2). */
export const EXPERIMENTAL_MARKETS = new Set([
  "corners_o85",
  "corners_o95",
  "corners_o105",
  "team_corners_home_o45",
  "team_corners_away_o45",
]);
export const EXPERIMENTAL_MIN_N = 30;

export const SELECTION_LABELS: Record<string, string> = {
  home: "Home",
  draw: "Draw",
  away: "Away",
  over: "Over 2.5",
  under: "Under 2.5",
  yes: "Yes",
  no: "No",
  other: "Any other score",
};

export const kickoffFmt = (iso: string) =>
  new Date(iso).toLocaleString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  });
