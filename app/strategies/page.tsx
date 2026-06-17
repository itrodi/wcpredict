import Link from "next/link";

import ModelSwitcher from "@/components/ModelSwitcher";
import { dateFmt, kickoffFmt, odds, pct } from "@/lib/format";
import { EXPERIMENTAL_MIN_N, marketLabel, selectionLabel } from "@/lib/markets";
import { resolveView } from "@/lib/pipeline";
import {
  buildRiskTiers,
  KELLY_CAP,
  KELLY_FRACTION,
  type Combo,
  type MatchdayTiers,
  type RiskTier,
  type SameGameCombo,
  type StrategyLeg,
  type StrategyRow,
  type TierBoard,
} from "@/lib/strategies";
import { supabaseServer } from "@/lib/supabase/server";
import type { ModelScore } from "@/lib/types";

export const revalidate = 300;

const HORIZON_DAYS = 21;

const FOLD_LABELS: Record<number, string> = { 2: "Double", 3: "Treble", 4: "4-fold" };
const foldLabel = (size: number) => FOLD_LABELS[size] ?? `${size}-fold`;

const kellyPct = (k: number) => (k > 0 ? `${(k * 100).toFixed(1)}%` : "—");
const evFmt = (ev: number) => `${ev.toFixed(2)}×`;
const edgeFmt = (e: number | null) => (e == null ? "—" : `${e > 0 ? "+" : ""}${(e * 100).toFixed(1)}%`);

/** Visual identity + plain-language framing per risk tier. */
const TIER_META: Record<
  RiskTier,
  { label: string; tag: string; blurb: string; ring: string; chip: string; dot: string }
> = {
  safe: {
    label: "Safe",
    tag: "bankers",
    blurb: "Most likely to land (model ≥ 60%). Short odds, lowest variance.",
    ring: "border-emerald-500/30",
    chip: "bg-emerald-500/15 text-emerald-300",
    dot: "bg-emerald-400",
  },
  medium: {
    label: "Medium",
    tag: "balanced",
    blurb: "Roughly even-money calls (40–60%). The sweet spot for value at fair odds.",
    ring: "border-amber-500/30",
    chip: "bg-amber-500/15 text-amber-300",
    dot: "bg-amber-400",
  },
  risky: {
    label: "Risky",
    tag: "longshots",
    blurb: "Underdog value (25–42%). Bigger prices, bigger swings — stake small.",
    ring: "border-rose-500/30",
    chip: "bg-rose-500/15 text-rose-300",
    dot: "bg-rose-400",
  },
};

/** Matchday staking strategies: per match day, a Safe / Medium / Risky board
 * shown side-by-side — the best single picks, cross-match accumulators, and
 * correlation-aware same-game combos at each risk appetite, across every market
 * (result, goals overs, BTTS, corners, halves). Each pick is value-ranked and
 * carries edge / EV / Kelly. Responsible-use messaging lives ON this page. */
