"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import ProbabilityBar from "@/components/ProbabilityBar";
import { kickoffFmt, odds } from "@/lib/format";
import { isExperimental, marketLabel, selectionLabel } from "@/lib/markets";
import { supabaseBrowser } from "@/lib/supabase/client";
import type { PickRow } from "@/lib/types";

const PICK_SELECT =
  "*, fixtures(id, kickoff, status, home:teams!fixtures_home_id_fkey(name), away:teams!fixtures_away_id_fkey(name))";

function Countdown({ kickoff }: { kickoff: string }) {
  const [label, setLabel] = useState("");
  useEffect(() => {
    const tick = () => {
      const ms = new Date(kickoff).getTime() - Date.now();
      if (ms <= 0) return setLabel("kicked off");
      const h = Math.floor(ms / 3600000);
      const m = Math.floor((ms % 3600000) / 60000);
      setLabel(h >= 48 ? `in ${Math.floor(h / 24)}d ${h % 24}h` : h > 0 ? `in ${h}h ${m}m` : `in ${m}m`);
    };
    tick();
    const t = setInterval(tick, 60000);
    return () => clearInterval(t);
  }, [kickoff]);
  return <span className="font-mono text-xs text-amber-300">{label}</span>;
}

function PickCard({ pick, experimental }: { pick: PickRow; experimental: boolean }) {
  return (
    <div className="rounded-xl border border-pitch-700 bg-pitch-900 p-4">
      <div className="mb-2 flex items-center justify-between gap-2 text-xs text-zinc-500">
        <Link href={`/matches/${pick.fixture_id}`} className="font-medium text-zinc-200 hover:text-accent">
          {pick.fixtures?.home?.name ?? "TBD"} v {pick.fixtures?.away?.name ?? "TBD"}
        </Link>
        {pick.fixtures && <Countdown kickoff={pick.fixtures.kickoff} />}
      </div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-zinc-100">
          {marketLabel(pick.market)} · {selectionLabel(pick.market, pick.selection)}
        </span>
        {experimental && (
          <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-400">
            experimental
          </span>
        )}
      </div>
      <ProbabilityBar label="Model" probability={Number(pick.probability)} highlight />
      <div className="mt-1.5 text-xs text-zinc-500">
        {pick.market_odds != null ? (
          <>
            book {odds(pick.market_odds)} · edge{" "}
            <span className={Number(pick.edge) > 0 ? "text-accent" : "text-zinc-400"}>
              {pick.edge != null
                ? `${Number(pick.edge) > 0 ? "+" : ""}${(Number(pick.edge) * 100).toFixed(1)}%`
                : "–"}
            </span>
          </>
        ) : (
          <>model fair {odds(1 / Number(pick.probability))} · no book price yet</>
        )}
        {" · "}published {kickoffFmt(pick.published_at)}
      </div>
      {pick.rationale && pick.rationale.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs text-zinc-400">
          {pick.rationale
            .filter((b) => !b.startsWith("[retired"))
            .map((b, i) => (
              <li key={i} className="flex gap-1.5">
                <span className="text-accent">▸</span>
                <span>{b}</span>
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}

/** Live pick lists with Realtime (spec v4.1 §4.3): any change to the picks
 * table triggers a refetch so new/retired picks appear without refresh. */
export default function PicksLive({
  initial,
  calibratedMarkets,
}: {
  initial: PickRow[];
  calibratedMarkets: string[];
}) {
  const [picks, setPicks] = useState<PickRow[]>(initial);

  useEffect(() => {
    setPicks(initial);
    const sb = supabaseBrowser();
    if (!sb) return;
    const refetch = async () => {
      const { data } = await sb
        .from("picks")
        .select(PICK_SELECT)
        .is("retired_at", null)
        .is("outcome", null)
        .order("published_at", { ascending: false });
      if (data) setPicks(data as unknown as PickRow[]);
    };
    const ch = sb
      .channel("picks-live")
      .on("postgres_changes", { event: "*", schema: "public", table: "picks" }, refetch)
      .subscribe();
    return () => {
      sb.removeChannel(ch);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const bankers = picks.filter((p) => p.tier === "banker");
  const value = picks.filter((p) => p.tier === "value");

  if (picks.length === 0) {
    return (
      <p className="rounded-xl border border-pitch-700 bg-pitch-900 p-6 text-sm text-zinc-500">
        No live picks right now — the engine publishes only selections that pass the calibration
        and edge gates, so an empty list is the rules working, not a bug.
      </p>
    );
  }

  return (
    <div className="grid gap-8 lg:grid-cols-2">
      <section>
        <h2 className="mb-3 text-lg font-bold text-zinc-100">
          Bankers{" "}
          <span className="text-sm font-normal text-zinc-500">
            high-confidence model plays — results &amp; overs (goals, corners)
          </span>
        </h2>
        <div className="space-y-4">
          {bankers.length === 0 && <p className="text-sm text-zinc-600">None live.</p>}
          {bankers.map((p) => (
            <PickCard key={p.id} pick={p} experimental={isExperimental(p.market, calibratedMarkets)} />
          ))}
        </div>
      </section>
      <section>
        <h2 className="mb-3 text-lg font-bold text-zinc-100">
          Value{" "}
          <span className="text-sm font-normal text-zinc-500">
            +EV vs the book (edge ≥ 4pts), priced markets only
          </span>
        </h2>
        <div className="space-y-4">
          {value.length === 0 && <p className="text-sm text-zinc-600">None live.</p>}
          {value.map((p) => (
            <PickCard key={p.id} pick={p} experimental={isExperimental(p.market, calibratedMarkets)} />
          ))}
        </div>
      </section>
    </div>
  );
}
