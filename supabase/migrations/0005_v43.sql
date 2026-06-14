-- v4.3 migration — ADDITIVE ONLY (betting-discipline upgrades, June 2026)
--
-- Idempotent + schema-qualified: safe to re-run, and resolves `fixtures` via
-- public.* regardless of the session search_path. NOTE: this must be run on the
-- SAME Supabase project your engine's SUPABASE_URL points to — that project
-- already has public.fixtures (the worker writes 104 fixtures to it). If you get
-- 'relation "fixtures" does not exist', you are on the wrong/empty project; run
--   select count(*) from public.fixtures;
-- in this editor first — it must return your fixture count, not an error.

-- Closing line value (CLV): the last pre-kickoff de-vigged median per
-- (fixture, market, selection). Written every run while the fixture is still
-- 'scheduled', so the final write IS the closing price — and it survives
-- odds_snapshots pruning, unlike the raw snapshots.
create table if not exists public.closing_odds (
  id serial primary key,
  fixture_id integer references public.fixtures(id),
  market text not null,              -- '1x2' | 'ou25'
  selection text not null,
  decimal_odds numeric(7,3) not null,
  devigged_p numeric(5,4),
  recorded_at timestamptz not null,
  unique (fixture_id, market, selection)
);
alter table public.closing_odds enable row level security;
drop policy if exists "public read codds" on public.closing_odds;
create policy "public read codds" on public.closing_odds for select using (true);

-- Picks carry their CLV once settled: clv = published_odds/closing_odds − 1
-- (positive = beat the close — the leading indicator of real edge).
alter table public.picks add column if not exists closing_odds numeric(7,3);
alter table public.picks add column if not exists clv numeric(6,4);

-- Calibration gate quality check: a market is only "calibrated" when the
-- model also beats a base-rate predictor on log-loss (sample size alone was
-- the old gate). NULL for the 'market' baseline pipeline itself.
alter table public.model_scores add column if not exists beats_baseline boolean;

-- Group-stage incentive context from the Monte Carlo (free pipeline):
-- P(advance | win/draw/loss) per side for each unplayed group fixture.
-- Feeds the picks engine's dead-rubber / mutual-draw suppression.
create table if not exists public.fixture_incentives (
  fixture_id integer references public.fixtures(id) primary key,
  home_adv_win numeric(5,4),
  home_adv_draw numeric(5,4),
  home_adv_loss numeric(5,4),
  away_adv_win numeric(5,4),
  away_adv_draw numeric(5,4),
  away_adv_loss numeric(5,4),
  mutual_draw boolean,
  n_sims integer,
  computed_at timestamptz not null
);
alter table public.fixture_incentives enable row level security;
drop policy if exists "public read finc" on public.fixture_incentives;
create policy "public read finc" on public.fixture_incentives for select using (true);
