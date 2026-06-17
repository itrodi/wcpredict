-- v4.9 migration — ADDITIVE ONLY
-- Per-appearance player stats from /matches/{id}/player-stats, aggregated on the
-- team page to show which players drive each priced metric (goals/BTTS, chance
-- creation, corner pressure, card risk). Mirrors how match_stats feeds the team
-- signals — ingested for finished mapped fixtures, grows through the tournament.
create table player_match_stats (
  id serial primary key,
  fixture_id integer references fixtures(id),
  team_id integer references teams(id),
  statsapi_id text not null,        -- vendor player id (pl_…)
  name text,
  position text,
  minutes integer,
  rating numeric(5,2),
  goals integer,
  shots integer,
  shots_on_target integer,
  key_passes integer,
  duels_won integer,
  dribbles integer,                 -- dribbles succeeded
  fouls_drawn integer,
  fouls_committed integer,
  yellows integer,
  reds integer,
  computed_at timestamptz not null,
  unique (fixture_id, statsapi_id)
);
alter table player_match_stats enable row level security;
create policy "public read pms" on player_match_stats for select using (true);
create index player_match_stats_team_idx on player_match_stats (team_id);
