import Link from "next/link";

import ModelSwitcher from "@/components/ModelSwitcher";
import {
  EXPERIMENTAL_MARKETS,
  EXPERIMENTAL_MIN_N,
  kickoffFmt,
  MARKET_LABELS,
  odds,
  pct,
  SELECTION_LABELS,
} from "@/lib/format";
import { resolvePipeline } from "@/lib/pipeline";
import { supabaseServer } from "@/lib/supabase/server";
import type { MatchPrediction, ModelScore } from "@/lib/types";

export const revalidate = 300;

type ValueRow = MatchPrediction & {
  fixtures: {
    id: number;
    kickoff: string;
    status: string;
    home: { name: string } | null;
    away: { name: string } | null;
  } | null;
};

/** Value finder (spec v4 §6.7): upcoming fixtures sorted by |edge| (model/blend
 * vs de-vigged market). Responsible-use messaging lives ON this page. */
export default async function ValuePage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const sb = supabaseServer();
  const pipeline = await resolvePipeline(await searchParams, sb);

  let rows: ValueRow[] = [];
  let calibrated: string[] = [];
  if (sb) {
    const [{ data }, { data: scores }] = await Promise.all([
      sb
        .from("match_predictions")
        .select(
          "*, fixtures!inner(id, kickoff, status, home:teams!fixtures_home_id_fkey(name), away:teams!fixtures_away_id_fkey(name))"
        )
        .eq("pipeline", pipeline)
        .not("edge", "is", null)
        .neq("fixtures.status", "finished")
        .gte("fixtures.kickoff", new Date().toISOString()),
      sb.from("model_scores").select("*").gte("n", EXPERIMENTAL_MIN_N),
    ]);
    rows = ((data as unknown as ValueRow[] | null) ?? [])
      .sort((x, y) => Math.abs(Number(y.edge)) - Math.abs(Number(x.edge)))
      .slice(0, 25);
    calibrated = ((scores as ModelScore[] | null) ?? []).map((s) => s.market);
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="mb-1 text-2xl font-bold text-zinc-100">Value finder</h1>
          <p className="text-sm text-zinc-500">
            Upcoming selections where the chosen model most disagrees with the de-vigged market
            price, sorted by |edge|.
          </p>
        </div>
        <ModelSwitcher active={pipeline} />
      </div>

      <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 text-xs text-amber-200/90">
        <strong>Read this first.</strong> An &ldquo;edge&rdquo; is a disagreement between a model and the
        market — it is NOT a guaranteed profit, and most disagreements resolve in the market&rsquo;s
        favour. Check the <Link href="/models" className="underline">model scoreboard</Link> before
        trusting any number here. If you choose to bet: only what you can afford to lose, 18+/21+
        where applicable, and see{" "}
        <a className="underline" href="https://www.begambleaware.org" rel="noreferrer" target="_blank">
          BeGambleAware
        </a>
        .
      </div>

      {rows.length === 0 ? (
        <p className="rounded-xl border border-pitch-700 bg-pitch-900 p-6 text-sm text-zinc-500">
          No edges to show — either no upcoming fixtures have book odds yet, or the engine
          hasn&rsquo;t run since the last odds pull.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-pitch-700">
          <table className="w-full text-sm">
            <thead className="bg-pitch-800 text-left text-xs uppercase tracking-wide text-zinc-500">
              <tr>
                <th className="px-3 py-2.5">Match</th>
                <th className="px-3 py-2.5">Market · selection</th>
                <th className="px-3 py-2.5 text-right">Model</th>
                <th className="px-3 py-2.5 text-right">Book</th>
                <th className="px-3 py-2.5 text-right">Edge</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-pitch-800 bg-pitch-900">
              {rows.map((r) => {
                const experimental =
                  EXPERIMENTAL_MARKETS.has(r.market) && !calibrated.includes(r.market);
                return (
                  <tr key={r.id} className="hover:bg-pitch-800/60">
                    <td className="px-3 py-2">
                      <Link href={`/matches/${r.fixture_id}`} className="text-zinc-100 hover:text-accent">
                        {r.fixtures?.home?.name ?? "TBD"} v {r.fixtures?.away?.name ?? "TBD"}
                      </Link>
                      <div className="text-xs text-zinc-600">
                        {r.fixtures ? kickoffFmt(r.fixtures.kickoff) : ""}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-zinc-300">
                      {MARKET_LABELS[r.market] ?? r.market} ·{" "}
                      {SELECTION_LABELS[r.selection] ?? r.selection}
                      {experimental && (
                        <span className="ml-2 rounded-full border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-400">
                          experimental
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-zinc-200">
                      {pct(Number(r.probability))}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-zinc-400">
                      {odds(r.market_odds)}
                    </td>
                    <td
                      className={`px-3 py-2 text-right font-mono font-semibold ${
                        Number(r.edge) > 0 ? "text-accent" : "text-zinc-400"
                      }`}
                    >
                      {`${Number(r.edge) > 0 ? "+" : ""}${(Number(r.edge) * 100).toFixed(1)}%`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
