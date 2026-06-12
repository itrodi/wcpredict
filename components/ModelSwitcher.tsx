"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

import { VIEW_LABELS, VIEWS, type View } from "@/lib/pipeline";

/** Two-pill view toggle (spec v4.1 Phase 3): Site Picks | Baseline. */
export default function ModelSwitcher({ active }: { active: View }) {
  const pathname = usePathname();
  const params = useSearchParams();

  const href = (v: View) => {
    const q = new URLSearchParams(params.toString());
    q.delete("model"); // retire legacy param on navigation
    q.set("view", v);
    return `${pathname}?${q.toString()}`;
  };

  return (
    <div className="inline-flex gap-1 rounded-lg border border-pitch-700 bg-pitch-900 p-1 text-xs">
      {VIEWS.map((v) => (
        <Link
          key={v}
          href={href(v)}
          className={`rounded-md px-3 py-1 transition ${
            v === active
              ? "bg-accent/15 font-semibold text-accent"
              : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          {VIEW_LABELS[v]}
        </Link>
      ))}
    </div>
  );
}
