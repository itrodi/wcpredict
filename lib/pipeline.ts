import type { SupabaseClient } from "@supabase/supabase-js";

/** Two-view simplification (spec v4.1 Phase 3): users choose between exactly
 * two views; all pipelines stay in the DB for scoring.
 *
 *  Site Picks (default) -> blend_statsapi for 1X2, statsapi for everything else
 *  Baseline (free data) -> blend_free for 1X2, free for everything else
 *
 * Fallback rule: until Pipeline B has rows (or if it is down), "Site Picks"
 * silently resolves to the free family and the badge says "Baseline model" —
 * never an empty page. */

export type View = "site" | "baseline";
export const VIEWS: View[] = ["site", "baseline"];

export const VIEW_LABELS: Record<View, string> = {
  site: "Site Picks",
  baseline: "Baseline",
};

/** Badge / scoreboard names. Raw pipeline ids never reach the UI. */
export const PIPELINE_LABELS: Record<string, string> = {
  free: "Baseline model",
  statsapi: "Site model",
  blend_free: "Baseline blend",
  blend_statsapi: "Site blend",
  market: "Market",
};

/** Legacy deep links (?model=...) keep resolving (spec v4.1 Phase 3). */
const LEGACY_MODEL_TO_VIEW: Record<string, View> = {
  free: "baseline",
  blend_free: "baseline",
  statsapi: "site",
  blend_statsapi: "site",
};

export function parseView(searchParams: {
  [key: string]: string | string[] | undefined;
}): View {
  const raw = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);
  const view = raw(searchParams["view"]);
  if (view === "site" || view === "baseline") return view;
  const legacy = raw(searchParams["model"]);
  if (legacy && LEGACY_MODEL_TO_VIEW[legacy]) return LEGACY_MODEL_TO_VIEW[legacy];
  return "site";
}

export type ResolvedView = {
  view: View;            // what the URL asked for (drives the switcher)
  label: string;         // effective badge text after the fallback rule
  blendPipeline: string; // pipeline that owns 1X2 rows
  modelPipeline: string; // pipeline that owns all other markets + sims
};

export async function resolveView(
  searchParams: { [key: string]: string | string[] | undefined },
  sb: SupabaseClient | null
): Promise<ResolvedView> {
  const view = parseView(searchParams);
  if (view === "baseline") {
    return { view, label: "Baseline model", blendPipeline: "blend_free", modelPipeline: "free" };
  }
  let hasB = false;
  if (sb) {
    const { count } = await sb
      .from("match_predictions")
      .select("id", { count: "exact", head: true })
      .eq("pipeline", "statsapi");
    hasB = (count ?? 0) > 0;
  }
  return hasB
    ? { view, label: "Site model", blendPipeline: "blend_statsapi", modelPipeline: "statsapi" }
    : { view, label: "Baseline model", blendPipeline: "blend_free", modelPipeline: "free" };
}

/** Merge prediction rows from [blendPipeline, modelPipeline] into the view's
 * display set: the blend owns 1X2 (falling back to the model row when no blend
 * row exists, e.g. no odds yet); the model pipeline owns every other market. */
export function mergeViewRows<T extends { pipeline: string; market: string; selection: string }>(
  rows: T[],
  resolved: Pick<ResolvedView, "blendPipeline" | "modelPipeline">
): T[] {
  const out = new Map<string, T>();
  for (const r of rows) {
    if (r.market === "1x2") {
      const key = `1x2:${r.selection}`;
      const existing = out.get(key);
      if (r.pipeline === resolved.blendPipeline || !existing) out.set(key, r);
    } else if (r.pipeline === resolved.modelPipeline) {
      out.set(`${r.market}:${r.selection}`, r);
    }
  }
  return [...out.values()];
}
