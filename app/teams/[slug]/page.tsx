import { notFound } from "next/navigation";

import FixtureCard from "@/components/FixtureCard";
import ModelSwitcher from "@/components/ModelSwitcher";
import ProbabilityBar from "@/components/ProbabilityBar";
import UpdatedBadge from "@/components/UpdatedBadge";
import { basePipeline, PIPELINE_LABELS, resolvePipeline } from "@/lib/pipeline";
import { supabaseServer } from "@/lib/supabase/server";
import type { Fixture, MatchPrediction, Team, TournamentOdds } from "@/lib/types";

export const revalidate = 300;

const FIXTURE_SELECT =
  "*, home:teams!fixtures_home_id_fkey(name,slug), away:teams!fixtures_away_id_fkey(name,slug)";

export default async function TeamPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const { slug } = await params;
  const sb = supabaseServer();
  if (!sb) notFound();
  const pipeline = await resolvePipeline(await searchParams, sb);
  const simPipeline = basePipeline(pipeline); // sims exist per model pipeline only

  const { data: team } = await sb.from("teams").select("*").eq("slug", slug).maybeSingle();
  if (!team) notFound();
  const t = team as Team;

  const [{ data: oddsRow }, { data: fx }] = await Promise.all([
    sb
      .from("tournament_odds")
      .select("*")
      .eq("team_id", t.id)
      .eq("pipeline", simPipeline) // pipeline filter mandatory post-v4 (spec §10)
      .maybeSingle(),
    sb
      .from("fixtures")
      .select(FIXTURE_SELECT)
      .or(`home_id.eq.${t.id},away_id.eq.${t.id}`)
      .order("kickoff", { ascending: true }),
  ]);
  const odds = oddsRow as TournamentOdds | null;
  const fixtures = (fx as Fixture[] | null) ?? [];
  const upcomingIds = fixtures.filter((f) => f.status !== "finished").map((f) => f.id);

  let predictions: MatchPrediction[] = [];
  if (upcomingIds.length > 0) {
    const { data: preds } = await sb
      .from("match_predictions")
      .select("*")
      .eq("pipeline", pipeline)
      .eq("market", "1x2")
      .in("fixture_id", upcomingIds);
    predictions = (preds as MatchPrediction[] | null) ?? [];
  }

  return (
    <div className="space-y-8">
      <div className="rounded-xl border border-pitch-700 bg-pitch-900 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold text-zinc-100">{t.name}</h1>
            <p className="mt-1 text-sm text-zinc-500">
              {t.group_code ? `Group ${t.group_code} · ` : ""}
              {t.confederation ?? ""} · Elo <span className="font-mono text-zinc-300">{t.elo}</span>
            </p>
          </div>
          <div className="flex flex-col items-end gap-2">
            <ModelSwitcher active={pipeline} />
            {odds && (
              <UpdatedBadge computedAt={odds.computed_at} label={PIPELINE_LABELS[simPipeline]} />
            )}
          </div>
        </div>
        {odds && (
          <div className="mt-5 space-y-2">
            <ProbabilityBar label="Advance" probability={odds.advance_grp ?? 0} />
            <ProbabilityBar label="Quarter-final" probability={odds.reach_qf ?? 0} />
            <ProbabilityBar label="Semi-final" probability={odds.reach_sf ?? 0} />
            <ProbabilityBar label="Final" probability={odds.reach_final ?? 0} />
            <ProbabilityBar label="Champion" probability={odds.champion ?? 0} highlight />
          </div>
        )}
      </div>

      <section>
        <h2 className="mb-4 text-xl font-bold text-zinc-100">Path through the tournament</h2>
        {fixtures.length === 0 ? (
          <p className="text-sm text-zinc-500">No fixtures ingested yet for this team.</p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            {fixtures.map((f) => (
              <FixtureCard
                key={f.id}
                fixture={f}
                predictions={predictions.filter((p) => p.fixture_id === f.id)}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
