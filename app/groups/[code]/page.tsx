import Link from "next/link";
import { notFound } from "next/navigation";

import FixtureCard from "@/components/FixtureCard";
import ModelSwitcher from "@/components/ModelSwitcher";
import { pct } from "@/lib/format";
import { mergeViewRows, resolveView } from "@/lib/pipeline";
import { supabaseServer } from "@/lib/supabase/server";
import type { Fixture, MatchPrediction, Team, TournamentOdds } from "@/lib/types";

export const revalidate = 300;

const FIXTURE_SELECT =
  "*, home:teams!fixtures_home_id_fkey(name,slug), away:teams!fixtures_away_id_fkey(name,slug)";

type StandingRow = {
  team: Team;
  played: number;
  won: number;
  drawn: number;
  lost: number;
  gf: number;
  ga: number;
  pts: number;
  advance: number | null;
};

/** Group page (spec v4.1 §1.3 bonus): standings + the group's 6 matches +
 * each team's advance probability from the tournament sim. */
export default async function GroupPage({
  params,
  searchParams,
}: {
  params: Promise<{ code: string }>;
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const { code: raw } = await params;
  const code = raw.toUpperCase();
  if (!/^[A-L]$/.test(code)) notFound();

  const sb = supabaseServer();
  if (!sb) notFound();
  const view = await resolveView(await searchParams, sb);

  const [{ data: teams }, { data: fx }] = await Promise.all([
    sb.from("teams").select("*").eq("group_code", code).order("name"),
    sb
      .from("fixtures")
      .select(FIXTURE_SELECT)
      .eq("stage", "group")
      .eq("group_code", code)
      .order("kickoff", { ascending: true }),
  ]);
  const teamRows = (teams as Team[] | null) ?? [];
  const fixtures = (fx as Fixture[] | null) ?? [];
  if (teamRows.length === 0) notFound();

  const [{ data: tOdds }, { data: preds }] = await Promise.all([
    sb
      .from("tournament_odds")
      .select("team_id, advance_grp")
      .eq("pipeline", view.modelPipeline)
      .in("team_id", teamRows.map((t) => t.id)),
    fixtures.length
      ? sb
          .from("match_predictions")
          .select("*")
          .in("pipeline", [view.blendPipeline, view.modelPipeline])
          .eq("market", "1x2")
          .in("fixture_id", fixtures.map((f) => f.id))
      : Promise.resolve({ data: [] }),
  ]);
  const advance = new Map(
    ((tOdds as Pick<TournamentOdds, "team_id" | "advance_grp">[] | null) ?? []).map((o) => [
      o.team_id,
      o.advance_grp,
    ])
  );
  const predictions = (preds as MatchPrediction[] | null) ?? [];

  // standings from played group matches: points → goal diff → goals for
  const table = new Map<number, StandingRow>(
    teamRows.map((t) => [
      t.id,
      { team: t, played: 0, won: 0, drawn: 0, lost: 0, gf: 0, ga: 0, pts: 0, advance: advance.get(t.id) ?? null },
    ])
  );
  for (const f of fixtures) {
    if (f.status !== "finished" || f.home_goals == null || !f.home_id || !f.away_id) continue;
    const h = table.get(f.home_id);
    const a = table.get(f.away_id);
    if (!h || !a) continue;
    const hg = f.home_goals;
    const ag = f.away_goals ?? 0;
    h.played++; a.played++;
    h.gf += hg; h.ga += ag; a.gf += ag; a.ga += hg;
    if (hg > ag) { h.won++; a.lost++; h.pts += 3; }
    else if (hg < ag) { a.won++; h.lost++; a.pts += 3; }
    else { h.drawn++; a.drawn++; h.pts++; a.pts++; }
  }
  const standings = [...table.values()].sort(
    (x, y) => y.pts - x.pts || y.gf - y.ga - (x.gf - x.ga) || y.gf - x.gf || x.team.name.localeCompare(y.team.name)
  );

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="mb-1 text-2xl font-bold text-zinc-100">Group {code}</h1>
          <p className="text-sm text-zinc-500">
            <Link href="/fixtures" className="text-emerald-300 hover:underline">← all fixtures</Link>
            {" · "}top 2 advance directly; the 8 best third-placed teams join them in the Round of 32.
          </p>
        </div>
        <ModelSwitcher active={view.view} />
      </div>

      <div className="overflow-x-auto rounded-xl border border-pitch-700">
        <table className="w-full text-sm">
          <thead className="bg-pitch-800 text-left text-xs uppercase tracking-wide text-zinc-500">
            <tr>
              <th className="px-3 py-2.5">#</th>
              <th className="px-3 py-2.5">Team</th>
              <th className="px-3 py-2.5 text-right">P</th>
              <th className="px-3 py-2.5 text-right">W</th>
              <th className="px-3 py-2.5 text-right">D</th>
              <th className="px-3 py-2.5 text-right">L</th>
              <th className="px-3 py-2.5 text-right">GD</th>
              <th className="px-3 py-2.5 text-right">Pts</th>
              <th className="px-3 py-2.5 text-right">Advance</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-pitch-800 bg-pitch-900">
            {standings.map((r, i) => (
              <tr key={r.team.id} className={i < 2 ? "bg-emerald-500/5" : undefined}>
                <td className="px-3 py-2 text-zinc-600">{i + 1}</td>
                <td className="px-3 py-2">
                  <Link href={`/teams/${r.team.slug}`} className="font-medium text-zinc-100 hover:text-accent">
                    {r.team.name}
                  </Link>
                </td>
                <td className="px-3 py-2 text-right font-mono text-zinc-400">{r.played}</td>
                <td className="px-3 py-2 text-right font-mono text-zinc-400">{r.won}</td>
                <td className="px-3 py-2 text-right font-mono text-zinc-400">{r.drawn}</td>
                <td className="px-3 py-2 text-right font-mono text-zinc-400">{r.lost}</td>
                <td className="px-3 py-2 text-right font-mono text-zinc-300">
                  {r.gf - r.ga > 0 ? "+" : ""}{r.gf - r.ga}
                </td>
                <td className="px-3 py-2 text-right font-mono font-semibold text-zinc-100">{r.pts}</td>
                <td className="px-3 py-2 text-right font-mono text-accent">{pct(r.advance)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <section>
        <h2 className="mb-4 text-xl font-bold text-zinc-100">Matches</h2>
        <div className="grid gap-4 sm:grid-cols-2">
          {fixtures.map((f) => (
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
      </section>
    </div>
  );
}
