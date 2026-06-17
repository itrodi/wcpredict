-- v4.8 migration — ADDITIVE ONLY
-- Per-fixture full-time scoreline grid (free Elo→Poisson model), so the
-- /strategies same-game pricer can compute EXACT joints for goal-derived market
-- pairs (result × goals-over × BTTS) instead of a copula approximation. One
-- coherent joint distribution over (home goals, away goals); the copula is kept
-- only for pairs that touch corners or first-half goals (not in this grid).
create table score_grids (
  fixture_id integer primary key references fixtures(id),
  grid jsonb not null,          -- grid[i][j] = P(home i goals, away j goals), 0..7 each
  computed_at timestamptz not null
);
alter table score_grids enable row level security;
create policy "public read grids" on score_grids for select using (true);
alter publication supabase_realtime add table score_grids;
