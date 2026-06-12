"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

import { PIPELINE_LABELS, PIPELINES, type Pipeline } from "@/lib/pipeline";

/** Global pipeline toggle (spec v4 §6.1), persisted in ?model=. */
export default function ModelSwitcher({ active }: { active: Pipeline }) {
  const pathname = usePathname();
  const params = useSearchParams();

  const href = (p: Pipeline) => {
    const q = new URLSearchParams(params.toString());
    q.set("model", p);
    return `${pathname}?${q.toString()}`;
  };

  return (
    <div className="inline-flex flex-wrap gap-1 rounded-lg border border-pitch-700 bg-pitch-900 p-1 text-xs">
      {PIPELINES.map((p) => (
        <Link
          key={p}
          href={href(p)}
          className={`rounded-md px-2.5 py-1 transition ${
            p === active
              ? "bg-accent/15 font-semibold text-accent"
              : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          {PIPELINE_LABELS[p]}
        </Link>
      ))}
    </div>
  );
}