export default async function StrategiesPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const sp = await searchParams;
  const sb = supabaseServer();
  const view = await resolveView(sp, sb);

  let days: MatchdayTiers[] = [];
  let corrMatches = 0; // finished matches behind the fitted correlations (max n)
  if (sb) {
    const now = new Date().toISOString();
    const horizon = new Date(Date.now() + HORIZON_DAYS * 86400_000).toISOString();
    const [{ data }, { data: scores }, { data: corr }, { data: gridRows }] = await Promise.all([
      sb
        .from("match_predictions")
        .select(
          "pipeline, market, selection, probability, market_odds, fair_odds, edge, fixture_id, " +
            "fixtures!inner(id, kickoff, status, home:teams!fixtures_home_id_fkey(name), away:teams!fixtures_away_id_fkey(name))"
        )
        .in("pipeline", [view.blendPipeline, view.modelPipeline])
        .neq("fixtures.status", "finished")
        .gte("fixtures.kickoff", now)
        .lte("fixtures.kickoff", horizon)
        .limit(4000),
      sb.from("model_scores").select("*").gte("n", EXPERIMENTAL_MIN_N).eq("beats_baseline", true),
      sb.from("market_correlations").select("category_a, category_b, rho, n"),
      sb.from("score_grids").select("fixture_id, grid"),
    ]);
    const calibrated = ((scores as ModelScore[] | null) ?? []).map((s) => s.market);
    // fitted same-game correlations (engine/correlations.py); falls back to the
    // static prior in lib/correlation.ts for any pair the engine hasn't written
    const corrRows = (corr as { category_a: string; category_b: string; rho: number; n: number }[] | null) ?? [];
    const fitted = new Map(corrRows.map((r) => [`${r.category_a}|${r.category_b}`, Number(r.rho)]));
    corrMatches = corrRows.reduce((m, r) => Math.max(m, Number(r.n) || 0), 0);
    // per-fixture scoreline grids → exact same-game joints for goal pairs
    const grids = new Map(
      ((gridRows as { fixture_id: number; grid: number[][] }[] | null) ?? []).map((g) => [
        g.fixture_id,
        g.grid,
      ])
    );
    days = buildRiskTiers((data as unknown as StrategyRow[] | null) ?? [], view, calibrated, fitted, grids);
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="mb-1 text-2xl font-bold text-zinc-100">Matchday strategies</h1>
          <p className="max-w-2xl text-sm text-zinc-500">
            For each upcoming match day, a <span className="text-emerald-300">Safe</span> /{" "}
            <span className="text-amber-300">Medium</span> / <span className="text-rose-300">Risky</span>{" "}
            board from the {view.label.toLowerCase()} — singles, cross-match accumulators and
            correlation-aware same-game combos across every market (result, goals overs, BTTS, corners,
            halves), value-ranked with flat and Kelly staking.
          </p>
        </div>
        <ModelSwitcher active={view.view} />
      </div>

      <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 text-xs text-amber-200/90">
        <strong>Read this first.</strong> These are model projections, not guarantees — and risk rises
        as you move from Safe to Risky and as you add legs. Cross-match accumulators assume distinct
        matches are independent; same-game goal pairs are priced exactly from the model&rsquo;s
        scoreline grid, and corner/half pairs from a fitted correlation.
        Check the <Link href="/models" className="underline">model scoreboard</Link> before trusting
        any number. If you choose to bet: only what you can afford to lose, 18+/21+ where applicable,
        and see{" "}
        <a className="underline" href="https://www.begambleaware.org" rel="noreferrer" target="_blank">
          BeGambleAware
        </a>
        .
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        {(Object.keys(TIER_META) as RiskTier[]).map((tier) => (
          <div key={tier} className={`rounded-xl border ${TIER_META[tier].ring} bg-pitch-900 p-3`}>
            <div className="mb-1 flex items-center gap-2">
              <span className={`h-2 w-2 rounded-full ${TIER_META[tier].dot}`} />
              <span className="text-sm font-semibold text-zinc-100">{TIER_META[tier].label}</span>
              <span className="text-[10px] uppercase tracking-wide text-zinc-600">{TIER_META[tier].tag}</span>
            </div>
            <p className="text-xs leading-relaxed text-zinc-500">{TIER_META[tier].blurb}</p>
          </div>
        ))}
      </div>

      <div className="rounded-xl border border-pitch-700 bg-pitch-900 p-4 text-xs leading-relaxed text-zinc-500">
        <span className="font-semibold text-zinc-300">How to read this.</span> A play&rsquo;s{" "}
        <strong>tier</strong> is set by how likely the model thinks it is to land; inside each tier the
        picks are <strong>value-ranked</strong> (probability × odds), so positive-edge prices rise to
        the top. <strong>Same-game combos</strong> pair two markets in one match so the joint
        probability isn&rsquo;t a naive product — the <span className="italic">indep.</span> figure
        shows what the product would have been. Pure goal pairs (full-time result, goals over/under,
        BTTS) are priced <strong>exactly</strong> from the model&rsquo;s scoreline grid; pairs that
        touch corners or first-half goals use a copula whose correlation is{" "}
        {corrMatches > 0
          ? `fitted from ${corrMatches} finished match${corrMatches === 1 ? "" : "es"} so far (shrunk toward a prior while the sample is small)`
          : "a conservative prior until finished matches accumulate"}
        .{" "}
        <strong>Edge</strong> is the model probability minus the book&rsquo;s implied price;{" "}
        <strong>EV</strong> is the expected return per 1u. <strong>Flat</strong> is a fixed 1-unit
        stake; <strong>Kelly</strong> is a fraction of bankroll sized by edge (
        {KELLY_FRACTION === 0.25 ? "quarter" : `${KELLY_FRACTION}×`}-Kelly, capped at{" "}
        {Math.round(KELLY_CAP * 100)}%) and reads &ldquo;—&rdquo; when the price carries no edge. Odds
        marked <span className="italic">fair</span> are the model&rsquo;s own price (no book line yet).
      </div>

      {days.length === 0 ? (
        <p className="rounded-xl border border-pitch-700 bg-pitch-900 p-6 text-sm text-zinc-500">
          No strategies to show — either there are no qualifying upcoming fixtures in the next{" "}
          {HORIZON_DAYS} days, or the engine hasn&rsquo;t run yet.
        </p>
      ) : (
        <div className="space-y-10">
          {days.map((day) => (
            <MatchdayBlock key={day.date} day={day} />
          ))}
        </div>
      )}
    </div>
  );
}

