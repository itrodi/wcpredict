import { notFound } from "next/navigation";

import MatchMarkets from "@/components/MatchMarkets";
import { kickoffFmt, STAGE_LABELS } from "@/lib/format";
import { supabaseServer } from "@/lib/supabase/server";
import type { Fixture, MatchPrediction } from "@/lib/types";

export const revalidate = 300;

export default async function MatchPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const fixtureId = Number(id);
  if (!Number.isInteger(fixtureId)) notFound();

  const sb = supabaseServer();
  if (!sb) notFound();

  const { data: fixture } = await sb
    .from("fixtures")
    .select(
      "*, home:teams!fixtures_home_id_fkey(name,slug), away:teams!fixtures_away_id_fkey(name,slug)"
    )
    .eq("id", fixtureId)
    .maybeSingle();
  if (!fixture) notFound();
  const f = fixture as Fixture;

  const { data: preds } = await sb
    .from("match_predictions")
    .select("*")
    .eq("fixture_id", fixtureId);
  const initial = (preds as MatchPrediction[] | null) ?? [];

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-pitch-700 bg-pitch-900 p-6">
        <div className="mb-3 text-xs text-zinc-500">
          {STAGE_LABELS[f.stage] ?? f.stage}
          {f.group_code ? ` · Group ${f.group_code}` : ""}
          {f.venue ? ` · ${f.venue}` : ""}
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

      {f.status === "finished" ? (
        <p className="text-sm text-zinc-500">
          This match has finished — predictions below are the final pre-match numbers, kept for the
          calibration record.
        </p>
      ) : null}
      <MatchMarkets fixtureId={fixtureId} initial={initial} />
    </div>
  );
}
