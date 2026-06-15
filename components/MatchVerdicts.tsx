import Link from "next/link";

import { kickoffFmt } from "@/lib/format";
import { selectionLabel } from "@/lib/markets";
import { confClass, confLabel, type Verdict, type VerdictPick } from "@/lib/verdicts";

/** Format a verdict pick into human text. 1X2 selections resolve to team names. */
function pickText(p: VerdictPick, home: string, away: string): string {
  if (p.market === "1x2") {
    return p.selection === "home" ? home : p.selection === "away" ? away : "Draw";
  }
  return selectionLabel(p.market, p.selection);
}

function Cell({ pick, home, away }: { pick: VerdictPick | null; home: string; away: string }) {
  if (!pick) return <span className="text-zinc-600">–</span>;
  return (
    <span className="inline-flex flex-col">
      <span className="text-zinc-200">{pickText(pick, home, away)}</span>
      <span className={`font-mono text-xs ${confClass(pick.prob)}`}>{Math.round(pick.prob * 100)}%</span>
    </span>
  );
}

/** Per-match "dominant pick" board: the model's strongest call in result, goals
 * and corners for every upcoming fixture, plus the single top pick. These are
 * model projections for browsing — NOT the tracked Bankers/Value picks. */
export default function MatchVerdicts({ verdicts }: { verdicts: Verdict[] }) {
  if (verdicts.length === 0) {
    return (
      <p className="rounded-xl border border-pitch-700 bg-pitch-900 p-6 text-sm text-zinc-500">
        No upcoming fixtures with predictions yet.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-xl border border-pitch-700">
      <table className="w-full text-sm">
        <thead className="bg-pitch-800 text-left text-xs uppercase tracking-wide text-zinc-500">
          <tr>
            <th className="px-3 py-2.5">Match</th>
            <th className="px-3 py-2.5">Top pick</th>
            <th className="px-3 py-2.5">Result</th>
            <th className="px-3 py-2.5">Goals</th>
            <th className="px-3 py-2.5">Corners</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-pitch-800 bg-pitch-900">
          {verdicts.map((v) => (
            <tr key={v.fixtureId} className="hover:bg-pitch-800/60">
              <td className="px-3 py-2">
                <Link href={`/matches/${v.fixtureId}`} className="text-zinc-100 hover:text-accent">
                  {v.home} v {v.away}
                </Link>
                <div className="text-xs text-zinc-600">{kickoffFmt(v.kickoff)}</div>
              </td>
              <td className="px-3 py-2">
                {v.top ? (
                  <span className="inline-flex flex-col">
                    <span className="font-medium text-zinc-100">{pickText(v.top, v.home, v.away)}</span>
                    <span className={`font-mono text-xs ${confClass(v.top.prob)}`}>
                      {Math.round(v.top.prob * 100)}% · {confLabel(v.top.prob)}
                    </span>
                  </span>
                ) : (
                  <span className="text-zinc-600">–</span>
                )}
              </td>
              <td className="px-3 py-2"><Cell pick={v.result} home={v.home} away={v.away} /></td>
              <td className="px-3 py-2"><Cell pick={v.goals} home={v.home} away={v.away} /></td>
              <td className="px-3 py-2"><Cell pick={v.corners} home={v.home} away={v.away} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