function MatchdayBlock({ day }: { day: MatchdayTiers }) {
  const populated = day.tiers.filter(
    (t) => t.singles.length > 0 || t.combos.length > 0 || t.sameGame.length > 0
  );
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-pitch-700 pb-2">
        <h2 className="text-lg font-bold text-zinc-100">{dateFmt(`${day.date}T00:00:00Z`)}</h2>
        <span className="text-xs text-zinc-600">
          {day.legCount} qualifying {day.legCount === 1 ? "match" : "matches"}
        </span>
      </div>

      {populated.length === 0 ? (
        <p className="text-xs text-zinc-600">No qualifying picks for this match day.</p>
      ) : (
        populated.map((board) => <TierBlock key={board.tier} board={board} />)
      )}
    </section>
  );
}

function TierBlock({ board }: { board: TierBoard }) {
  const meta = TIER_META[board.tier];
  return (
    <div className={`space-y-3 rounded-xl border ${meta.ring} bg-pitch-950/40 p-4`}>
      <div className="flex items-center gap-2">
        <span className={`h-2.5 w-2.5 rounded-full ${meta.dot}`} />
        <h3 className="text-sm font-bold text-zinc-100">{meta.label}</h3>
        <span className="text-[10px] uppercase tracking-wide text-zinc-600">{meta.tag}</span>
      </div>

      {board.combos.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">Accumulators</h4>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {board.combos.map((combo) => (
              <ComboCard key={combo.size} combo={combo} chip={meta.chip} />
            ))}
          </div>
        </div>
      )}

      {board.sameGame.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
            Same-game combos <span className="text-zinc-600">· correlation-priced</span>
          </h4>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {board.sameGame.map((sgc) => (
              <SameGameCard key={sgc.fixtureId} combo={sgc} chip={meta.chip} />
            ))}
          </div>
        </div>
      )}

      {board.singles.length > 0 && <SinglesTable legs={board.singles} />}
    </div>
  );
}

