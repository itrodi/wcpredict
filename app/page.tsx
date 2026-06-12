import Link from "next/link";

import FixtureCard from "@/components/FixtureCard";
import ModelSwitcher from "@/components/ModelSwitcher";
import { mergeViewRows, resolveView } from "@/lib/pipeline";
import { supabaseServer } from "@/lib/supabase/server";
import type { Fixture, MatchPrediction } from "@/lib/types";

export const revalidate = 300; // 5 min keeps first paint fresh; Realtime handles the rest

const FIXTURE_SELECT =
  "*, home:teams!fixtures_home_id_fkey(name,slug), away:teams!fixtures_away_id_fkey(name,slug)";

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const sb = supabaseServer();
  const view = await resolveView(await searchParams, sb);
  let upcoming: Fixture[] = [];
  let recent: Fixture[] = [];
  let predictions: MatchPrediction[] = [];

  if (sb) {
    const [{ data: up }, { data: rec }] = await Promise.all([
      sb
        .from("fixtures")
        .select(FIXTURE_SELECT)
        .neq("status", "finished")
        .order("kickoff", { ascending: true })
        .limit(12),
      sb
        .from("fixtures")
        .select(FIXTURE_SELECT)
        .eq("status", "finished")
        .order("kickoff", { ascending: false })
        .limit(6),
    ]);
    upcoming = (up as Fixture[] | null) ?? [];
    recent = (rec as Fixture[] | null) ?? [];
    if (upcoming.length > 0) {
      const { data: preds } = await sb
        .from("match_predictions")
        .select("*")
        .in("pipeline", [view.blendPipeline, view.modelPipeline])
        .eq("market", "1x2")
        .in(
          "fixture_id",
          upcoming.map((f) => f.id)
        );
      predictions = (preds as MatchPrediction[] | null) ?? [];
    }
  }

  return (
    <div className="space-y-10">
      <section>
        <div className="mb-1 flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-bold text-zinc-100">Upcoming matches</h1>
          <ModelSwitcher active={view.view} />
        </div>
        <p className="mb-5 text-sm text-zinc-500">
          Model probabilities, refreshed by the worker every few hours (hourly on matchdays).
        </p>
        {upcoming.length === 0 ? (
          <p className="rounded-xl border border-pitch-700 bg-pitch-900 p-6 text-sm text-zinc-500">
            No fixtures yet. Run the database migrations + seed, then trigger the{" "}
            <code className="text-emerald-300">refresh</code> GitHub Actions workflow to ingest the schedule.
          </p>
        ) : (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              {upcoming.map((f) => (
                <FixtureCard
                  key={f.id}
                  fixture={f}
                  predictions={mergeViewRows(
                    predictions.filter((p) => p.fixture_id === f.id),
                    view
                  )}
                />
              ))}
            </div>
            <div className="mt-5">
              <Link
                href="/fixtures"
                className="inline-flex items-center gap-1 rounded-lg border border-pitch-700 bg-pitch-900 px-4 py-2 text-sm font-medium text-emerald-300 transition hover:border-accent/50"
              >
                All fixtures →
              </Link>
            </div>
          </>
        )}
      </section>

      {recent.length > 0 && (
        <section>
          <h2 className="mb-4 text-xl font-bold text-zinc-100">Recent results</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {recent.map((f) => (
              <FixtureCard key={f.id} fixture={f} predictions={[]} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
