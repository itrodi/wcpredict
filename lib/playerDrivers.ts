/** Player drivers (v4.9): aggregate per-appearance player stats
 * (player_match_stats, ingested from /matches/{id}/player-stats) into per-team
 * "who drives each metric" rankings, tying individual players to the markets we
 * actually price — goals/BTTS, chance creation, the corners market, and card
 * risk. Pure + unit-tested; the team page reads the raw rows and calls this. */

export type PlayerMatchStat = {
  statsapi_id: string;
  team_id?: number | null;
  name: string | null;
  position: string | null;
  minutes: number | null;
  rating: number | null;
  goals: number | null;
  shots: number | null;
  shots_on_target: number | null;
  key_passes: number | null;
  duels_won: number | null;
  dribbles: number | null;
  fouls_drawn: number | null;
  fouls_committed: number | null;
  yellows: number | null;
  reds: number | null;
};

export type PlayerAggregate = {
  id: string;
  name: string;
  position: string | null;
  matches: number;
  minutes: number;
  goals: number;
  shots: number;
  shotsOnTarget: number;
  keyPasses: number;
  duelsWon: number;
  dribbles: number;
  foulsDrawn: number;
  foulsCommitted: number;
  yellows: number;
  reds: number;
};

const n = (v: number | null | undefined) => (typeof v === "number" && Number.isFinite(v) ? v : 0);

/** Sum each player's appearances into a season-to-date aggregate. */
export function aggregatePlayers(rows: PlayerMatchStat[]): PlayerAggregate[] {
  const by = new Map<string, PlayerAggregate>();
  for (const r of rows) {
    if (!r.statsapi_id) continue;
    const a =
      by.get(r.statsapi_id) ??
      {
        id: r.statsapi_id, name: r.name ?? "Unknown", position: r.position,
        matches: 0, minutes: 0, goals: 0, shots: 0, shotsOnTarget: 0, keyPasses: 0,
        duelsWon: 0, dribbles: 0, foulsDrawn: 0, foulsCommitted: 0, yellows: 0, reds: 0,
      };
    a.matches += 1;
    a.minutes += n(r.minutes);
    a.goals += n(r.goals);
    a.shots += n(r.shots);
    a.shotsOnTarget += n(r.shots_on_target);
    a.keyPasses += n(r.key_passes);
    a.duelsWon += n(r.duels_won);
    a.dribbles += n(r.dribbles);
    a.foulsDrawn += n(r.fouls_drawn);
    a.foulsCommitted += n(r.fouls_committed);
    a.yellows += n(r.yellows);
    a.reds += n(r.reds);
    if (r.name) a.name = r.name; // prefer a non-null name
    by.set(r.statsapi_id, a);
  }
  return [...by.values()];
}

export type DriverCategory = {
  key: string;
  label: string;
  drives: string; // the priced market this metric feeds
  score: (a: PlayerAggregate) => number;
  detail: (a: PlayerAggregate) => string;
};

/** Each category maps a playing-style metric to a market we price. */
export const DRIVER_CATEGORIES: DriverCategory[] = [
  {
    key: "goals",
    label: "Goals & finishing",
    drives: "goals over / BTTS",
    score: (a) => a.goals * 10 + a.shotsOnTarget, // scorers first, then shot threat
    detail: (a) => `${a.goals} G · ${a.shotsOnTarget}/${a.shots} on target`,
  },
  {
    key: "creation",
    label: "Chance creation",
    drives: "the shots behind overs",
    score: (a) => a.keyPasses,
    detail: (a) => `${a.keyPasses} key passes`,
  },
  {
    key: "corners",
    label: "Corner & set-piece pressure",
    drives: "the corners market",
    score: (a) => a.shots + a.dribbles + a.foulsDrawn, // territory + pressure that wins corners/set-pieces
    detail: (a) => `${a.shots} sh · ${a.dribbles} drb · ${a.foulsDrawn} fouls won`,
  },
  {
    key: "discipline",
    label: "Card risk",
    drives: "card markets / referee context",
    score: (a) => a.foulsCommitted + a.yellows * 3 + a.reds * 6,
    detail: (a) => `${a.foulsCommitted} fouls · ${a.yellows}Y${a.reds ? ` ${a.reds}R` : ""}`,
  },
];

/** Top-n players for a category (positive contribution only), highest first. */
export function topDrivers(
  aggs: PlayerAggregate[],
  cat: DriverCategory,
  limit = 3
): { player: PlayerAggregate; value: number }[] {
  return aggs
    .map((player) => ({ player, value: cat.score(player) }))
    .filter((d) => d.value > 0)
    .sort((x, y) => y.value - x.value)
    .slice(0, limit);
}