function SinglesTable({ legs }: { legs: StrategyLeg[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-pitch-700">
      <table className="w-full text-sm">
        <caption className="bg-pitch-800 px-3 py-2 text-left text-xs uppercase tracking-wide text-zinc-500">
          Single picks
        </caption>
        <thead className="bg-pitch-800/60 text-left text-xs uppercase tracking-wide text-zinc-500">
          <tr>
            <th className="px-3 py-2">Match</th>
            <th className="px-3 py-2">Market · pick</th>
            <th className="px-3 py-2 text-right">Prob</th>
            <th className="px-3 py-2 text-right">Odds</th>
            <th className="px-3 py-2 text-right">Edge</th>
            <th className="px-3 py-2 text-right">EV</th>
            <th className="px-3 py-2 text-right">Kelly</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-pitch-800 bg-pitch-900">
          {legs.map((leg) => (
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
              <td className="px-3 py-2 text-right font-mono text-accent">{edgeFmt(leg.edge)}</td>
              <td className="px-3 py-2 text-right font-mono text-zinc-200">{evFmt(leg.ev)}</td>
              <td className="px-3 py-2 text-right font-mono text-zinc-200">{kellyPct(leg.kelly)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ComboCard({ combo, chip }: { combo: Combo; chip: string }) {
  return (
    <div className="flex flex-col rounded-xl border border-pitch-700 bg-pitch-900 p-4">
      <div className="mb-2 flex items-center justify-between">
        <h5 className="text-sm font-semibold text-zinc-100">{foldLabel(combo.size)}</h5>
        <span className={`rounded-full px-2 py-0.5 font-mono text-xs font-semibold ${chip}`}>
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
        <Stat label="EV" value={evFmt(combo.expectedValue)} />
        <Stat label="Kelly" value={kellyPct(combo.kelly)} />
      </dl>
    </div>
  );
}

function SameGameCard({ combo, chip }: { combo: SameGameCombo; chip: string }) {
  return (
    <div className="flex flex-col rounded-xl border border-pitch-700 bg-pitch-900 p-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <Link href={`/matches/${combo.fixtureId}`} className="truncate text-sm font-semibold text-zinc-100 hover:text-accent">
          {combo.home} v {combo.away}
        </Link>
        <span className={`rounded-full px-2 py-0.5 font-mono text-xs font-semibold ${chip}`}>
          {pct(combo.jointProbability)}
        </span>
      </div>

      <ul className="mb-3 space-y-1.5 text-xs">
        {combo.legs.map((leg) => (
          <li key={`${leg.market}-${leg.selection}`} className="flex items-baseline justify-between gap-2 text-zinc-300">
            <span className="truncate">
              {marketLabel(leg.market)} — {selectionLabel(leg.market, leg.selection)}
            </span>
            <span className="shrink-0 font-mono text-zinc-500">
              {odds(leg.odds)}
              {leg.oddsIsFair && <span className="ml-1 text-[10px] italic text-zinc-600">fair</span>}
            </span>
          </li>
        ))}
      </ul>

      <dl className="mt-auto grid grid-cols-3 gap-2 border-t border-pitch-800 pt-3 text-center text-xs">
        <Stat
          label="Together"
          value={pct(combo.jointProbability)}
          hint={`indep. ${pct(combo.independentProbability)}`}
        />
        <Stat label="Odds" value={odds(combo.combinedOdds)} />
        <Stat label="Kelly" value={kellyPct(combo.kelly)} />
      </dl>
      <p className="mt-2 text-center text-[10px] text-zinc-600">
        correlation ρ = {combo.rho.toFixed(2)}
        {combo.exact ? " · exact (scoreline grid)" : " · copula"}
      </p>
    </div>
  );
}

function ComboLeg({ leg }: { leg: StrategyLeg }) {
  return (
    <li className="flex items-baseline justify-between gap-2">
      <Link href={`/matches/${leg.fixtureId}`} className="truncate text-zinc-300 hover:text-accent">
        {leg.home} v {leg.away}
        <span className="text-zinc-500"> — {selectionLabel(leg.market, leg.selection)}</span>
      </Link>
      <span className="shrink-0 font-mono text-zinc-500">{pct(leg.probability)}</span>
    </li>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <dt className="text-zinc-600">{label}</dt>
      <dd className="font-mono font-semibold text-zinc-200">{value}</dd>
      {hint && <dd className="font-mono text-[10px] text-zinc-600">{hint}</dd>}
    </div>
  );
}
