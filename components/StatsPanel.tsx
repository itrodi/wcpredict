"use client";

import { useEffect, useState } from "react";

import { supabaseBrowser } from "@/lib/supabase/client";
import type { MatchStat } from "@/lib/types";

const ROWS: { key: keyof MatchStat; label: string; fmt?: (v: number) => string }[] = [
  { key: "xg", label: "Expected goals (xG)", fmt: (v) => v.toFixed(2) },
  { key: "shots", label: "Shots" },
  { key: "shots_on_target", label: "On target" },
  { key: "corners", label: "Corners" },
  { key: "possession", label: "Possession", fmt: (v) => `${Math.round(v)}%` },
  { key: "fouls", label: "Fouls" },
  { key: "yellows", label: "Yellow cards" },
  { key: "reds", label: "Red cards" },
];

/** Corners/shots/xG/possession from match_stats (Pipeline B data, spec v4 §6.4).
 * Live-updating via Realtime during matches — rows tick as the live poller upserts. */
export default function StatsPanel({
  fixtureId,
  homeTeamId,
  homeName,
  awayName,
  initial,
  live,
}: {
  fixtureId: number;
  homeTeamId: number | null;
  homeName: string;
  awayName: string;
  initial: MatchStat[];
  live: boolean;
}) {
  const [stats, setStats] = useState<MatchStat[]>(initial);

  useEffect(() => {
    setStats(initial);
    const sb = supabaseBrowser();
    if (!sb || !live) return; // subscribe only when it can change (200-connection cap)
    const ch = sb
      .channel(`mstats-${fixtureId}`)
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "match_stats", filter: `fixture_id=eq.${fixtureId}` },
        (payload) => {
          const next = payload.new as MatchStat;
          if (!next?.team_id) return;
          setStats((prev) => {
            const i = prev.findIndex(
              (s) => s.team_id === next.team_id && s.period === next.period
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
      sb.removeChannel(ch);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fixtureId, live]);

  const ft = stats.filter((s) => s.period === "FT");
  const home = ft.find((s) => s.team_id === homeTeamId || s.is_home === true);
  const away = ft.find((s) => s !== home);
  if (!home && !away) return null;

  const cell = (s: MatchStat | undefined, row: (typeof ROWS)[number]) => {
    const v = s?.[row.key];
    if (v == null) return "–";
    return row.fmt ? row.fmt(Number(v)) : String(v);
  };

  return (
    <section className="rounded-xl border border-pitch-700 bg-pitch-900 p-4">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-zinc-400">
        Match stats
        {live && (
          <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium normal-case tracking-normal text-amber-400">
            live
          </span>
        )}
      </h3>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-zinc-500">
            <th className="pb-2 text-left font-medium">{homeName}</th>
            <th className="pb-2 text-center font-normal" />
            <th className="pb-2 text-right font-medium">{awayName}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-pitch-800">
          {ROWS.filter((r) => home?.[r.key] != null || away?.[r.key] != null).map((row) => (
            <tr key={row.key as string}>
              <td className="py-1.5 text-left font-mono text-zinc-100">{cell(home, row)}</td>
              <td className="py-1.5 text-center text-xs text-zinc-500">{row.label}</td>
              <td className="py-1.5 text-right font-mono text-zinc-100">{cell(away, row)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
