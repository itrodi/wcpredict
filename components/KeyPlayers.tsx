import {
  aggregatePlayers,
  DRIVER_CATEGORIES,
  type PlayerAggregate,
  type PlayerMatchStat,
  topDrivers,
} from "@/lib/playerDrivers";

function TeamColumn({ name, players }: { name: string; players: PlayerAggregate[] }) {
  const rows = DRIVER_CATEGORIES.map((cat) => ({ cat, top: topDrivers(players, cat, 1)[0] })).filter(
    (r) => r.top
  );
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-zinc-200">{name}</h3>
      {rows.length === 0 ? (
        <p className="text-xs text-zinc-600">No player data yet.</p>
      ) : (
        <ul className="space-y-2 text-xs">
          {rows.map(({ cat, top }) => (
            <li key={cat.key}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="truncate text-zinc-300">
                  {top!.player.name}
                  {top!.player.position && (
                    <span className="ml-1 text-[10px] text-zinc-600">{top!.player.position}</span>
                  )}
                </span>
                <span className="shrink-0 font-mono text-zinc-500">{cat.detail(top!.player)}</span>
              </div>
              <div className="text-[10px] uppercase tracking-wide text-zinc-600">
                {cat.label} · drives {cat.drives}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Per-side key players (top driver in each metric category) as context beside
 * the prediction — display/reference only, never a model input. */
export default function KeyPlayers({
  homeName,
  awayName,
  rows,
  homeTeamId,
  awayTeamId,
}: {
  homeName: string;
  awayName: string;
  rows: PlayerMatchStat[];
  homeTeamId: number | null;
  awayTeamId: number | null;
}) {
  const home = aggregatePlayers(rows.filter((r) => r.team_id === homeTeamId));
  const away = aggregatePlayers(rows.filter((r) => r.team_id === awayTeamId));
  if (home.length === 0 && away.length === 0) return null;
  return (
    <section className="rounded-xl border border-pitch-700 bg-pitch-900 p-4">
      <h2 className="mb-1 text-sm font-semibold uppercase tracking-wide text-zinc-400">
        Key players to watch
      </h2>
      <p className="mb-4 text-xs text-zinc-500">
        Each side&rsquo;s standout per metric, from per-match player stats — context for the
        prediction, not an input to it.
      </p>
      <div className="grid gap-x-8 gap-y-4 sm:grid-cols-2">
        <TeamColumn name={homeName} players={home} />
        <TeamColumn name={awayName} players={away} />
      </div>
    </section>
  );
}
