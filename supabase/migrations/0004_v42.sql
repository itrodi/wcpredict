-- v4.2 migration — ADDITIVE ONLY (review fixes, June 2026)

-- Knockout-correct settlement (review #3): football-data's score.fullTime
-- includes extra time, but every market the engine prices (and every book
-- price it compares against) is 90 minutes. Store enough to settle correctly:
--   duration   'REGULAR' | 'EXTRA_TIME' | 'PENALTY_SHOOTOUT' (fd score.duration)
--   winner_id  the decided winner (covers pens, where goals stay level)
--   ht goals   half-time score, so 1H markets (ht_1x2/ou05_1h/ou15_1h/htft)
--              actually settle instead of staying unfalsifiable forever
alter table fixtures add column duration text not null default 'REGULAR';
alter table fixtures add column winner_id integer references teams(id);
alter table fixtures add column ht_home_goals integer;
alter table fixtures add column ht_away_goals integer;

-- Simulation now reports R32->R16 advancement (it was computed and discarded)
alter table tournament_odds add column reach_r16 numeric(5,4);

-- One-off cleanup (review #1): log_finished_predictions read its "already
-- logged" set without pagination, so past runs may have re-inserted duplicate
-- prediction_log rows once the table passed PostgREST's 1000-row default cap.
-- Keep the earliest row per (pipeline, fixture, market, selection).
delete from prediction_log a
using prediction_log b
where a.id > b.id
  and a.fixture_id = b.fixture_id
  and a.pipeline = b.pipeline
  and a.market = b.market
  and a.selection = b.selection;
