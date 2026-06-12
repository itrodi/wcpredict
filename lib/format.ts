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

// Market + selection labels live in lib/markets.ts (single source of truth,
// spec v4.1 Phase 2) — never derive them inline in components.

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

export const dateFmt = (iso: string) =>
  new Date(iso).toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
    timeZone: "UTC",
  });
