"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export default function ChampionChart({
  data,
}: {
  data: { name: string; champion: number }[];
}) {
  if (data.length === 0) return null;
  return (
    <div className="h-72 rounded-xl border border-pitch-700 bg-pitch-900 p-4">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
          <CartesianGrid stroke="#1a3526" vertical={false} />
          <XAxis dataKey="name" tick={{ fill: "#a1a1aa", fontSize: 11 }} interval={0} angle={-30} textAnchor="end" height={56} />
          <YAxis
            tick={{ fill: "#a1a1aa", fontSize: 11 }}
            tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
            width={40}
          />
          <Tooltip
            formatter={(v) => [`${(Number(v) * 100).toFixed(1)}%`, "Title odds"]}
            contentStyle={{ background: "#0b1a12", border: "1px solid #1a3526", borderRadius: 8 }}
            labelStyle={{ color: "#e4e4e7" }}
            cursor={{ fill: "rgba(52,211,153,0.08)" }}
          />
          <Bar dataKey="champion" fill="#34d399" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
