"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

/** Safe | Value mode toggle for the matchday strategies page. Mirrors
 * ModelSwitcher; kept self-contained so the strategy math never reaches the
 * client bundle. */
const MODES = [
  { id: "safe", label: "Safe" },
  { id: "value", label: "Value" },
] as const;

export default function StrategyToggle({ active }: { active: "safe" | "value" }) {
  const pathname = usePathname();
  const params = useSearchParams();

  const href = (mode: string) => {
    const q = new URLSearchParams(params.toString());
    q.set("strategy", mode);
    return `${pathname}?${q.toString()}`;
  };

  return (
    <div className="inline-flex gap-1 rounded-lg border border-pitch-700 bg-pitch-900 p-1 text-xs">
      {MODES.map((m) => (
        <Link
          key={m.id}
          href={href(m.id)}
          className={`rounded-md px-3 py-1 transition ${
            m.id === active
              ? "bg-accent/15 font-semibold text-accent"
              : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          {m.label}
        </Link>
      ))}
    </div>
  );
}
