import Link from "next/link";

import GoalRush from "@/components/GoalRush";
import MatchVerdicts from "@/components/MatchVerdicts";
import PicksLive from "@/components/PicksLive";
import { buildGoalRush, type GoalMatch, type GoalRow } from "@/lib/goals";
import { EXPERIMENTAL_MIN_N } from "@/lib/markets";
import { resolveView } from "@/lib/pipeline";
import { supabaseServer } from "@/lib/supabase/server";
import type { ModelScore, PickRow } from "@/lib/types";
import { buildVerdicts, type Verdict, type VerdictRow } from "@/lib/verdicts";

const VERDICT_SELECT =
  "pipeline, market, selection, probability, fixture_id, " +
  "fixtures!inner(kickoff, status, home:teams!fixtures_home_id_fkey(name), away:teams!fixtures_away_id_fkey(name))";
const VERDICT_MARKETS = ["1x2", "ou25", "corners_o85", "corners_o95", "corners_o105"];
const GOAL_MARKETS = ["ou15", "ou25", "ou35", "ou45", "ou55"];

export const revalidate = 300;

const PICK_SELECT =
  "*, fixtures(id, kickoff, status, home:teams!fixtures_home_id_fkey(name), away:teams!fixtures_away_id_fkey(name))";

type TierRecord = {
  n: number;
  wins: number;
  hitRate: number | null;
  pl: number;
  roi: number | null;     // pl / picks with a price, flat 1u stakes
  avgOdds: number | null;
  clv: number | null;     // mean CLV over picks with a recorded closing price
};

const emptyRecord = (): TierRecord => ({
  n: 0, wins: 0, hitRate: null, pl: 0, roi: null, avgOdds: null, clv: null,
});

/** Track record from EVERY settled pick — retired ones settle too (they were
 * followable while live; dropping them would select losers out of the record).
 * Hit rate, flat-1u P/L, ROI, average odds and CLV per bucket. Wins AND
 * losses — this block is the feature's credibility (spec v4.1 §4.3). */
function trackRecord(settled: PickRow[]): Record<"banker" | "value" | "retired", TierRecord> {
  const out = { banker: emptyRecord(), value: emptyRecord(), retired: emptyRecord() };
  const staked = { banker: 0, value: 0, retired: 0 };
  const oddsSum = { banker: 0, value: 0, retired: 0 };
  const clvSum = { banker: 0, value: 0, retired: 0 };
  const clvN = { banker: 0, value: 0, retired: 0 };
  for (const p of settled) {
    const bucket = p.retired_at ? "retired" : p.tier;
    const t = out[bucket];
    if (!t) continue;
    t.n++;
    if (p.outcome) t.wins++;
    if (p.market_odds) {
      staked[bucket]++;
      oddsSum[bucket] += Number(p.market_odds);
      t.pl += p.outcome ? Number(p.market_odds) - 1 : -1;
    }
    if (p.clv != null) {
      clvSum[bucket] += Number(p.clv);
      clvN[bucket]++;
    }
  }
  for (const bucket of ["banker", "value", "retired"] as const) {
    const t = out[bucket];
    t.hitRate = t.n > 0 ? t.wins / t.n : null;
    t.roi = staked[bucket] > 0 ? t.pl / staked[bucket] : null;
    t.avgOdds = staked[bucket] > 0 ? oddsSum[bucket] / staked[bucket] : null;
    t.clv = clvN[bucket] > 0 ? clvSum[bucket] / clvN[bucket] : null;
    t.pl = Math.round(t.pl * 100) / 100;
  }
  return out;
}

