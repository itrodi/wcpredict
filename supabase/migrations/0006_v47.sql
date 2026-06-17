-- v4.7 migration — ADDITIVE ONLY
-- Same-game market correlations, fit from finished tournament matches by
-- engine/correlations.py and read by the /strategies same-game combo pricer.
-- One row per unordered, direction-normalized market-category pair (the ρ a
-- Gaussian copula consumes for the over/over orientation; the frontend flips
-- the sign for under/no selections).
create table market_correlations (
  category_a text not null,         -- 'btts' | 'corners' | 'ftgoals' | 'hfgoals' (category_a < category_b)
  category_b text not null,
  rho numeric(5,3) not null,        -- empirical-Bayes estimate, shrunk toward the prior by n
  n integer not null,               -- jointly-settleable finished matches behind the estimate
  computed_at timestamptz not null,
  primary key (category_a, category_b)
);
alter table market_correlations enable row level security;
create policy "public read mcorr" on market_correlations for select using (true);
alter publication supabase_realtime add table market_correlations;
