import { supabaseServer } from "@/lib/supabase/server";
import type { OpsStatus } from "@/lib/types";

export const revalidate = 0; // health checks should never be stale

type XTeam = { team_id: number; fd_id: string | null; statsapi_id: string | null };
type XFix = { fixture_id: number; fd_id: string | null; statsapi_id: string | null };

/** Internal health page (spec v4.1 §1.2/1.4): reads the worker-written
 * ops_status rows (fixture counts per vendor, unmapped vendor names, credits,
 * last refresh) plus the live xmap audit. */
export default async function HealthPage() {
  const sb = supabaseServer();
  if (!sb) return <p className="text-sm text-zinc-500">Supabase not configured.</p>;

  const [{ data: ops }, { data: teams }, { data: xteams }, { data: fixtures }, { data: xfix }] =
    await Promise.all([
      sb.from("ops_status").select("*"),
      sb.from("teams").select("id, name, slug"),
      sb.from("xmap_teams").select("*"),
      sb
        .from("fixtures")
        .select("id, kickoff, stage, home:teams!fixtures_home_id_fkey(name), away:teams!fixtures_away_id_fkey(name)"),
      sb.from("xmap_fixtures").select("*"),
    ]);

  const status = new Map(((ops as OpsStatus[] | null) ?? []).map((o) => [o.key, o]));
  const v = (key: string) => status.get(key)?.value as Record<string, unknown> | undefined;

  const fdCount = v("fixture_count_fd");
  const saCount = v("fixture_count_statsapi");
  const coverage = v("pipeline_a_coverage");
  const unmapped = (v("unmapped_teams")?.statsapi as string[] | undefined) ?? [];
  const credits = v("odds_credits_remaining")?.remaining;
  const lastRefresh = v("last_refresh")?.at as string | undefined;
  const saOdds = v("statsapi_odds");
  const missingOdds = (v("fixtures_missing_odds")?.fixture_ids as number[] | undefined) ?? [];

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

  const expected = (fdCount?.expected as number) ?? 104;
  const fdOk = (fdCount?.ingested as number) >= expected;
  const saOk = (saCount?.ingested as number) >= expected;
  const allOk =
    fdOk && saOk && unmapped.length === 0 && unmappedTeams.length === 0 && unmappedFixtures.length === 0;

  const Counter = ({ label, got, ok }: { label: string; got: unknown; ok: boolean }) => (
    <div className="rounded-lg border border-pitch-700 bg-pitch-800 p-3">
      <div className="text-xs text-zinc-500">{label}</div>
      <div className={`font-mono text-xl font-bold ${ok ? "text-accent" : "text-amber-400"}`}>
        {String(got ?? "?")}/{expected}
      </div>
    </div>
  );

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-zinc-100">Pipeline health</h1>

      <div
        className={`rounded-xl border p-4 text-sm ${
          allOk
            ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-300"
            : "border-amber-500/30 bg-amber-500/5 text-amber-300"
        }`}
      >
        {allOk
          ? `All ${expected} fixtures ingested by both vendors and fully mapped.`
          : "Gaps detected — details below."}
        <div className="mt-1 text-xs opacity-70">
          Last refresh: {lastRefresh ? new Date(lastRefresh).toUTCString() : "never"} · Odds
          credits remaining: {String(credits ?? "?")} · TheStatsAPI odds endpoint:{" "}
          {saOdds ? (saOdds.available ? "available" : `excluded (HTTP ${String(saOdds.status_code)})`) : "not probed"}
        </div>
      </div>

      {/* per-vendor diagnostic block (spec v4.1 §1.2) */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Counter label="football-data.org fixtures" got={fdCount?.ingested} ok={fdOk} />
        <Counter label="TheStatsAPI fixtures mapped" got={saCount?.ingested} ok={saOk} />
        <Counter
          label="fixtures in DB"
          got={coverage?.fixtures}
          ok={(coverage?.fixtures as number) >= expected}
        />
        <div className="rounded-lg border border-pitch-700 bg-pitch-800 p-3">
          <div className="text-xs text-zinc-500">fixtures missing odds (7d)</div>
          <div className={`font-mono text-xl font-bold ${missingOdds.length === 0 ? "text-accent" : "text-amber-400"}`}>
            {missingOdds.length}
          </div>
        </div>
      </div>

      {unmapped.length > 0 && (
        <section className="rounded-xl border border-amber-500/30 bg-pitch-900 p-4">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-amber-400">
            Unmapped vendor names (copy-paste these into engine/aliases.py)
          </h2>
          <ul className="space-y-1 font-mono text-sm text-zinc-200">
            {unmapped.map((name) => (
              <li key={name}>&quot;{name}&quot;</li>
            ))}
          </ul>
        </section>
      )}

      {unmappedTeams.length > 0 && (
        <section className="rounded-xl border border-pitch-700 bg-pitch-900 p-4">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-zinc-400">
            Teams missing TheStatsAPI id
          </h2>
          <ul className="space-y-1 text-sm text-zinc-300">
            {unmappedTeams.map((t) => (
              <li key={t.id} className="font-mono">
                #{t.id} {t.name}
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
