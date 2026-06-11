import { createClient, SupabaseClient } from "@supabase/supabase-js";

/** Server Components read with the publishable/anon key — safe only because RLS is on.
 * Returns null when env vars are absent (e.g. CI builds) so pages can render an
 * empty state instead of crashing. */
export function supabaseServer(): SupabaseClient | null {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) return null;
  return createClient(url, key, { auth: { persistSession: false } });
}
