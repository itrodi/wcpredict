import Link from "next/link";
import { notFound } from "next/navigation";

import LineMovementChart from "@/components/LineMovementChart";
import LineupsPanel from "@/components/LineupsPanel";
import MatchMarkets from "@/components/MatchMarkets";
import ModelSwitcher from "@/components/ModelSwitcher";
import StatsPanel from "@/components/StatsPanel";
import { EXPERIMENTAL_MIN_N, kickoffFmt, STAGE_LABELS } from "@/lib/format";
import { basePipeline, resolvePipeline } from "@/lib/pipeline";
import { supabaseServer } from "@/lib/supabase/server";
import type { Fixture, Lineup, MatchPrediction, MatchStat, ModelScore, OddsSnapshot } from "@/lib/types";

export const revalidate = 300;

export default async function MatchPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const { id } = await params;
  const fixtureId = Number(id);
  if (!Number.isInteger(fixtureId)) notFound();

  const sb = supabaseServer();
  if (!sb) notFound();
  const sp = await searchParams;
  const pipeline = await resolvePipeline(sp, sb);

  const { data: fixture } = await sb
    .from("fixtures")
    .select(
      "*, home:teams!fixtures_home_id_fkey(name,slug), away:teams!fixtures_away_id_fkey(name,slug)"
    )
    .eq("id", fixtureId)
    .maybeSingle();
  if (!fixture) notFound();
  const f = fixture as Fixture;

  // Blends only carry 1X2 rows; merge the base model's other markets underneath
  // so switching to a blend never empties the page. Blend wins per (market, selection).
  const pipelines = pipeline.startsWith("blend_")
    ? [pipeline, basePipeline(pipeline)]
    : [pipeline];
  const [{ data: preds }, { data: scores }, { data: stats }, { data: lineups }, { data: snaps }] =
    await Promise.all([
      sb.from("match_predictions").select("*").eq("fixture_id", fixtureId).in("pipeline", pipelines),
      sb.from("model_scores").select("*").gte("n", EXPERIMENTAL_MIN_N),
      sb.from("match_stats").select("*").eq("fixture_id", fixtureId),
      sb.from("lineups").select("*").eq("fixture_id", fixtureId),
      sb
        .from("odds_snapshots")
        .select("fixture_id, bookmaker, market, selection, decimal_odds, fetched_at, id")
        .eq("fixture_id", fixtureId)
        .eq("market", "h2h")
        .order("fetched_at", { ascending: true })
        .limit(2000),
    ]);

  const all = (preds as MatchPrediction[] | null) ?? [];
  const merged = new Map<string, MatchPrediction>();
  for (const p of pipelines.slice().reverse()) {
    for (const r of all.filter((r) => r.pipeline === p)) {
      merged.set(`${r.market}:${r.selection}`, r);
    }
  }
  const initial = [...merged.values()];
  const calibrated = ((scores as ModelScore[] | null) ?? []).map((s) => s.market);

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-pitch-700 bg-pitch-900 p-6">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs text-zinc-500">
          <span>
            {STAGE_LABELS[f.stage] ?? f.stage}
            {f.group_code ? ` · Group ${f.group_code}` : ""}
            {f.venue ? ` · ${f.venue}` : ""}
          </span>
          <Link
            href={`/matches/${fixtureId}/compare`}
            className="rounded-md border border-pitch-700 px-2.5 py-1 text-emerald-300 hover:border-accent/50"
          >
            Compare models →
          </Link>
        </div>
        <div className="flex items-center justify-between gap-4">
          <h1 className="flex-1 text-xl font-bold text-zinc-100 sm:text-2xl">
            {f.home?.name ?? "TBD"}
          </h1>
          <div className="shrink-0 text-center">
            {f.status !== "scheduled" ? (
              <div className="font-mono text-3xl text-zinc-100">
                {f.home_goals ?? "-"} : {f.away_goals ?? "-"}
              </div>
            ) : (
              <div className="text-sm text-zinc-400">{kickoffFmt(f.kickoff)}</div>
            )}
            {f.status === "live" && <div className="text-xs font-semibold text-amber-400">LIVE</div>}
            {f.status === "finished" && <div className="text-xs text-zinc-500">Full time</div>}
          </div>
          <h1 className="flex-1 text-right text-xl font-bold text-zinc-100 sm:text-2xl">
            {f.away?.name ?? "TBD"}
          </h1>
        </div>
      </div>

      <ModelSwitcher active={pipeline} />

      <StatsPanel
        fixtureId={fixtureId}
        homeTeamId={f.home_id}
        homeName={f.home?.name ?? "Home"}
        awayName={f.away?.name ?? "Away"}
        initial={(stats as MatchStat[] | null) ?? []}
        live={f.status === "live"}
      />

      {f.status !== "finished" && (
        <LineupsPanel
          homeTeamId={f.home_id}
          homeName={f.home?.name ?? "Home"}
          awayName={f.away?.name ?? "Away"}
          lineups={(lineups as Lineup[] | null) ?? []}
        />
      )}

      <LineMovementChart
        snapshots={(snaps as OddsSnapshot[] | null) ?? []}
        homeName={f.home?.name ?? "Home"}
        awayName={f.away?.name ?? "Away"}
      />

      {f.status === "finished" ? (
        <p className="text-sm text-zinc-500">
          This match has finished — predictions below are the final pre-match numbers, kept for the
          calibration record.
        </p>
      ) : null}
      <MatchMarkets
        fixtureId={fixtureId}
        pipeline={pipeline}
        initial={initial}
        calibratedMarkets={calibrated}
      />
    </div>
  );
}
