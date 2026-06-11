import Link from "next/link";

import { kickoffFmt, pct, STAGE_LABELS } from "@/lib/format";
import type { Fixture, MatchPrediction } from "@/lib/types";

export default function FixtureCard({
  fixture,
  predictions,
}: {
  fixture: Fixture;
  predictions: MatchPrediction[]; // 1x2 rows for this fixture
}) {
  const p = (sel: string) => predictions.find((r) => r.selection === sel)?.probability;
  const finished = fixture.status === "finished";

  return (
    <Link
      href={`/matches/${fixture.id}`}
      className="block rounded-xl border border-pitch-700 bg-pitch-900 p-4 transition hover:border-accent/50"
    >
      <div className="mb-2 flex items-center justify-between text-xs text-zinc-500">
        <span>
          {STAGE_LABELS[fixture.stage] ?? fixture.stage}
          {fixture.group_code ? ` · Group ${fixture.group_code}` : ""}
        </span>
        <span>
          {fixture.status === "live" ? (
            <span className="font-semibold text-amber-400">LIVE</span>
          ) : (
            kickoffFmt(fixture.kickoff)
          )}
        </span>
      </div>
      <div className="flex items-center justify-between gap-2">
        <span className="flex-1 truncate font-medium text-zinc-100">
          {fixture.home?.name ?? "TBD"}
        </span>
        <span className="shrink-0 font-mono text-lg text-zinc-100">
          {finished || fixture.status === "live"
            ? `${fixture.home_goals ?? "-"} : ${fixture.away_goals ?? "-"}`
            : "vs"}
        </span>
        <span className="flex-1 truncate text-right font-medium text-zinc-100">
          {fixture.away?.name ?? "TBD"}
        </span>
      </div>
      {!finished && predictions.length > 0 && (
        <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
          {(["home", "draw", "away"] as const).map((sel) => (
            <div key={sel} className="rounded-md bg-pitch-800 py-1.5">
              <div className="text-zinc-500 uppercase">{sel}</div>
              <div className="font-mono text-sm text-emerald-300">{pct(p(sel))}</div>
            </div>
          ))}
        </div>
      )}
    </Link>
  );
}
