import Link from "next/link";
import { notFound } from "next/navigation";

import UpdatedBadge from "@/components/UpdatedBadge";
import { kickoffFmt, MARKET_LABELS, pct, SELECTION_LABELS } from "@/lib/format";
import { supabaseServer } from "@/lib/supabase/server";
import type { Fixture, MatchPrediction } from "@/lib/types";

export const revalidate = 300;

const DISAGREE = 0.05; // highlight where A and B disagree by >5 points (spec v4 §6.2)

const MARKET_ORDER = [
  "1x2", "ou25", "btts", "ht_1x2", "ou05_1h", "ou15_1h",
  "corners_o85", "corners_o95", "corners_o105",
];

/** The flagship dual-pipeline page: Pipeline A | Pipeline B | Market, side by side. */
export default async function ComparePage({ params }: { params: Promise<{ id: string }> }) {
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
    .eq("fixture_id", fixtureId)
    .in("pipeline", ["free", "statsapi"]);
  const rows = (preds as MatchPrediction[] | null) ?? [];

  const a = new Map(rows.filter((r) => r.pipeline === "free").map((r) => [`${r.market}:${r.selection}`, r]));
  const b = new Map(rows.filter((r) => r.pipeline === "statsapi").map((r) => [`${r.market}:${r.selection}`, r]));
  const keys = [...new Set([...a.keys(), ...b.keys()])];

  const markets = MARKET_ORDER.filter((m) => keys.some((k) => k.startsWith(`${m}:`)));

  // de-vigged market prob recovered from edge = p_model - p_market (1X2 only in v1)
  const marketP = (key: string) => {
    const r = a.get(key) ?? b.get(key);
    return r?.edge != null ? Number(r.probability) - Number(r.edge) : null;
  };

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-pitch-700 bg-pitch-900 p-6">
        <div className="mb-2 text-xs text-zinc-500">
          Model comparison ·{" "}
          <Link href={`/matches/${fixtureId}`} className="text-emerald-300 hover:underline">
            ← back to match
          </Link>
        </div>
        <h1 className="text-xl font-bold text-zinc-100 sm:text-2xl">
          {f.home?.name ?? "TBD"} <span className="text-zinc-500">vs</span> {f.away?.name ?? "TBD"}
        </h1>
        <p className="mt-1 text-sm text-zinc-500">{kickoffFmt(f.kickoff)}</p>
      </div>

      <div className="flex items-center justify-between">
        <UpdatedBadge computedAt={rows[0]?.computed_at ?? null} />
        <span className="text-xs text-zinc-600">
          highlighted = models disagree by &gt;{DISAGREE * 100} points
        </span>
      </div>

      {rows.length === 0 && (
        <p className="text-sm text-zinc-500">No predictions for this fixture yet.</p>
      )}

      {markets.map((market) => {
        const sels = [
          ...new Set(
            keys.filter((k) => k.startsWith(`${market}:`)).map((k) => k.slice(market.length + 1))
          ),
        ];
        return (
          <section key={market} className="overflow-x-auto rounded-xl border border-pitch-700">
            <table className="w-full text-sm">
              <thead className="bg-pitch-800 text-left text-xs uppercase tracking-wide text-zinc-500">
                <tr>
                  <th className="px-3 py-2.5">{MARKET_LABELS[market] ?? market}</th>
                  <th className="px-3 py-2.5 text-right">Free model</th>
                  <th className="px-3 py-2.5 text-right">Stats model</th>
                  <th className="px-3 py-2.5 text-right">Market</th>
                  <th className="px-3 py-2.5 text-right">A − B</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-pitch-800 bg-pitch-900">
                {sels.map((sel) => {
                  const key = `${market}:${sel}`;
                  const pa = a.get(key)?.probability;
                  const pb = b.get(key)?.probability;
                  const pm = market === "1x2" ? marketP(key) : null;
                  const diff = pa != null && pb != null ? Number(pa) - Number(pb) : null;
                  const hot = diff != null && Math.abs(diff) > DISAGREE;
                  return (
                    <tr key={sel} className={hot ? "bg-amber-500/5" : undefined}>
                      <td className="px-3 py-2 text-zinc-300">{SELECTION_LABELS[sel] ?? sel}</td>
                      <td className="px-3 py-2 text-right font-mono text-zinc-200">
                        {pa != null ? pct(Number(pa)) : "–"}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-zinc-200">
                        {pb != null ? pct(Number(pb)) : "–"}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-zinc-400">
                        {pm != null ? pct(pm) : "–"}
                      </td>
                      <td
                        className={`px-3 py-2 text-right font-mono ${
                          hot ? "font-semibold text-amber-400" : "text-zinc-500"
                        }`}
                      >
                        {diff != null ? `${diff > 0 ? "+" : ""}${(diff * 100).toFixed(1)}` : "–"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>
        );
      })}
    </div>
  );
}
