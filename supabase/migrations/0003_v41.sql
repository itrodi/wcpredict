-- v4.1 migration — ADDITIVE ONLY (spec v4.1 phases 1.4, 4.2, 5.0–5.4)

-- 1.4 ops_status: worker-written operational truth, read by /admin/health
create table ops_status (
  key text primary key,            -- 'fixture_count_fd', 'fixture_count_statsapi', 'unmapped_teams',
                                   -- 'last_refresh', 'odds_credits_remaining', 'statsapi_odds', ...
  value jsonb not null,
  updated_at timestamptz not null
);
alter table ops_status enable row level security;
create policy "public read ops" on ops_status for select using (true);

-- 4.2 picks: rule-generated, immutable once published, publicly settled
create table picks (
  id serial primary key,
  fixture_id integer references fixtures(id),
  pipeline text not null,           -- which view generated it ('site')
  market text not null,
  selection text not null,
  tier text not null,               -- 'banker' | 'value'
  probability numeric(5,4) not null,
  market_odds numeric(7,3),
  edge numeric(6,4),
  rationale jsonb,                  -- machine-built bullets + retire reason
  published_at timestamptz not null,
  retired_at timestamptz,           -- superseded before kickoff
  outcome boolean,                  -- filled when fixture finishes
  unique (fixture_id, market, selection, published_at)
);
alter table picks enable row level security;
create policy "public read picks" on picks for select using (true);
alter publication supabase_realtime add table picks;
create index picks_live_idx on picks (retired_at, outcome, published_at desc);

-- 5.1 shotmaps
create table shots (
  id serial primary key,
  fixture_id integer references fixtures(id),
  team_id integer references teams(id),
  minute integer,
  xg numeric(5,3),
  is_goal boolean,
  situation text,
  body_part text,
  x numeric(5,2),
  y numeric(5,2),
  unique (fixture_id, team_id, minute, x, y)
);
alter table shots enable row level security;
create policy "public read shots" on shots for select using (true);
create index shots_fixture_idx on shots (fixture_id);
create index shots_team_idx on shots (team_id);

-- 5.2 derived team signals
create table team_signals (
  id serial primary key,
  team_id integer references teams(id),
  signal text not null,             -- 'xg_overperf' | 'big_chance_rate' | 'corner_pace_for' | ...
  value numeric(8,3),
  window text,                      -- e.g. 'wc2026'
  computed_at timestamptz not null,
  unique (team_id, signal)
);
alter table team_signals enable row level security;
create policy "public read tsig" on team_signals for select using (true);

-- 5.3 players + lineup strength
create table players (
  statsapi_id text primary key,
  team_id integer references teams(id),
  name text,
  position text,
  rating numeric(5,2),
  minutes integer,
  fetched_at timestamptz not null
);
alter table players enable row level security;
create policy "public read players" on players for select using (true);
alter table lineups add column strength numeric(5,2);     -- minutes-weighted mean XI rating
alter table lineups add column key_absences jsonb;        -- top-3-rated squad players missing from XI

-- 5.4 referee context
alter table fixtures add column referee text;
create table referee_signals (
  referee text primary key,
  matches integer,
  avg_cards numeric(5,2),
  avg_fouls numeric(5,2),
  avg_corners numeric(5,2),
  computed_at timestamptz not null
);
alter table referee_signals enable row level security;
create policy "public read rsig" on referee_signals for select using (true);

-- 5.0 TheStatsAPI odds (used only if the plan turns out to include them)
alter table odds_snapshots add column source text not null default 'oddsapi';
