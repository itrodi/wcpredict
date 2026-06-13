-- v4.3 migration — ADDITIVE ONLY (betting-discipline upgrades, June 2026)

-- Closing line value (CLV): the last pre-kickoff de-vigged median per
-- (fixture, market, selection). Written every run while the fixture is still
-- 'scheduled', so the final write IS the closing price — and it survives
-- odds_snapshots pruning, unlike the raw snapshots.
create table closing_odds (
  id serial primary key,
  fixture_id integer references fixtures(id),
  market text not null,              -- '1x2' | 'ou25'
  selection text not null,
  decimal_odds numeric(7,3) not null,
  devigged_p numeric(5,4),
  recorded_at timestamptz not null,
  unique (fixture_id, market, selection)
);
alter table closing_odds enable row level security;
create policy "public read codds" on closing_odds for select using (true);

-- Picks carry their CLV once settled: clv = published_odds/closing_odds − 1
-- (positive = beat the close — the leading indicator of real edge).
alter table picks add column closing_odds numeric(7,3);
alter table picks add column clv numeric(6,4);

-- Calibration gate quality check: a market is only "calibrated" when the
-- model also beats a base-rate predictor on log-loss (sample size alone was
-- the old gate). NULL for the 'market' baseline pipeline itself.
alter table model_scores add column beats_baseline boolean;

-- Group-stage incentive context from the Monte Carlo (free pipeline):
-- P(advance | win/draw/loss) per side for each unplayed group fixture.
-- Feeds the picks engine's dead-rubber / mutual-draw suppression.
create table fixture_incentives (
  fixture_id integer references fixtures(id) primary key,
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
alter table fixture_incentives enable row level security;
create policy "public read finc" on fixture_incentives for select using (true);
