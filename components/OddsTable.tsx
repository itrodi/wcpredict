"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import UpdatedBadge from "@/components/UpdatedBadge";
import { pct } from "@/lib/format";
import { supabaseBrowser } from "@/lib/supabase/client";
import type { TournamentOdds } from "@/lib/types";

/** Tournament odds table, live via Realtime (event "*" — see spec §6).
 * Rows belong to ONE pipeline; Realtime payloads from any other pipeline are
 * dropped client-side (never mix pipelines silently). */
export default function OddsTable({
  initial,
  pipeline,
  label,
}: {
  initial: TournamentOdds[];
  pipeline: string;
  label: string;
}) {
  const [rows, setRows] = useState<TournamentOdds[]>(initial);

  useEffect(() => {
    setRows(initial);
    const sb = supabaseBrowser();
    if (!sb) return;
    const ch = sb
      .channel(`tournament-odds-${pipeline}`)
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "tournament_odds" },
        (payload) => {
          const next = payload.new as TournamentOdds;
          if (!next?.team_id || next.pipeline !== pipeline) return;
          setRows((prev) => {
            const i = prev.findIndex((r) => r.team_id === next.team_id);
            const copy = i === -1 ? [...prev, next] : [...prev];
            if (i !== -1) copy[i] = { ...prev[i], ...next, teams: prev[i].teams };
            return copy.sort((a, b) => (b.champion ?? 0) - (a.champion ?? 0));
          });
        }
      )
      .subscribe();
    return () => {
      sb.removeChannel(ch);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pipeline]);

  if (rows.length === 0) {
    return (
      <p className="text-sm text-zinc-500">
        No simulation results yet — trigger the refresh workflow to populate them.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <UpdatedBadge computedAt={rows[0]?.computed_at ?? null} label={label} />
        <span className="text-xs text-zinc-600">{rows[0]?.n_sims?.toLocaleString()} Monte Carlo runs</span>
      </div>
      <div className="overflow-x-auto rounded-xl border border-pitch-700">
        <table className="w-full text-sm">
          <thead className="bg-pitch-800 text-left text-xs uppercase tracking-wide text-zinc-500">
            <tr>
              <th className="px-3 py-2.5">#</th>
              <th className="px-3 py-2.5">Team</th>
              <th className="px-3 py-2.5 text-right">Elo</th>
              <th className="px-3 py-2.5 text-right">Advance</th>
              <th className="px-3 py-2.5 text-right">QF</th>
              <th className="px-3 py-2.5 text-right">SF</th>
              <th className="px-3 py-2.5 text-right">Final</th>
              <th className="px-3 py-2.5 text-right">Champion</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-pitch-800 bg-pitch-900">
            {rows.map((r, i) => (
              <tr key={r.team_id} className="hover:bg-pitch-800/60">
                <td className="px-3 py-2 text-zinc-600">{i + 1}</td>
                <td className="px-3 py-2">
                  {r.teams ? (
                    <Link href={`/teams/${r.teams.slug}`} className="font-medium text-zinc-100 hover:text-accent">
                      {r.teams.name}
                    </Link>
                  ) : (
                    `#${r.team_id}`
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono text-zinc-400">{r.teams?.elo ?? "–"}</td>
                <td className="px-3 py-2 text-right font-mono text-zinc-300">{pct(r.advance_grp)}</td>
                <td className="px-3 py-2 text-right font-mono text-zinc-300">{pct(r.reach_qf)}</td>
                <td className="px-3 py-2 text-right font-mono text-zinc-300">{pct(r.reach_sf)}</td>
                <td className="px-3 py-2 text-right font-mono text-zinc-300">{pct(r.reach_final)}</td>
                <td className="px-3 py-2 text-right font-mono font-semibold text-accent">{pct(r.champion)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
