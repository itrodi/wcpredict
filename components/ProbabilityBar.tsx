import { pct } from "@/lib/format";

export default function ProbabilityBar({
  label,
  probability,
  highlight = false,
}: {
  label: string;
  probability: number;
  highlight?: boolean;
}) {
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="w-24 shrink-0 text-zinc-400">{label}</span>
      <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-pitch-800">
        <div
          className={`h-full rounded-full ${highlight ? "bg-accent" : "bg-emerald-700"}`}
          style={{ width: `${Math.min(100, probability * 100)}%` }}
        />
      </div>
      <span className="w-12 shrink-0 text-right font-mono text-zinc-200">{pct(probability)}</span>
    </div>
  );
}
