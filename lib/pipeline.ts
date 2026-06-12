import type { SupabaseClient } from "@supabase/supabase-js";

/** Pipeline selection (spec v4 §6.1) — persisted in the URL as ?model=... */
export const PIPELINES = ["free", "statsapi", "blend_free", "blend_statsapi"] as const;
export type Pipeline = (typeof PIPELINES)[number];

export const PIPELINE_LABELS: Record<string, string> = {
  free: "Free model",
  statsapi: "Stats model",
  blend_free: "Blend (free)",
  blend_statsapi: "Blend (stats)",
  market: "Market",
};

export function parsePipeline(value: string | string[] | undefined): Pipeline | null {
  const v = Array.isArray(value) ? value[0] : value;
  return PIPELINES.includes(v as Pipeline) ? (v as Pipeline) : null;
}

/** Default: blend_statsapi once it has data, else free (spec v4 §6.1). */
export async function resolvePipeline(
  searchParams: { [key: string]: string | string[] | undefined },
  sb: SupabaseClient | null
): Promise<Pipeline> {
  const explicit = parsePipeline(searchParams["model"]);
  if (explicit) return explicit;
  if (sb) {
    const { count } = await sb
      .from("match_predictions")
      .select("id", { count: "exact", head: true })
      .eq("pipeline", "blend_statsapi");
    if ((count ?? 0) > 0) return "blend_statsapi";
  }
  return "free";
}

/** Blends only exist for 1X2 match markets; tournament sims + exotic markets
 * come from the underlying model pipeline. */
export function basePipeline(p: Pipeline): "free" | "statsapi" {
  return p === "statsapi" || p === "blend_statsapi" ? "statsapi" : "free";
}
