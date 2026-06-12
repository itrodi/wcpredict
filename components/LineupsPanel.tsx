import type { Lineup } from "@/lib/types";

function Eleven({ lineup, teamName }: { lineup: Lineup | undefined; teamName: string }) {
  if (!lineup) {
    return (
      <div className="flex-1">
        <h4 className="mb-2 text-sm font-medium text-zinc-300">{teamName}</h4>
        <p className="text-xs text-zinc-600">Lineup not available yet.</p>
      </div>
    );
  }
  return (
    <div className="flex-1">
      <h4 className="mb-2 flex flex-wrap items-center gap-2 text-sm font-medium text-zinc-300">
        {teamName}
        {lineup.formation && <span className="font-mono text-xs text-zinc-500">{lineup.formation}</span>}
        {lineup.confirmed ? (
          <span className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[10px] text-emerald-400">
            confirmed
          </span>
        ) : (
          <span className="rounded-full border border-pitch-700 bg-pitch-800 px-2 py-0.5 text-[10px] text-zinc-500">
            probable
          </span>
        )}
        {lineup.strength != null && (
          <span className="font-mono text-xs text-emerald-300">
            XI rating {Number(lineup.strength).toFixed(2)}
          </span>
        )}
      </h4>
      {lineup.key_absences && lineup.key_absences.length > 0 && (
        <p className="mb-2 text-xs text-amber-400">
          Missing top-rated: {lineup.key_absences.join(", ")}
        </p>
      )}
      <ol className="space-y-1 text-xs text-zinc-400">
        {(lineup.starters ?? []).map((p, i) => (
          <li key={`${p.name}-${i}`} className="flex items-baseline gap-2">
            <span className="w-5 shrink-0 text-right font-mono text-zinc-600">{p.shirt ?? "·"}</span>
            <span className="text-zinc-200">{p.name}</span>
            {p.position && <span className="text-zinc-600">{p.position}</span>}
          </li>
        ))}
      </ol>
    </div>
  );
}

/** Formation + XI from lineups (Pipeline B data, spec v4 §6.5). */
export default function LineupsPanel({
  homeTeamId,
  homeName,
  awayName,
  lineups,
}: {
  homeTeamId: number | null;
  homeName: string;
  awayName: string;
  lineups: Lineup[];
}) {
  if (lineups.length === 0) return null;
  const home = lineups.find((l) => l.team_id === homeTeamId);
  const away = lineups.find((l) => l !== home);
  return (
    <section className="rounded-xl border border-pitch-700 bg-pitch-900 p-4">
      <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-400">Lineups</h3>
      <div className="flex gap-6">
        <Eleven lineup={home} teamName={homeName} />
        <Eleven lineup={away} teamName={awayName} />
      </div>
    </section>
  );
}
