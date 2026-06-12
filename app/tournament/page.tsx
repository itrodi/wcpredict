import ChampionChart from "@/components/ChampionChart";
import ModelSwitcher from "@/components/ModelSwitcher";
import OddsTable from "@/components/OddsTable";
import { resolveView } from "@/lib/pipeline";
import { supabaseServer } from "@/lib/supabase/server";
import type { TournamentOdds } from "@/lib/types";

export const revalidate = 300;

export default async function Tournament({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const sb = supabaseServer();
  const view = await resolveView(await searchParams, sb);

  let rows: TournamentOdds[] = [];
  if (sb) {
    const { data } = await sb
      .from("tournament_odds")
      .select(
        "id, pipeline, team_id, advance_grp, reach_qf, reach_sf, reach_final, champion, n_sims, model_version, computed_at, teams(name, slug, elo, group_code)"
      )
      .eq("pipeline", view.modelPipeline)
      .order("champion", { ascending: false });
    rows = (data as unknown as TournamentOdds[] | null) ?? [];
  }

  const chart = rows
    .filter((r) => (r.champion ?? 0) > 0)
    .slice(0, 10)
    .map((r) => ({ name: r.teams?.name ?? `#${r.team_id}`, champion: r.champion ?? 0 }));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="mb-1 text-2xl font-bold text-zinc-100">Tournament simulator</h1>
          <p className="text-sm text-zinc-500">
            Advancement and title probabilities from Monte Carlo simulation of the remaining
            tournament, updated live as the worker recomputes.
          </p>
        </div>
        <ModelSwitcher active={view.view} />
      </div>
      <ChampionChart data={chart} />
      <OddsTable initial={rows} pipeline={view.modelPipeline} label={view.label} />
    </div>
  );
}
