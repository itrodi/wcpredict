"use client";

import { useEffect, useState } from "react";

/** Honest freshness badge — shows how old the worker's numbers are (spec §6). */
export default function UpdatedBadge({ computedAt }: { computedAt: string | null }) {
  const [label, setLabel] = useState<string>("");

  useEffect(() => {
    if (!computedAt) return;
    const update = () => {
      const mins = Math.max(0, Math.round((Date.now() - new Date(computedAt).getTime()) / 60000));
      setLabel(
        mins < 1 ? "updated just now" : mins < 60 ? `updated ${mins}m ago` : `updated ${Math.round(mins / 60)}h ago`
      );
    };
    update();
    const t = setInterval(update, 60000);
    return () => clearInterval(t);
  }, [computedAt]);

  if (!computedAt) return null;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-pitch-700 bg-pitch-800 px-2.5 py-0.5 text-xs text-emerald-300/80">
      <span className="h-1.5 w-1.5 rounded-full bg-accent" />
      {label}
    </span>
  );
}
