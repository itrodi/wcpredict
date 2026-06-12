"use client";

import { useMemo } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { OddsSnapshot } from "@/lib/types";

const COLORS: Record<string, string> = {
  home: "#34d399",
  draw: "#a1a1aa",
  away: "#60a5fa",
};

/** Line movement from odds_snapshots (spec v4 §6.6): median decimal odds per
 * selection per fetch batch, opening to now. */
export default function LineMovementChart({
  snapshots,
  homeName,
  awayName,
}: {
  snapshots: OddsSnapshot[];
  homeName: string;
  awayName: string;
}) {
  const data = useMemo(() => {
    // group by fetch batch, median across bookmakers per selection
    const batches = new Map<string, Record<string, number[]>>();
    for (const s of snapshots) {
      const b = batches.get(s.fetched_at) ?? {};
      (b[s.selection] = b[s.selection] ?? []).push(Number(s.decimal_odds));
      batches.set(s.fetched_at, b);
    }
    const median = (xs: number[]) => {
      const a = [...xs].sort((x, y) => x - y);
      return a.length % 2 ? a[(a.length - 1) / 2] : (a[a.length / 2 - 1] + a[a.length / 2]) / 2;
    };
    return [...batches.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([ts, sels]) => ({
        ts: new Date(ts).toLocaleString("en-GB", {
          day: "numeric",
          month: "short",
          hour: "2-digit",
          minute: "2-digit",
          timeZone: "UTC",
        }),
        home: sels.home ? median(sels.home) : null,
        draw: sels.draw ? median(sels.draw) : null,
        away: sels.away ? median(sels.away) : null,
      }));
  }, [snapshots]);

  if (data.length < 2) return null;

  return (
    <section className="rounded-xl border border-pitch-700 bg-pitch-900 p-4">
      <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-400">
        Line movement (1X2, median book odds)
      </h3>
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
            <CartesianGrid stroke="#1a3526" vertical={false} />
            <XAxis dataKey="ts" tick={{ fill: "#a1a1aa", fontSize: 10 }} minTickGap={40} />
            <YAxis
              tick={{ fill: "#a1a1aa", fontSize: 10 }}
              domain={["auto", "auto"]}
              width={36}
              tickFormatter={(v: number) => v.toFixed(2)}
            />
            <Tooltip
              contentStyle={{ background: "#0b1a12", border: "1px solid #1a3526", borderRadius: 8 }}
              labelStyle={{ color: "#e4e4e7" }}
              formatter={(v, name) => [
                Number(v).toFixed(2),
                name === "home" ? homeName : name === "away" ? awayName : "Draw",
              ]}
            />
            {(["home", "draw", "away"] as const).map((sel) => (
              <Line
                key={sel}
                type="stepAfter"
                dataKey={sel}
                stroke={COLORS[sel]}
                dot={false}
                strokeWidth={1.75}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2 flex gap-4 text-xs text-zinc-500">
        <span><span className="text-accent">▬</span> {homeName}</span>
        <span><span className="text-zinc-400">▬</span> Draw</span>
        <span><span className="text-blue-400">▬</span> {awayName}</span>
      </div>
    </section>
  );
}
