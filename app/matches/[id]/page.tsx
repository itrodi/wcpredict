import Link from "next/link";
import { notFound } from "next/navigation";

import InsightsCard, { buildInsights } from "@/components/InsightsCard";
import KeyPlayers from "@/components/KeyPlayers";
import LineMovementChart from "@/components/LineMovementChart";
import LineupsPanel from "@/components/LineupsPanel";
import MatchMarkets from "@/components/MatchMarkets";
import ModelSwitcher from "@/components/ModelSwitcher";
import StatsPanel from "@/components/StatsPanel";
import { kickoffFmt, STAGE_LABELS } from "@/lib/format";
import { EXPERIMENTAL_MIN_N } from "@/lib/markets";
import { type PlayerMatchStat } from "@/lib/playerDrivers";
import { mergeViewRows, resolveView } from "@/lib/pipeline";
import { supabaseServer } from "@/lib/supabase/server";
import type {
  Fixture,
  Lineup,
  MatchPrediction,
  MatchStat,
  ModelScore,
  OddsSnapshot,
  RefereeSignal,
  TeamSignal,
} from "@/lib/types";

export const revalidate = 300;

function biggestMove(snaps: OddsSnapshot[]) {
  const series = new Map<string, number[]>();
  for (const s of snaps) {
    const list = series.get(s.selection) ?? [];
    list.push(Number(s.decimal_odds));
    series.set(s.selection, list);
  }
  let best: { selection: string; from: number; to: number } | null = null;
  for (const [selection, odds] of series) {
    if (odds.length < 2) continue;
    const move = { selection, from: odds[0], to: odds[odds.length - 1] };
    if (!best || Math.abs(move.from - move.to) > Math.abs(best.from - best.to)) best = move;
  }
  return best;
}

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
  const view = await resolveView(await searchParams, sb);

  const { data: fixture } = await sb
    .from("fixtures")
    .select(
      "*, home:teams!fixtures_home_id_fkey(name,slug), away:teams!fixtures_away_id_fkey(name,slug)"
    )
    .eq("id", fixtureId)
    .maybeSingle();
  if (!fixture) notFound();
  const f = fixture as Fixture;

  const teamIds = [f.home_id, f.away_id].filter((x): x is number => x != null);
  const [
    { data: preds },
    { data: scores },
    { data: stats },
    { data: lineups },
    { data: snaps },
    { data: sigs },
    { data: refSig },
    { data: playerStats },
  ] = await Promise.all([
    sb
      .from("match_predictions")
      .select("*")
      .eq("fixture_id", fixtureId)
      .in("pipeline", [view.blendPipeline, view.modelPipeline]),
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
    teamIds.length
      ? sb.from("team_signals").select("*").in("team_id", teamIds)
      : Promise.resolve({ data: [] }),
    f.referee
      ? sb.from("referee_signals").select("*").eq("referee", f.referee).maybeSingle()
      : Promise.resolve({ data: null }),
    teamIds.length
      ? sb.from("player_match_stats").select("*").in("team_id", teamIds)
      : Promise.resolve({ data: [] }),
  ]);

  const initial = mergeViewRows((preds as MatchPrediction[] | null) ?? [], view);
  const calibrated = ((scores as ModelScore[] | null) ?? []).map((s) => s.market);
  const lineupRows = (lineups as Lineup[] | null) ?? [];
  const snapRows = (snaps as OddsSnapshot[] | null) ?? [];

  const insights = buildInsights({
    home: { teamId: f.home_id, name: f.home?.name ?? "Home" },
    away: { teamId: f.away_id, name: f.away?.name ?? "Away" },
    signals: (sigs as TeamSignal[] | null) ?? [],
    referee: (refSig as RefereeSignal | null) ?? null,
    lineups: lineupRows,
    movement: biggestMove(snapRows),
  });

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-pitch-700 bg-pitch-900 p-6">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs text-zinc-500">
          <span>
            {STAGE_LABELS[f.stage] ?? f.stage}
            {f.group_code ? (
              <>
                {" · "}
                <Link href={`/groups/${f.group_code}`} className="text-emerald-300 hover:underline">
                  Group {f.group_code}
                </Link>
              </>
            ) : null}
            {f.venue ? ` · ${f.venue}` : ""}
            {f.referee ? ` · Referee: ${f.referee}` : ""}
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

      <ModelSwitcher active={view.view} />

      <InsightsCard bullets={insights} />

      <KeyPlayers
        homeName={f.home?.name ?? "Home"}
        awayName={f.away?.name ?? "Away"}
        homeTeamId={f.home_id}
        awayTeamId={f.away_id}
        rows={(playerStats as PlayerMatchStat[] | null) ?? []}
      />

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
          lineups={lineupRows}
        />
      )}

      <LineMovementChart
        snapshots={snapRows}
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
        pipelines={[view.blendPipeline, view.modelPipeline]}
        label={view.label}
        initial={initial}
        calibratedMarkets={calibrated}
      />
    </div>
  );
}
