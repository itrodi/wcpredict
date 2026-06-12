import type { Lineup, RefereeSignal, TeamSignal } from "@/lib/types";

type Side = { teamId: number | null; name: string };

/** Machine-built insight bullets for one fixture (spec v4.1 §5.5): templates
 * over real signals only — team_signals, referee history, lineup strength and
 * line movement. Same discipline as pick rationales: no free text invention. */
export function buildInsights({
  home,
  away,
  signals,
  referee,
  lineups,
  movement,
}: {
  home: Side;
  away: Side;
  signals: TeamSignal[];
  referee: RefereeSignal | null;
  lineups: Lineup[];
  movement: { selection: string; from: number; to: number } | null;
}): string[] {
  const bullets: string[] = [];
  const get = (teamId: number | null, signal: string) => {
    const row = signals.find((s) => s.team_id === teamId && s.signal === signal);
    return row ? Number(row.value) : null;
  };

  for (const side of [home, away]) {
    const over = get(side.teamId, "xg_overperf");
    if (over != null && Math.abs(over) >= 1.5) {
      bullets.push(
        over > 0
          ? `${side.name} scoring ${over.toFixed(1)} goals above chance quality (xG) — regression risk`
          : `${side.name} ${Math.abs(over).toFixed(1)} goals below xG — unlucky so far`
      );
    }
  }
  for (const side of [home, away]) {
    const form = get(side.teamId, "form_vs_elo");
    if (form != null && Math.abs(form) >= 2) {
      bullets.push(
        `${side.name} running ${form > 0 ? "hot" : "cold"}: ${form > 0 ? "+" : ""}${form.toFixed(1)} pts vs Elo expectation over the last 5`
      );
    }
  }
  const cfHome = get(home.teamId, "corner_pace_for");
  const caAway = get(away.teamId, "corner_pace_against");
  if (cfHome != null && caAway != null && cfHome + caAway >= 10) {
    bullets.push(
      `Corner pressure: ${home.name} win ${cfHome.toFixed(1)}/match, ${away.name} concede ${caAway.toFixed(1)}`
    );
  }
  for (const side of [home, away]) {
    const fh = get(side.teamId, "fh_share");
    if (fh != null && (fh >= 0.6 || fh <= 0.35)) {
      bullets.push(
        `${side.name} are ${fh >= 0.6 ? "fast" : "slow"} starters: ${Math.round(fh * 100)}% of their xG comes before half-time`
      );
    }
    const sp = get(side.teamId, "set_piece_xg_share");
    if (sp != null && sp >= 0.4) {
      bullets.push(`${Math.round(sp * 100)}% of ${side.name}'s threat comes from set pieces`);
    }
  }
  const luHome = lineups.find((l) => l.team_id === home.teamId);
  const luAway = lineups.find((l) => l.team_id === away.teamId);
  if (luHome?.strength != null && luAway?.strength != null) {
    bullets.push(
      `XI season ratings: ${home.name} ${Number(luHome.strength).toFixed(2)} vs ${away.name} ${Number(luAway.strength).toFixed(2)}`
    );
  }
  for (const [lu, side] of [[luHome, home], [luAway, away]] as const) {
    if (lu?.key_absences?.length) {
      bullets.push(`${side.name} missing top-rated ${lu.key_absences.join(", ")}`);
    }
  }
  if (referee?.avg_cards != null) {
    bullets.push(
      `Referee ${referee.referee}: avg ${Number(referee.avg_cards).toFixed(1)} cards/match` +
        (referee.avg_corners != null ? `, ${Number(referee.avg_corners).toFixed(1)} corners` : "") +
        ` (${referee.matches} matches tracked)`
    );
  }
  if (movement && Math.abs(movement.from - movement.to) >= 0.08) {
    const sel = movement.selection === "home" ? home.name : movement.selection === "away" ? away.name : "the draw";
    bullets.push(`Market move: ${sel} ${movement.from.toFixed(2)} → ${movement.to.toFixed(2)} since opening`);
  }
  return bullets.slice(0, 5);
}

export default function InsightsCard({ bullets }: { bullets: string[] }) {
  if (bullets.length === 0) return null;
  return (
    <section className="rounded-xl border border-accent/25 bg-pitch-900 p-4">
      <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-emerald-300">
        Insights
      </h3>
      <ul className="space-y-1.5 text-sm text-zinc-300">
        {bullets.map((b, i) => (
          <li key={i} className="flex gap-2">
            <span className="text-accent">▸</span>
            <span>{b}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
