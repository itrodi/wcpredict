import Link from "next/link";

import FixtureCard from "@/components/FixtureCard";
import ModelSwitcher from "@/components/ModelSwitcher";
import TeamSelect from "@/components/TeamSelect";
import { dateFmt, STAGE_LABELS } from "@/lib/format";
import { mergeViewRows, resolveView } from "@/lib/pipeline";
import { supabaseServer } from "@/lib/supabase/server";
import type { Fixture, MatchPrediction } from "@/lib/types";

export const revalidate = 300;

const FIXTURE_SELECT =
  "*, home:teams!fixtures_home_id_fkey(name,slug), away:teams!fixtures_away_id_fkey(name,slug)";

const KNOCKOUT_ORDER = ["R32", "R16", "QF", "SF", "3P", "F"];

type GroupBy = "group" | "matchday" | "all";

/** ALL tournament matches (spec v4.1 §1.3) — the complete-fixtures view that
 * makes "missing matches" visibly not missing. */
export default async function FixturesPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const sb = supabaseServer();
  const sp = await searchParams;
  const view = await resolveView(sp, sb);
  const raw = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);
  const by = (["group", "matchday", "all"].includes(raw(sp.by) ?? "") ? raw(sp.by) : "group") as GroupBy;
  const teamSlug = raw(sp.team) ?? null;

  let fixtures: Fixture[] = [];
  let predictions: MatchPrediction[] = [];
  let teams: { slug: string; name: string }[] = [];
  if (sb) {
    const [{ data: fx }, { data: tm }] = await Promise.all([
      sb.from("fixtures").select(FIXTURE_SELECT).order("kickoff", { ascending: true }),
      sb.from("teams").select("slug, name").order("name"),
    ]);
    fixtures = (fx as Fixture[] | null) ?? [];
    teams = tm ?? [];
    if (teamSlug) {
      fixtures = fixtures.filter(
        (f) => f.home?.slug === teamSlug || f.away?.slug === teamSlug
      );
    }
    const upcomingIds = fixtures.filter((f) => f.status !== "finished").map((f) => f.id);
    for (let i = 0; i < upcomingIds.length; i += 100) {
      const { data: preds } = await sb
        .from("match_predictions")
        .select("*")
        .in("pipeline", [view.blendPipeline, view.modelPipeline])
        .eq("market", "1x2")
        .in("fixture_id", upcomingIds.slice(i, i + 100));
      predictions = predictions.concat((preds as MatchPrediction[] | null) ?? []);
    }
  }

  // build sections per the selected grouping
  let sections: { title: string; href?: string; fixtures: Fixture[] }[] = [];
  if (by === "group" && !teamSlug) {
    const groups = [...new Set(
      fixtures.filter((f) => f.stage === "group" && f.group_code).map((f) => f.group_code as string)
    )].sort();
    sections = groups.map((g) => ({
      title: `Group ${g}`,
      href: `/groups/${g}`,
      fixtures: fixtures.filter((f) => f.stage === "group" && f.group_code === g),
    }));
    for (const stage of KNOCKOUT_ORDER) {
      const list = fixtures.filter((f) => f.stage === stage);
      if (list.length) sections.push({ title: STAGE_LABELS[stage] ?? stage, fixtures: list });
    }
  } else if (by === "matchday" && !teamSlug) {
    const days = [...new Set(fixtures.map((f) => f.kickoff.slice(0, 10)))].sort();
    sections = days.map((d) => ({
      title: dateFmt(`${d}T12:00:00Z`),
      fixtures: fixtures.filter((f) => f.kickoff.slice(0, 10) === d),
    }));
  } else {
    sections = [{ title: teamSlug ? `${fixtures.length} matches` : "All matches", fixtures }];
  }

  const pill = (label: string, target: GroupBy) => {
    const q = new URLSearchParams();
    q.set("view", view.view);
    q.set("by", target);
    return (
      <Link
        key={target}
        href={`/fixtures?${q.toString()}`}
        className={`rounded-md px-3 py-1 text-xs transition ${
          by === target && !teamSlug
            ? "bg-accent/15 font-semibold text-accent"
            : "text-zinc-400 hover:text-zinc-200"
        }`}
      >
        {label}
      </Link>
    );
  };

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="mb-1 text-2xl font-bold text-zinc-100">All fixtures</h1>
          <p className="text-sm text-zinc-500">
            Every match of the tournament — {fixtures.length} fixtures
            {teamSlug ? ` for ${teams.find((t) => t.slug === teamSlug)?.name ?? teamSlug}` : ""}.
          </p>
        </div>
        <ModelSwitcher active={view.view} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex gap-1 rounded-lg border border-pitch-700 bg-pitch-900 p-1">
          {pill("All", "all")}
          {pill("By group", "group")}
          {pill("By matchday", "matchday")}
        </div>
        <TeamSelect teams={teams} selected={teamSlug} />
      </div>

      {fixtures.length === 0 ? (
        <p className="rounded-xl border border-pitch-700 bg-pitch-900 p-6 text-sm text-zinc-500">
          No fixtures ingested yet — trigger the refresh workflow.
        </p>
      ) : (
        sections.map((s) => (
          <section key={s.title}>
            <h2 className="mb-3 text-lg font-bold text-zinc-100">
              {s.href ? (
                <Link href={s.href} className="hover:text-accent">
                  {s.title} →
                </Link>
              ) : (
                s.title
              )}
            </h2>
            <div className="grid gap-4 sm:grid-cols-2">
              {s.fixtures.map((f) => (
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
        ))
      )}
    </div>
  );
}
