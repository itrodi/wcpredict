import { supabaseServer } from "@/lib/supabase/server";

export const revalidate = 0; // health checks should never be stale

type XTeam = { team_id: number; fd_id: string | null; statsapi_id: string | null };
type XFix = { fixture_id: number; fd_id: string | null; statsapi_id: string | null };

/** Internal health page (spec v4 §4): cross-vendor identity-mapping discrepancies.
 * Every WC fixture must carry both fd_id and statsapi_id; anything listed here is
 * a logged discrepancy from the worker's CI-style assertion. */
export default async function HealthPage() {
  const sb = supabaseServer();
  if (!sb) return <p className="text-sm text-zinc-500">Supabase not configured.</p>;

  const [{ data: teams }, { data: xteams }, { data: fixtures }, { data: xfix }, { data: scores }] =
    await Promise.all([
      sb.from("teams").select("id, name, slug"),
      sb.from("xmap_teams").select("*"),
      sb
        .from("fixtures")
        .select("id, kickoff, stage, home:teams!fixtures_home_id_fkey(name), away:teams!fixtures_away_id_fkey(name)"),
      sb.from("xmap_fixtures").select("*"),
      sb.from("model_scores").select("computed_at").order("computed_at", { ascending: false }).limit(1),
    ]);

  const teamList = teams ?? [];
  const xt = new Map(((xteams as XTeam[] | null) ?? []).map((x) => [x.team_id, x]));
  const xf = new Map(((xfix as XFix[] | null) ?? []).map((x) => [x.fixture_id, x]));

  const unmappedTeams = teamList.filter((t) => !xt.get(t.id)?.statsapi_id);
  const fixtureList = (fixtures ?? []) as unknown as {
    id: number;
    kickoff: string;
    stage: string;
    home: { name: string } | null;
    away: { name: string } | null;
  }[];
  const unmappedFixtures = fixtureList.filter(
    (f) => f.home && f.away && (!xf.get(f.id)?.fd_id || !xf.get(f.id)?.statsapi_id)
  );

  const ok = unmappedTeams.length === 0 && unmappedFixtures.length === 0;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-zinc-100">Pipeline health</h1>

      <div
        className={`rounded-xl border p-4 text-sm ${
          ok
            ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-300"
            : "border-amber-500/30 bg-amber-500/5 text-amber-300"
        }`}
      >
        {ok
          ? "All teams and fixtures carry both vendor ids — cross-vendor mapping is healthy."
          : `${unmappedTeams.length} teams and ${unmappedFixtures.length} fixtures are missing a vendor id.`}
        <div className="mt-1 text-xs opacity-70">
          Last model_scores write:{" "}
          {scores?.[0]?.computed_at ? new Date(scores[0].computed_at).toUTCString() : "never"}
        </div>
      </div>

      {unmappedTeams.length > 0 && (
        <section className="rounded-xl border border-pitch-700 bg-pitch-900 p-4">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-zinc-400">
            Teams missing TheStatsAPI id
          </h2>
          <ul className="space-y-1 text-sm text-zinc-300">
            {unmappedTeams.map((t) => (
              <li key={t.id} className="font-mono">
                #{t.id} {t.name}{" "}
                <span className="text-zinc-600">— add an alias in engine/aliases.py if it&rsquo;s a name-drift case</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {unmappedFixtures.length > 0 && (
        <section className="rounded-xl border border-pitch-700 bg-pitch-900 p-4">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-zinc-400">
            Fixtures missing a vendor id
          </h2>
          <ul className="space-y-1 text-sm text-zinc-300">
            {unmappedFixtures.map((f) => {
              const x = xf.get(f.id);
              return (
                <li key={f.id} className="font-mono">
                  #{f.id} {f.home?.name} v {f.away?.name} ({f.stage}) — fd:{x?.fd_id ?? "∅"} statsapi:
                  {x?.statsapi_id ?? "∅"}
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
