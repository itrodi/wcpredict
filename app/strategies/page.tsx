import Link from "next/link";

import ModelSwitcher from "@/components/ModelSwitcher";
import { dateFmt, kickoffFmt, odds, pct } from "@/lib/format";
import { EXPERIMENTAL_MIN_N, marketLabel, selectionLabel } from "@/lib/markets";
import { resolveView } from "@/lib/pipeline";
import {
  buildStrategies,
  KELLY_CAP,
  KELLY_FRACTION,
  type Combo,
  type Matchday,
  type StrategyLeg,
  type StrategyRow,
} from "@/lib/strategies";
import { supabaseServer } from "@/lib/supabase/server";
import type { ModelScore } from "@/lib/types";

export const revalidate = 300;

const HORIZON_DAYS = 21;

const FOLD_LABELS: Record<number, string> = { 2: "Double", 3: "Treble", 4: "4-fold" };
const foldLabel = (size: number) => FOLD_LABELS[size] ?? `${size}-fold`;

const kellyPct = (k: number) => (k > 0 ? `${(k * 100).toFixed(1)}%` : "—");

/** Matchday staking strategies: per match day, the safest single picks and the
 * safest accumulators (doubles → 4-folds), each with a flat baseline and a
 * fractional-Kelly suggested stake. Safety-first: ranked by the probability that
 * every leg lands. Responsible-use messaging lives ON this page. */
export default async function StrategiesPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const sb = supabaseServer();
  const view = await resolveView(await searchParams, sb);

  let days: Matchday[] = [];
  if (sb) {
    const now = new Date().toISOString();
    const horizon = new Date(Date.now() + HORIZON_DAYS * 86400_000).toISOString();
    const [{ data }, { data: scores }] = await Promise.all([
      sb
        .from("match_predictions")
        .select(
          "pipeline, market, selection, probability, market_odds, fair_odds, fixture_id, " +
            "fixtures!inner(id, kickoff, status, home:teams!fixtures_home_id_fkey(name), away:teams!fixtures_away_id_fkey(name))"
        )
        .in("pipeline", [view.blendPipeline, view.modelPipeline])
        .neq("fixtures.status", "finished")
        .gte("fixtures.kickoff", now)
        .lte("fixtures.kickoff", horizon)
        .limit(4000),
      sb.from("model_scores").select("*").gte("n", EXPERIMENTAL_MIN_N).eq("beats_baseline", true),
    ]);
    const calibrated = ((scores as ModelScore[] | null) ?? []).map((s) => s.market);
    days = buildStrategies((data as unknown as StrategyRow[] | null) ?? [], view, calibrated);
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="mb-1 text-2xl font-bold text-zinc-100">Matchday strategies</h1>
          <p className="max-w-2xl text-sm text-zinc-500">
            For each upcoming match day, the <em>safest</em> picks from the {view.label.toLowerCase()}{" "}
            — ranked by the probability they land — plus the safest accumulators built from them. Each
            comes with a flat-stake baseline and a fractional-Kelly suggested stake.
          </p>
        </div>
        <ModelSwitcher active={view.view} />
      </div>

      <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 text-xs text-amber-200/90">
        <strong>Read this first.</strong> These are model projections, not guarantees — combining legs
        multiplies risk, and the combined probabilities assume the matches are independent (they aren&rsquo;t
        perfectly). Check the <Link href="/models" className="underline">model scoreboard</Link> before
        trusting any number. If you choose to bet: only what you can afford to lose, 18+/21+ where
        applicable, and see{" "}
        <a className="underline" href="https://www.begambleaware.org" rel="noreferrer" target="_blank">
          BeGambleAware
        </a>
        .
      </div>

      <div className="rounded-xl border border-pitch-700 bg-pitch-900 p-4 text-xs leading-relaxed text-zinc-500">
        <span className="font-semibold text-zinc-300">How to read this.</span> The safest{" "}
        <em>K</em>-fold is simply the {`K`} highest-probability single picks combined. <strong>Flat</strong>{" "}
        is a fixed 1-unit stake. <strong>Kelly</strong> is a fraction of your bankroll sized by edge
        ({KELLY_FRACTION === 0.25 ? "quarter" : `${KELLY_FRACTION}×`}-Kelly, capped at{" "}
        {Math.round(KELLY_CAP * 100)}%); it reads &ldquo;—&rdquo; when the price carries no edge over the
        model&rsquo;s fair odds, in which case treat it as a flat-only model lean. Odds marked{" "}
        <span className="italic">fair</span> have no book line yet — they&rsquo;re the model&rsquo;s own price.
      </div>

      {days.length === 0 ? (
        <p className="rounded-xl border border-pitch-700 bg-pitch-900 p-6 text-sm text-zinc-500">
          No strategies to show — either there are no upcoming fixtures with predictions in the next{" "}
          {HORIZON_DAYS} days, or the engine hasn&rsquo;t run yet.
        </p>
      ) : (
        <div className="space-y-8">
          {days.map((day) => (
            <MatchdayBlock key={day.date} day={day} />
          ))}
        </div>
      )}
    </div>
  );
}

