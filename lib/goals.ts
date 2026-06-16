/** Goal rush (v4.6): rank upcoming matches by how likely they are to be
 * high-scoring, exposing the full over ladder (1.5 → 5.5). Pure + unit-tested. */

export type GoalRow = {
  market: string;
  selection: string;
  probability: number | string;
  fixture_id: number;
  fixtures?: {
    kickoff: string;
    status: string;
    home: { name: string } | null;
    away: { name: string } | null;
  } | null;
};

/** Over-line ladder, low to high. */
export const GOAL_LADDER: [string, number][] = [
  ["ou15", 1.5],
  ["ou25", 2.5],
  ["ou35", 3.5],
  ["ou45", 4.5],
  ["ou55", 5.5],
];

export type GoalMatch = {
  fixtureId: number;
  home: string;
  away: string;
  kickoff: string;
  overs: Record<string, number>; // market key -> P(over)
  headline: { market: string; line: number; prob: number } | null; // biggest line still ≥ 50%
};

export function buildGoalRush(rows: GoalRow[], limit = 15): GoalMatch[] {
  const byFixture = new Map<number, GoalRow[]>();
  for (const r of rows) {
    const list = byFixture.get(r.fixture_id) ?? [];
    list.push(r);
    byFixture.set(r.fixture_id, list);
  }

  const out: GoalMatch[] = [];
  for (const [fixtureId, list] of byFixture) {
    const fx = list.find((r) => r.fixtures)?.fixtures;
    if (!fx) continue;
    const overs: Record<string, number> = {};
    for (const r of list) {
      if (r.selection === "over") overs[r.market] = Number(r.probability);
    }
    if (overs.ou25 == null) continue; // need at least the anchor line
    // headline = the highest line the model still makes more likely than not
    let headline: GoalMatch["headline"] = null;
    for (const [market, line] of GOAL_LADDER) {
      const p = overs[market];
      if (p != null && p >= 0.5) headline = { market, line, prob: p };
    }
    out.push({
      fixtureId,
      home: fx.home?.name ?? "TBD",
      away: fx.away?.name ?? "TBD",
      kickoff: fx.kickoff,
      overs,
      headline,
    });
  }
  // rank by likelihood of a goal-fest: over 3.5 first, over 2.5 as tiebreak
  out.sort(
    (a, b) => (b.overs.ou35 ?? 0) - (a.overs.ou35 ?? 0) || (b.overs.ou25 ?? 0) - (a.overs.ou25 ?? 0)
  );
  return out.slice(0, limit);
}
