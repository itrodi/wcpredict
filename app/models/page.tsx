import Link from "next/link";

import UpdatedBadge from "@/components/UpdatedBadge";
import { marketLabel } from "@/lib/markets";
import { PIPELINE_LABELS } from "@/lib/pipeline";
import { supabaseServer } from "@/lib/supabase/server";
import type { ModelScore, PickRow } from "@/lib/types";

export const revalidate = 300;

const PIPELINE_ORDER = ["free", "statsapi", "blend_free", "blend_statsapi", "market"];

/** The "compare and contrast" deliverable: which model is better, answered with
 * Brier/log-loss instead of vibes. pipeline='market' (de-vigged closing odds)
 * is the baseline both models must beat. */
export default async function ModelsPage() {
  const sb = supabaseServer();
  let scores: ModelScore[] = [];
  let settledPicks: PickRow[] = [];
  if (sb) {
    const [{ data }, { data: picks }] = await Promise.all([
      sb.from("model_scores").select("*").order("market"),
      sb.from("picks").select("*").not("outcome", "is", null).is("retired_at", null),
    ]);
    scores = (data as ModelScore[] | null) ?? [];
    settledPicks = (picks as PickRow[] | null) ?? [];
  }

  const markets = [...new Set(scores.map((s) => s.market))].sort(
    (x, y) => (x === "1x2" ? -1 : y === "1x2" ? 1 : x.localeCompare(y))
  );
  const get = (m: string, p: string) => scores.find((s) => s.market === m && s.pipeline === p);
  const settledFor = (m: string) => settledPicks.filter((p) => p.market === m);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="mb-1 text-2xl font-bold text-zinc-100">Model scoreboard</h1>
        <p className="text-sm text-zinc-500">
          The site shows two views (Site Picks and Baseline); all model variants are scored here.
          Brier score and log-loss per pipeline per market over every finished fixture (lower is
          better, one-vs-rest per selection). Δ columns are versus the de-vigged market baseline —
          negative means the model beats the closing line. Settled{" "}
          <Link href="/picks" className="text-emerald-300 hover:underline">picks</Link> per market
          are shown alongside.
        </p>
      </div>
      <UpdatedBadge computedAt={scores[0]?.computed_at ?? null} />

      {markets.length === 0 ? (
        <p className="rounded-xl border border-pitch-700 bg-pitch-900 p-6 text-sm text-zinc-500">
          No scores yet — the scoreboard populates automatically after the first finished fixtures.
        </p>
      ) : (
        markets.map((market) => {
          const baseline = get(market, "market");
          const picks = settledFor(market);
          const pickWins = picks.filter((p) => p.outcome).length;
          return (
            <section key={market} className="overflow-x-auto rounded-xl border border-pitch-700">
              <table className="w-full text-sm">
                <thead className="bg-pitch-800 text-left text-xs uppercase tracking-wide text-zinc-500">
                  <tr>
                    <th className="px-3 py-2.5">{marketLabel(market)}</th>
                    <th className="px-3 py-2.5 text-right">n matches</th>
                    <th className="px-3 py-2.5 text-right">Brier</th>
                    <th className="px-3 py-2.5 text-right">Log-loss</th>
                    <th className="px-3 py-2.5 text-right">Δ log-loss vs market</th>
                    <th className="px-3 py-2.5 text-right">Settled picks</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-pitch-800 bg-pitch-900">
                  {PIPELINE_ORDER.map((p) => {
                    const s = get(market, p);
                    if (!s) return null;
                    const delta =
                      baseline?.log_loss != null && s.log_loss != null && p !== "market"
                        ? Number(s.log_loss) - Number(baseline.log_loss)
                        : null;
                    return (
                      <tr key={p}>
                        <td className="px-3 py-2 font-medium text-zinc-200">
                          {PIPELINE_LABELS[p] ?? p}
                          {p === "market" && (
                            <span className="ml-2 text-xs text-zinc-600">(baseline)</span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-right font-mono text-zinc-400">{s.n}</td>
                        <td className="px-3 py-2 text-right font-mono text-zinc-200">
                          {s.brier != null ? Number(s.brier).toFixed(4) : "–"}
                        </td>
                        <td className="px-3 py-2 text-right font-mono text-zinc-200">
                          {s.log_loss != null ? Number(s.log_loss).toFixed(4) : "–"}
                        </td>
                        <td
                          className={`px-3 py-2 text-right font-mono ${
                            delta == null
                              ? "text-zinc-600"
                              : delta < 0
                                ? "font-semibold text-accent"
                                : "text-zinc-400"
                          }`}
                        >
                          {delta != null ? `${delta > 0 ? "+" : ""}${delta.toFixed(4)}` : "–"}
                        </td>
                        <td className="px-3 py-2 text-right font-mono text-zinc-400">
                          {p === "statsapi" && picks.length > 0
                            ? `${pickWins}–${picks.length - pickWins}`
                            : "–"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>
          );
        })
      )}
      <p className="text-xs text-zinc-600">
        Markets with fewer than 30 scored matches are still labelled experimental across the site.
      </p>
    </div>
  );
}
