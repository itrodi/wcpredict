import Link from "next/link";

import { kickoffFmt } from "@/lib/format";
import { GOAL_LADDER, type GoalMatch } from "@/lib/goals";
import { selectionLabel } from "@/lib/markets";
import { confClass } from "@/lib/verdicts";

function Pct({ p }: { p: number | undefined }) {
  if (p == null) return <span className="text-zinc-600">–</span>;
  return <span className={`font-mono ${confClass(p)}`}>{Math.round(p * 100)}%</span>;
}

/** High-scoring board: upcoming matches ranked by how likely they are to be a
 * goal-fest, showing the full over ladder. Model projections, not tracked bets. */
export default function GoalRush({ matches }: { matches: GoalMatch[] }) {
  if (matches.length === 0) {
    return (
      <p className="rounded-xl border border-pitch-700 bg-pitch-900 p-6 text-sm text-zinc-500">
        No upcoming fixtures with goal predictions yet.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-xl border border-pitch-700">
      <table className="w-full text-sm">
        <thead className="bg-pitch-800 text-left text-xs uppercase tracking-wide text-zinc-500">
          <tr>
            <th className="px-3 py-2.5">Match</th>
            {GOAL_LADDER.map(([market, line]) => (
              <th key={market} className="px-3 py-2.5 text-right">
                O{line}
              </th>
            ))}
            <th className="px-3 py-2.5">Top goals call</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-pitch-800 bg-pitch-900">
          {matches.map((m) => (
            <tr key={m.fixtureId} className="hover:bg-pitch-800/60">
              <td className="px-3 py-2">
                <Link href={`/matches/${m.fixtureId}`} className="text-zinc-100 hover:text-accent">
                  {m.home} v {m.away}
                </Link>
                <div className="text-xs text-zinc-600">{kickoffFmt(m.kickoff)}</div>
              </td>
              {GOAL_LADDER.map(([market]) => (
                <td key={market} className="px-3 py-2 text-right">
                  <Pct p={m.overs[market]} />
                </td>
              ))}
              <td className="px-3 py-2">
                {m.headline ? (
                  <span className="inline-flex flex-col">
                    <span className="font-medium text-zinc-100">
                      {selectionLabel(m.headline.market, "over")}
                    </span>
                    <span className={`font-mono text-xs ${confClass(m.headline.prob)}`}>
                      {Math.round(m.headline.prob * 100)}%
                    </span>
                  </span>
                ) : (
                  <span className="text-zinc-600">low-scoring lean</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
