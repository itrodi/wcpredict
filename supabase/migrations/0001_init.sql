-- wcpredict schema · v1 (spec §4)
-- Public read via RLS; ALL writes go through the worker's service-role key.

create table teams (
  id serial primary key,
  slug text unique not null,
  name text not null,
  group_code text,
  elo integer not null,                -- seeded from eloratings.net
  confederation text,
  updated_at timestamptz default now()
);

create table fixtures (
  id serial primary key,
  ext_id text unique,                  -- football-data.org match id
  stage text not null,                 -- 'group' | 'R32' | 'R16' | 'QF' | 'SF' | '3P' | 'F'
  group_code text,
  home_id integer references teams(id),
  away_id integer references teams(id),
  kickoff timestamptz not null,
  venue text,
  host_home boolean default false,
  status text default 'scheduled',     -- scheduled | live | finished (free scores are delayed; see spec §9)
  home_goals integer,
  away_goals integer,
  elo_applied boolean default false    -- worker bookkeeping: result already folded into Elo
);

create table match_predictions (
  id serial primary key,
  fixture_id integer references fixtures(id),
  market text not null,                -- '1x2' | 'ou25' | 'btts' | 'cs'
  selection text not null,
  probability numeric(5,4) not null,
  fair_odds numeric(7,3),
  market_odds numeric(7,3),            -- latest de-vigged book odds (1x2 only in v1)
  edge numeric(6,4),
  model_version text,
  computed_at timestamptz not null,    -- set explicitly by the worker on every upsert
  unique (fixture_id, market, selection)
);

create table tournament_odds (
  id serial primary key,
  team_id integer references teams(id),
  advance_grp numeric(5,4),
  reach_qf numeric(5,4),
  reach_sf numeric(5,4),
  reach_final numeric(5,4),
  champion numeric(5,4),
  n_sims integer,
  model_version text,
  computed_at timestamptz not null,
  unique (team_id, model_version)
);

create table odds_snapshots (          -- every Odds API pull, for line-movement + backtest
  id serial primary key,
  fixture_id integer references fixtures(id),
  bookmaker text not null,
  market text not null,                -- 'h2h' in v1
  selection text not null,             -- home | draw | away
  decimal_odds numeric(7,3) not null,
  fetched_at timestamptz not null
);

create table prediction_log (          -- calibration / backtest
  id serial primary key,
  fixture_id integer references fixtures(id),
  market text,
  selection text,
  probability numeric(5,4),
  outcome boolean,
  logged_at timestamptz default now()
);

create table api_cache (               -- worker-side HTTP cache (replaces any external cache)
  cache_key text primary key,
  payload jsonb not null,
  fetched_at timestamptz not null,
  ttl_seconds integer not null
);

-- ---- Indexes for the hot read paths ----
create index fixtures_kickoff_idx       on fixtures (kickoff);
create index fixtures_status_idx        on fixtures (status);
create index mpred_fixture_idx          on match_predictions (fixture_id);
create index osnap_fixture_fetched_idx  on odds_snapshots (fixture_id, fetched_at desc);
create index plog_fixture_idx           on prediction_log (fixture_id);

-- ---- Row-Level Security ----
alter table teams              enable row level security;
alter table fixtures           enable row level security;
alter table match_predictions  enable row level security;
alter table tournament_odds    enable row level security;
alter table odds_snapshots     enable row level security;
alter table prediction_log     enable row level security;
alter table api_cache          enable row level security;

-- public read-only on display tables (anon + authenticated)
create policy "public read teams"    on teams             for select using (true);
create policy "public read fixtures" on fixtures          for select using (true);
create policy "public read mpred"    on match_predictions for select using (true);
create policy "public read todds"    on tournament_odds   for select using (true);
create policy "public read osnap"    on odds_snapshots    for select using (true);
create policy "public read plog"     on prediction_log    for select using (true);

-- api_cache: NO policies at all -> invisible to the public, worker-only via service role.
-- NO insert/update/delete policies anywhere -> only the service_role key (worker) can write.

-- ---- Enable Realtime on the live tables ----
alter publication supabase_realtime add table match_predictions;
alter publication supabase_realtime add table tournament_odds;
alter publication supabase_realtime add table fixtures;
