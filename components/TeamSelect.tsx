"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

/** Team filter for /fixtures — navigates with ?team=<slug>. */
export default function TeamSelect({
  teams,
  selected,
}: {
  teams: { slug: string; name: string }[];
  selected: string | null;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  return (
    <select
      value={selected ?? ""}
      onChange={(e) => {
        const q = new URLSearchParams(params.toString());
        if (e.target.value) q.set("team", e.target.value);
        else q.delete("team");
        router.push(`${pathname}?${q.toString()}`);
      }}
      className="rounded-lg border border-pitch-700 bg-pitch-900 px-2.5 py-1.5 text-xs text-zinc-300 focus:border-accent/50 focus:outline-none"
    >
      <option value="">By team…</option>
      {teams.map((t) => (
        <option key={t.slug} value={t.slug}>
          {t.name}
        </option>
      ))}
    </select>
  );
}
