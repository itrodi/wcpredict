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
};

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