export default async function PicksPage() {
  const sb = supabaseServer();
  let live: PickRow[] = [];
  let settled: PickRow[] = [];
  let calibrated: string[] = [];
  let verdicts: Verdict[] = [];
  let goalRush: GoalMatch[] = [];
  if (sb) {
    const view = await resolveView({}, sb);
    const horizon = new Date(Date.now() + 10 * 86400_000).toISOString();
    const now = new Date().toISOString();
    const [{ data: l }, { data: s }, { data: scores }, { data: vrows }, { data: grows }] =
      await Promise.all([
      sb
        .from("picks")
        .select(PICK_SELECT)
        .is("retired_at", null)
        .is("outcome", null)
        .order("published_at", { ascending: false }),
      sb
        .from("picks")
        .select("*")
        .not("outcome", "is", null),
      sb.from("model_scores").select("*").gte("n", EXPERIMENTAL_MIN_N).eq("beats_baseline", true),
      sb
        .from("match_predictions")
        .select(VERDICT_SELECT)
        .in("pipeline", [view.blendPipeline, view.modelPipeline])
        .in("market", VERDICT_MARKETS)
        .eq("fixtures.status", "scheduled")
        .gte("fixtures.kickoff", now)
        .lte("fixtures.kickoff", horizon)
        .limit(1000),
      sb
        .from("match_predictions")
        .select(VERDICT_SELECT)
        .eq("pipeline", view.modelPipeline)
        .in("market", GOAL_MARKETS)
        .eq("selection", "over")
        .eq("fixtures.status", "scheduled")
        .gte("fixtures.kickoff", now)
        .lte("fixtures.kickoff", horizon)
        .limit(1000),
    ]);
    live = (l as unknown as PickRow[] | null) ?? [];
    settled = (s as PickRow[] | null) ?? [];
    calibrated = ((scores as ModelScore[] | null) ?? []).map((x) => x.market);
    verdicts = buildVerdicts(
      (vrows as unknown as VerdictRow[] | null) ?? [],
      view.blendPipeline,
      view.modelPipeline
    );
    goalRush = buildGoalRush((grows as unknown as GoalRow[] | null) ?? []);
  }

  const record = trackRecord(settled);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="mb-1 text-2xl font-bold text-zinc-100">Site picks</h1>
          <p className="max-w-xl text-sm text-zinc-500">
            Rule-generated from the site model across results <em>and</em> overs markets (goals,
            corners, team corners). Bankers are high-confidence model plays (priced at fair odds
            when no book line exists); value plays need a positive edge against the book. Capped per
            market type and per fixture, immutable once published, and every settled pick — retired
            ones included — is scored below in public. No hand-picking, no deleting losers.
          </p>
        </div>

        {/* Track record block — always visible (spec §4.3) */}
        <div className="rounded-xl border border-pitch-700 bg-pitch-900 p-4 text-sm">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-400">
            Track record (settled picks)
          </h2>
          {settled.length === 0 ? (
            <p className="text-xs text-zinc-600">No settled picks yet.</p>
          ) : (
            <>
              <table className="text-xs">
                <thead>
                  <tr className="text-zinc-500">
                    <th className="pr-3 text-left font-medium">Tier</th>
                    <th className="pr-3 text-right font-medium">W–L</th>
                    <th className="pr-3 text-right font-medium">Hit</th>
                    <th className="pr-3 text-right font-medium">Avg odds</th>
                    <th className="pr-3 text-right font-medium">P/L (1u)</th>
                    <th className="pr-3 text-right font-medium">ROI</th>
                    <th className="text-right font-medium">CLV</th>
                  </tr>
                </thead>
                <tbody>
                  {(["banker", "value", "retired"] as const).map((tier) => {
                    const r = record[tier];
                    if (tier === "retired" && r.n === 0) return null;
                    return (
                      <tr key={tier} className={tier === "retired" ? "text-zinc-500" : "text-zinc-200"}>
                        <td className="pr-3 capitalize">{tier === "retired" ? "retired*" : tier}</td>
                        <td className="pr-3 text-right font-mono">
                          {r.wins}–{r.n - r.wins}
                        </td>
                        <td className="pr-3 text-right font-mono">
                          {r.hitRate != null ? `${Math.round(r.hitRate * 100)}%` : "–"}
                        </td>
                        <td className="pr-3 text-right font-mono">
                          {r.avgOdds != null ? r.avgOdds.toFixed(2) : "–"}
                        </td>
                        <td className={`pr-3 text-right font-mono font-semibold ${r.pl >= 0 ? "text-accent" : "text-red-400"}`}>
                          {r.pl >= 0 ? "+" : ""}
                          {r.pl.toFixed(2)}u
                        </td>
                        <td className={`pr-3 text-right font-mono ${r.roi != null && r.roi >= 0 ? "text-accent" : "text-zinc-400"}`}>
                          {r.roi != null ? `${r.roi >= 0 ? "+" : ""}${(r.roi * 100).toFixed(1)}%` : "–"}
                        </td>
                        <td className={`text-right font-mono ${r.clv != null && r.clv >= 0 ? "text-accent" : "text-zinc-400"}`}>
                          {r.clv != null ? `${r.clv >= 0 ? "+" : ""}${(r.clv * 100).toFixed(1)}%` : "–"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <p className="mt-2 max-w-xs text-[10px] leading-relaxed text-zinc-600">
                CLV = published price vs the closing price; consistently positive CLV is the real
                evidence of edge, win/loss is mostly noise at this sample size.
                {record.banker.n + record.value.n < 50 &&
                  " Small sample — treat every number here as provisional."}
                {record.retired.n > 0 && " *Retired = withdrawn before kickoff but settled anyway."}
              </p>
            </>
          )}
        </div>
      </div>

      <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 text-xs text-amber-200/90">
        <strong>Read this first.</strong> Picks are model outputs, not guarantees — the track record
        above includes every loss for a reason. Check the{" "}
        <Link href="/models" className="underline">model scoreboard</Link> before trusting any number.
        If you choose to bet: only what you can afford to lose, 18+/21+ where applicable, and see{" "}
        <a className="underline" href="https://www.begambleaware.org" rel="noreferrer" target="_blank">
          BeGambleAware
        </a>
        .
      </div>

      <section className="space-y-3">
        <div>
          <h2 className="text-lg font-bold text-zinc-100">Every match — the model&rsquo;s top call</h2>
          <p className="text-sm text-zinc-500">
            The single most-confident pick per market (result, goals, corners) for every upcoming
            fixture, with the dominant pick highlighted. These are model projections for browsing —
            not the disciplined, tracked Bankers &amp; Value picks below.
          </p>
        </div>
        <MatchVerdicts verdicts={verdicts} />
      </section>

      <section className="space-y-3">
        <div>
          <h2 className="text-lg font-bold text-zinc-100">Goal rush — high-scoring matches</h2>
          <p className="text-sm text-zinc-500">
            Upcoming fixtures ranked by how likely they are to be a goal-fest, across the full
            over/under ladder (1.5 → 5.5). The &ldquo;top goals call&rdquo; is the highest line the
            model still makes more likely than not. Model projections, not tracked bets.
          </p>
        </div>
        <GoalRush matches={goalRush} />
      </section>

      <PicksLive initial={live} calibratedMarkets={calibrated} />
    </div>
  );
}
