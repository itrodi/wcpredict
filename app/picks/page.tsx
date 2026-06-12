import Link from "next/link";

import PicksLive from "@/components/PicksLive";
import { EXPERIMENTAL_MIN_N } from "@/lib/markets";
import { supabaseServer } from "@/lib/supabase/server";
import type { ModelScore, PickRow } from "@/lib/types";

export const revalidate = 300;

const PICK_SELECT =
  "*, fixtures(id, kickoff, status, home:teams!fixtures_home_id_fkey(name), away:teams!fixtures_away_id_fkey(name))";

type TierRecord = { n: number; wins: number; hitRate: number | null; pl: number };

/** Track record from settled, non-retired picks: hit rate per tier and P/L at
 * flat 1-unit stakes vs market_odds. Wins AND losses — this block is the
 * feature's credibility (spec v4.1 §4.3). */
function trackRecord(settled: PickRow[]): Record<"banker" | "value", TierRecord> {
  const out: Record<"banker" | "value", TierRecord> = {
    banker: { n: 0, wins: 0, hitRate: null, pl: 0 },
    value: { n: 0, wins: 0, hitRate: null, pl: 0 },
  };
  for (const p of settled) {
    const t = out[p.tier];
    if (!t) continue;
    t.n++;
    if (p.outcome) {
      t.wins++;
      if (p.market_odds) t.pl += Number(p.market_odds) - 1;
    } else if (p.market_odds) {
      t.pl -= 1;
    }
  }
  for (const t of Object.values(out)) {
    t.hitRate = t.n > 0 ? t.wins / t.n : null;
    t.pl = Math.round(t.pl * 100) / 100;
  }
  return out;
}

export default async function PicksPage() {
  const sb = supabaseServer();
  let live: PickRow[] = [];
  let settled: PickRow[] = [];
  let calibrated: string[] = [];
  if (sb) {
    const [{ data: l }, { data: s }, { data: scores }] = await Promise.all([
      sb
        .from("picks")
        .select(PICK_SELECT)
        .is("retired_at", null)
        .is("outcome", null)
        .order("published_at", { ascending: false }),
      sb
        .from("picks")
        .select("*")
        .not("outcome", "is", null)
        .is("retired_at", null),
      sb.from("model_scores").select("*").gte("n", EXPERIMENTAL_MIN_N),
    ]);
    live = (l as unknown as PickRow[] | null) ?? [];
    settled = (s as PickRow[] | null) ?? [];
    calibrated = ((scores as ModelScore[] | null) ?? []).map((x) => x.market);
  }

  const record = trackRecord(settled);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="mb-1 text-2xl font-bold text-zinc-100">Site picks</h1>
          <p className="max-w-xl text-sm text-zinc-500">
            Rule-generated from the site model — calibrated markets only, capped at 10 per tier and
            2 per fixture, immutable once published, and every settled pick is scored below in
            public. No hand-picking, no deleting losers.
          </p>
        </div>

        {/* Track record block — always visible (spec §4.3) */}
        <div className="rounded-xl border border-pitch-700 bg-pitch-900 p-4 text-sm">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-400">
            Track record (settled picks)
          </h2>
          {settled.length === 0 ? (
            <p className="text-xs text-zinc-600">No settled picks yet.</p>
          ) : (
            <table className="text-xs">
              <thead>
                <tr className="text-zinc-500">
                  <th className="pr-4 text-left font-medium">Tier</th>
                  <th className="pr-4 text-right font-medium">W–L</th>
                  <th className="pr-4 text-right font-medium">Hit rate</th>
                  <th className="text-right font-medium">P/L (1u flat)</th>
                </tr>
              </thead>
              <tbody>
                {(["banker", "value"] as const).map((tier) => {
                  const r = record[tier];
                  return (
                    <tr key={tier} className="text-zinc-200">
                      <td className="pr-4 capitalize">{tier}</td>
                      <td className="pr-4 text-right font-mono">
                        {r.wins}–{r.n - r.wins}
                      </td>
                      <td className="pr-4 text-right font-mono">
                        {r.hitRate != null ? `${Math.round(r.hitRate * 100)}%` : "–"}
                      </td>
                      <td className={`text-right font-mono font-semibold ${r.pl >= 0 ? "text-accent" : "text-red-400"}`}>
                        {r.pl >= 0 ? "+" : ""}
                        {r.pl.toFixed(2)}u
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 text-xs text-amber-200/90">
        <strong>Read this first.</strong> Picks are model outputs, not guarantees — the track record
        above includes every loss for a reason. Check the{" "}
        <Link href="/models" className="underline">model scoreboard</Link> before trusting any number.
        If you choose to bet: only what you can afford to lose, 18+/21+ where applicable, and see{" "}
        <a className="underline" href="https://www.begambleaware.org" rel="noreferrer" target="_blank">
          BeGambleAware
        </a>
        .
      </div>

      <PicksLive initial={live} calibratedMarkets={calibrated} />
    </div>
  );
}
