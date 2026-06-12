-- v4 dual-pipeline migration (spec v4 §2) — ADDITIVE ONLY, Pipeline A untouched.

-- 2.1 pipeline discriminator on prediction tables
alter table match_predictions add column pipeline text not null default 'free';
alter table tournament_odds   add column pipeline text not null default 'free';
alter table prediction_log    add column pipeline text not null default 'free';

-- replace unique constraints to include pipeline
alter table match_predictions drop constraint match_predictions_fixture_id_market_selection_key;
alter table match_predictions add unique (pipeline, fixture_id, market, selection);
alter table tournament_odds   drop constraint tournament_odds_team_id_model_version_key;
alter table tournament_odds   add unique (pipeline, team_id, model_version);

-- Pipeline B rating + bookkeeping (additive columns; A's elo/elo_applied untouched)
alter table teams    add column elo_xg integer;                       -- xG-adjusted Elo; null -> falls back to elo
alter table fixtures add column elo_xg_applied boolean default false; -- B applies each result once, independently of A

-- 2.2 cross-vendor identity mapping (spec §4 — never join on names at query time)
create table xmap_teams (
  team_id integer references teams(id) primary key,
  fd_id text,                   -- football-data.org team id
  statsapi_id text              -- TheStatsAPI team id
);

create table xmap_fixtures (
  fixture_id integer references fixtures(id) primary key,
  fd_id text,
  statsapi_id text
);

-- 2.3 TheStatsAPI payload tables
create table match_stats (        -- finalized + live per-team match stats
  id serial primary key,
  fixture_id integer references fixtures(id),
  team_id integer references teams(id),
  is_home boolean,
  period text not null default 'FT',     -- 'FT' | '1H' | '2H' (store what the API gives)
  shots integer,
  shots_on_target integer,
  corners integer,
  possession numeric(5,2),
  xg numeric(6,3),
  npxg numeric(6,3),
  fouls integer,
  yellows integer,
  reds integer,
  passes integer,
  source_payload jsonb,                  -- keep raw for fields we didn't model
  fetched_at timestamptz not null,
  unique (fixture_id, team_id, period)
);

create table lineups (
  id serial primary key,
  fixture_id integer references fixtures(id),
  team_id integer references teams(id),
  formation text,
  starters jsonb not null,       -- [{player_id, name, position, shirt}]
  bench jsonb,
  confirmed boolean default false,
  fetched_at timestamptz not null,
  unique (fixture_id, team_id)
);

-- 2.4 model comparison scoreboard
create table model_scores (
  id serial primary key,
  pipeline text not null,        -- 'free' | 'statsapi' | 'blend_free' | 'blend_statsapi' | 'market'
  market text not null,          -- '1x2' | 'ou25' | 'btts' | 'corners_o95' | 'ht_1x2' ...
  n integer not null,
  brier numeric(8,6),
  log_loss numeric(8,6),
  computed_at timestamptz not null,
  unique (pipeline, market)
);

-- indexes for the new hot paths
create index mstats_fixture_idx on match_stats (fixture_id);
create index lineups_fixture_idx on lineups (fixture_id);
create index mpred_pipeline_idx on match_predictions (pipeline, fixture_id);
create index todds_pipeline_idx on tournament_odds (pipeline);

-- 2.5 RLS: public read on all new tables; no write policies (worker-only), same pattern as v3
alter table xmap_teams    enable row level security;
alter table xmap_fixtures enable row level security;
alter table match_stats   enable row level security;
alter table lineups       enable row level security;
alter table model_scores  enable row level security;

create policy "public read xt"  on xmap_teams    for select using (true);
create policy "public read xf"  on xmap_fixtures for select using (true);
create policy "public read ms"  on match_stats   for select using (true);
create policy "public read lu"  on lineups       for select using (true);
create policy "public read sc"  on model_scores  for select using (true);

-- 2.6 Realtime additions
alter publication supabase_realtime add table match_stats;
alter publication supabase_realtime add table lineups;
alter publication supabase_realtime add table model_scores;
