"use client";

import { useEffect, useMemo, useState } from "react";

import ProbabilityBar from "@/components/ProbabilityBar";
import UpdatedBadge from "@/components/UpdatedBadge";
import {
  EXPERIMENTAL_MARKETS,
  MARKET_LABELS,
  odds,
  pct,
  SELECTION_LABELS,
} from "@/lib/format";
import { PIPELINE_LABELS } from "@/lib/pipeline";
import { supabaseBrowser } from "@/lib/supabase/client";
import type { MatchPrediction } from "@/lib/types";

const SELECTION_ORDER: Record<string, number> = {
  home: 0, draw: 1, away: 2, over: 0, under: 1, yes: 0, no: 1,
};

const MARKET_ORDER = [
  "1x2", "ou25", "btts", "ht_1x2", "ou05_1h", "ou15_1h", "htft",
  "corners_o85", "corners_o95", "corners_o105",
  "team_corners_home_o45", "team_corners_away_o45", "cs",
];

const GRID_MARKETS = new Set(["cs", "htft"]);

/** Full market breakdown with refresh-free updates via Realtime.
 * Subscribes to event "*" — the worker's upserts emit INSERTs the first time a
 * row is written, and an UPDATE-only subscription would silently miss them.
 * v4: rows are filtered to ONE pipeline (Realtime filters support a single
 * column, so the pipeline filter is applied client-side on each payload). */
export default function MatchMarkets({
  fixtureId,
  pipeline,
  initial,
  calibratedMarkets,
}: {
  fixtureId: number;
  pipeline: string;
  initial: MatchPrediction[];
  calibratedMarkets: string[]; // markets with >= 30 scored matches in model_scores
}) {
  const [rows, setRows] = useState<MatchPrediction[]>(initial);

  useEffect(() => {
    setRows(initial);
    const sb = supabaseBrowser();
    if (!sb) return;
    const ch = sb
      .channel(`mpred-${fixtureId}-${pipeline}`)
      .on(
        "postgres_changes",
        {
          event: "*",
          schema: "public",
          table: "match_predictions",
          filter: `fixture_id=eq.${fixtureId}`,
        },
        (payload) => {
          const next = payload.new as MatchPrediction;
          if (!next?.market || next.pipeline !== pipeline) return;
          setRows((prev) => {
            const i = prev.findIndex(
              (r) => r.market === next.market && r.selection === next.selection
            );
            if (i === -1) return [...prev, next];
            const copy = [...prev];
            copy[i] = next;
            return copy;
          });
        }
      )
      .subscribe();
    return () => {
      sb.removeChannel(ch); // free tier: 200 concurrent connections — always unsubscribe
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fixtureId, pipeline]);

  const byMarket = useMemo(() => {
    const m = new Map<string, MatchPrediction[]>();
    for (const r of rows) {
      m.set(r.market, [...(m.get(r.market) ?? []), r]);
    }
    for (const list of m.values()) {
      list.sort(
        (a, b) =>
          (SELECTION_ORDER[a.selection] ?? 99) - (SELECTION_ORDER[b.selection] ?? 99) ||
          b.probability - a.probability
      );
    }
    return m;
  }, [rows]);

  const computedAt = rows[0]?.computed_at ?? null;
  if (rows.length === 0) {
    return (
      <p className="text-sm text-zinc-500">
        No predictions from the {PIPELINE_LABELS[pipeline] ?? pipeline} pipeline for this fixture yet.
      </p>
    );
  }

  return (
    <div className="space-y-6">
      <UpdatedBadge computedAt={computedAt} label={PIPELINE_LABELS[pipeline]} />
      {MARKET_ORDER.map((market) => {
        const list = byMarket.get(market);
        if (!list) return null;
        const experimental =
          EXPERIMENTAL_MARKETS.has(market) && !calibratedMarkets.includes(market);
        return (
          <section key={market} className="rounded-xl border border-pitch-700 bg-pitch-900 p-4">
            <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-zinc-400">
              {MARKET_LABELS[market] ?? market}
              {experimental && (
                <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium normal-case tracking-normal text-amber-400">
                  experimental — uncalibrated
                </span>
              )}
            </h3>
            {GRID_MARKETS.has(market) ? (
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
                {list
                  .filter((r) => r.probability >= 0.005 || r.selection === "other")
                  .slice(0, 15)
                  .map((r) => (
                    <div key={r.selection} className="rounded-md bg-pitch-800 px-2 py-1.5 text-center">
                      <div className="font-mono text-sm text-zinc-100">
                        {SELECTION_LABELS[r.selection] ?? r.selection.replace("_", " / ")}
                      </div>
                      <div className="text-xs text-emerald-300">{pct(r.probability)}</div>
                    </div>
                  ))}
              </div>
            ) : (
              <div className="space-y-2">
                {list.map((r) => (
                  <div key={r.selection}>
                    <ProbabilityBar
                      label={SELECTION_LABELS[r.selection] ?? r.selection}
                      probability={r.probability}
                      highlight={(r.edge ?? 0) > 0.02}
                    />
                    {market === "1x2" && r.market_odds != null && (
                      <div className="ml-[6.75rem] mt-0.5 text-xs text-zinc-500">
                        fair {odds(r.fair_odds)} · book {odds(r.market_odds)} · edge{" "}
                        <span className={(r.edge ?? 0) > 0 ? "text-accent" : "text-zinc-500"}>
                          {r.edge != null ? `${(r.edge * 100).toFixed(1)}%` : "–"}
                        </span>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>
        );
      })}
      <p className="text-xs text-zinc-600">
        Probabilities are model estimates, not guarantees. No betting edge implies profit. Please gamble
        responsibly — see{" "}
        <a className="underline" href="https://www.begambleaware.org" rel="noreferrer" target="_blank">
          BeGambleAware
        </a>
        .
      </p>
    </div>
  );
}