function MatchdayBlock({ day }: { day: Matchday }) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-pitch-700 pb-2">
        <h2 className="text-lg font-bold text-zinc-100">{dateFmt(`${day.date}T00:00:00Z`)}</h2>
        <span className="text-xs text-zinc-600">
          {day.legCount} qualifying {day.legCount === 1 ? "match" : "matches"}
        </span>
      </div>

      {day.combos.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {day.combos.map((combo) => (
            <ComboCard key={combo.size} combo={combo} />
          ))}
        </div>
      )}

      <div className="overflow-x-auto rounded-xl border border-pitch-700">
        <table className="w-full text-sm">
          <caption className="bg-pitch-800 px-3 py-2 text-left text-xs uppercase tracking-wide text-zinc-500">
            Safest single picks
          </caption>
          <thead className="bg-pitch-800/60 text-left text-xs uppercase tracking-wide text-zinc-500">
            <tr>
              <th className="px-3 py-2">Match</th>
              <th className="px-3 py-2">Market · pick</th>
              <th className="px-3 py-2 text-right">Prob</th>
              <th className="px-3 py-2 text-right">Odds</th>
              <th className="px-3 py-2 text-right">Flat</th>
              <th className="px-3 py-2 text-right">Kelly</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-pitch-800 bg-pitch-900">
            {day.singles.map((leg) => (
              <tr key={`${leg.fixtureId}-${leg.market}-${leg.selection}`} className="hover:bg-pitch-800/60">
                <td className="px-3 py-2">
                  <Link href={`/matches/${leg.fixtureId}`} className="text-zinc-100 hover:text-accent">
                    {leg.home} v {leg.away}
                  </Link>
                  <div className="text-xs text-zinc-600">{kickoffFmt(leg.kickoff)}</div>
                </td>
                <td className="px-3 py-2 text-zinc-300">
                  {marketLabel(leg.market)} · {selectionLabel(leg.market, leg.selection)}
                </td>
                <td className="px-3 py-2 text-right font-mono font-semibold text-accent">
                  {pct(leg.probability)}
                </td>
                <td className="px-3 py-2 text-right font-mono text-zinc-400">
                  {odds(leg.odds)}
                  {leg.oddsIsFair && <span className="ml-1 text-[10px] italic text-zinc-600">fair</span>}
                </td>
                <td className="px-3 py-2 text-right font-mono text-zinc-400">1u</td>
                <td className="px-3 py-2 text-right font-mono text-zinc-200">{kellyPct(leg.kelly)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ComboCard({ combo }: { combo: Combo }) {
  return (
    <div className="flex flex-col rounded-xl border border-pitch-700 bg-pitch-900 p-4">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-zinc-100">{foldLabel(combo.size)}</h3>
        <span className="rounded-full bg-accent/15 px-2 py-0.5 font-mono text-xs font-semibold text-accent">
          {pct(combo.combinedProbability)}
        </span>
      </div>

      <ul className="mb-3 space-y-1.5 text-xs">
        {combo.legs.map((leg) => (
          <ComboLeg key={`${leg.fixtureId}-${leg.market}-${leg.selection}`} leg={leg} />
        ))}
      </ul>

      <dl className="mt-auto grid grid-cols-3 gap-2 border-t border-pitch-800 pt-3 text-center text-xs">
        <Stat label="Odds" value={odds(combo.combinedOdds)} />
        <Stat label="Flat" value="1u" />
        <Stat label="Kelly" value={kellyPct(combo.kelly)} />
      </dl>
    </div>
  );
}

function ComboLeg({ leg }: { leg: StrategyLeg }) {
  return (
    <li className="flex items-baseline justify-between gap-2">
      <Link href={`/matches/${leg.fixtureId}`} className="truncate text-zinc-300 hover:text-accent">
        {leg.home} v {leg.away}
        <span className="text-zinc-500">
          {" "}
          — {selectionLabel(leg.market, leg.selection)}
        </span>
      </Link>
      <span className="shrink-0 font-mono text-zinc-500">{pct(leg.probability)}</span>
    </li>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-zinc-600">{label}</dt>
      <dd className="font-mono font-semibold text-zinc-200">{value}</dd>
    </div>
  );
}
