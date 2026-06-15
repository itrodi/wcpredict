/** Match verdicts (v4.5): the model's single strongest call per market for every
 * upcoming game — a browsable "best pick" view, distinct from the disciplined,
 * tracked Bankers/Value picks. Pure logic so it can be unit-tested. */

export type VerdictPick = { market: string; selection: string; prob: number };

export type Verdict = {
  fixtureId: number;
  home: string;
  away: string;
  kickoff: string;
  result: VerdictPick | null; // full-time 1X2
  goals: VerdictPick | null; // over/under 2.5
  corners: VerdictPick | null; // strongest total-corners call
  top: VerdictPick | null; // dominant pick across the three
};

export type VerdictRow = {
  pipeline: string;
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

const CORNERS = new Set(["corners_o85", "corners_o95", "corners_o105"]);

/** Highest-probability row matching `ok`, ignoring near-certainties above `cap`
 * (those are obvious, not interesting headline calls). */
function pickBest(rows: VerdictRow[], ok: (r: VerdictRow) => boolean, cap: number): VerdictPick | null {
  let best: VerdictPick | null = null;
  for (const r of rows) {
    if (!ok(r)) continue;
    const p = Number(r.probability);
    if (!Number.isFinite(p) || p > cap) continue;
    if (!best || p > best.prob) best = { market: r.market, selection: r.selection, prob: p };
  }
  return best;
}

export function buildVerdicts(rows: VerdictRow[], blendPipe: string, modelPipe: string): Verdict[] {
  const byFixture = new Map<number, VerdictRow[]>();
  for (const r of rows) {
    const list = byFixture.get(r.fixture_id) ?? [];
    list.push(r);
    byFixture.set(r.fixture_id, list);
  }

  const out: Verdict[] = [];
  for (const [fixtureId, list] of byFixture) {
    const fx = list.find((r) => r.fixtures)?.fixtures;
    if (!fx) continue;
    // 1X2 comes from the blend (market-anchored), falling back to the model row
    const result =
      pickBest(list, (r) => r.market === "1x2" && r.pipeline === blendPipe, 0.97) ??
      pickBest(list, (r) => r.market === "1x2" && r.pipeline === modelPipe, 0.97);
    const goals = pickBest(list, (r) => r.market === "ou25" && r.pipeline === modelPipe, 0.97);
    const corners = pickBest(list, (r) => CORNERS.has(r.market) && r.pipeline === modelPipe, 0.9);
    const top = [result, goals, corners]
      .filter((p): p is VerdictPick => p != null)
      .reduce<VerdictPick | null>((b, p) => (!b || p.prob > b.prob ? p : b), null);
    out.push({
      fixtureId,
      home: fx.home?.name ?? "TBD",
      away: fx.away?.name ?? "TBD",
      kickoff: fx.kickoff,
      result,
      goals,
      corners,
      top,
    });
  }
  out.sort((a, b) => new Date(a.kickoff).getTime() - new Date(b.kickoff).getTime());
  return out;
}

/** Tailwind text colour by confidence band — shared by the component. */
export function confClass(p: number): string {
  if (p >= 0.7) return "text-emerald-300";
  if (p >= 0.6) return "text-lime-300";
  if (p >= 0.55) return "text-amber-300";
  return "text-zinc-400";
}

export function confLabel(p: number): string {
  if (p >= 0.7) return "High";
  if (p >= 0.6) return "Medium";
  if (p >= 0.55) return "Lean";
  return "Slight";
}
