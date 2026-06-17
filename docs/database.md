# Database

Schema lives in `supabase/migrations/` and must be run in order:

1. `0001_init.sql` — the v3 schema, RLS and Realtime publication.
2. `0002_dual_pipeline.sql` — v4, **additive only**: pipeline discriminator,
   identity mapping, Pipeline B payload tables, scoreboard.
3. `0003_v41.sql` — v4.1, additive: `ops_status`, `picks` (+ Realtime), `shots`,
   `team_signals`, `players`, `referee_signals`, `fixtures.referee`,
   `lineups.strength`/`key_absences`, `odds_snapshots.source`.
4. `0004_v42.sql` — v4.2, additive: `fixtures.duration`/`winner_id`/
   `ht_home_goals`/`ht_away_goals` (90-minute settlement + pens winners),
   `tournament_odds.reach_r16`, plus a one-off dedupe of `prediction_log`.
5. `0005_v43.sql` — v4.3, additive: `closing_odds` (CLV record), `picks.clv`/
   `closing_odds`, `model_scores.beats_baseline` (quality gate), and
   `fixture_incentives` (group-stage advancement leverage).
6. `0006_v47.sql` — v4.7, additive: `market_correlations` (+ Realtime) —
   same-game market-category correlations fitted from finished matches by
   `engine/correlations.py`, read by the `/strategies` same-game combo pricer.
7. `0007_v48.sql` — v4.8, additive: `score_grids` (+ Realtime) — per-fixture
   full-time scoreline grid, so `/strategies` prices exact goal-pair joints
   (result × goals-over × BTTS) instead of a copula approximation.
8. `0008_v49.sql` — v4.9, additive: `player_match_stats` — per-appearance player
   stats from `/matches/{id}/player-stats`, aggregated on the team page into
   per-metric "driver" rankings (goals/BTTS, creation, corners, card risk).

Seed data is `supabase/seed.sql` (idempotent — `on conflict (slug) do update`):
41 qualified teams with initial Elo approximated from eloratings.net.
`group_code` is deliberately NULL in the seed; the first ingest run fills it and
creates any team the seed didn't know (with `elo = 1600`).

## Security model

- **RLS on every table.** Display tables have a single `for select using (true)`
  policy → anyone (anon key) can read.
- **No insert/update/delete policies anywhere** → only the worker's
  service-role key can write.
- `api_cache` has **no policies at all** → invisible to the public entirely.

## Realtime publication

`match_predictions`, `tournament_odds`, `fixtures` (v3) + `match_stats`,
`lineups`, `model_scores` (v4) are in `supabase_realtime`.

## Tables

### teams
| column | notes |
|---|---|
| `id` serial PK | |
| `slug` unique | canonical identity; all vendor names resolve to this via `engine/aliases.py` |
| `name`, `confederation` | |
| `group_code` | `'A'`–`'L'`, filled by ingest |
| `elo` | Pipeline A rating (results-Elo), seeded then maintained by `ratings.py` |
| `elo_xg` | Pipeline B rating (xG-Elo), NULL until `ratings_xg.py` first writes; readers fall back to `elo` |

### fixtures
| column | notes |
|---|---|
| `ext_id` unique | football-data.org match id (also mirrored to `xmap_fixtures.fd_id`) |
| `stage` | `group` \| `R32` \| `R16` \| `QF` \| `SF` \| `3P` \| `F` |
| `group_code`, `venue`, `kickoff` | |
| `home_id`, `away_id` → teams | NULL while a knockout slot is undetermined |
| `host_home` | true when USA/Canada/Mexico are the home side → +100 Elo home advantage |
| `status` | `scheduled` \| `live` \| `finished` (free-tier scores are delayed; fine for the model) |
| `home_goals`, `away_goals` | football-data `fullTime` — **includes extra time**; check `duration` before treating it as the 90' score |
| `duration` | `REGULAR` \| `EXTRA_TIME` \| `PENALTY_SHOOTOUT` — settlement settles 90' markets only |
| `winner_id` → teams | decided winner (covers pens, where goals stay level); used by the simulation |
| `ht_home_goals`, `ht_away_goals` | half-time score — settles the 1H markets |
| `elo_applied`, `elo_xg_applied` | per-pipeline bookkeeping: each result folds into each rating exactly once |

### match_predictions
One row per (pipeline, fixture, market, selection) — that quadruple is the
unique key, which is what the worker upserts against.

| column | notes |
|---|---|
| `pipeline` | `free` \| `statsapi` \| `blend_free` \| `blend_statsapi` |
| `market`, `selection` | see the market catalogue in [engine.md](engine.md#market-catalogue) |
| `probability` | model probability |
| `fair_odds` | `1/probability` |
| `market_odds` | median book decimal odds (1X2 only in v1) |
| `edge` | `p_model − p_market(de-vigged)`; the de-vigged market prob is recoverable as `probability − edge` |
| `model_version` | `elo-poisson-v1` / `xgelo-dc-v1` / `blend-w0.7` |
| `computed_at` | **set explicitly by the worker on every upsert** — column defaults only fire on insert |

### tournament_odds
Per (pipeline, team, model_version): `advance_grp`, `reach_r16`, `reach_qf`,
`reach_sf`, `reach_final`, `champion`, `n_sims`, `computed_at`. Blends do not
simulate — only `free` and `statsapi` rows exist.

### odds_snapshots
Append-only history of every The Odds API pull: (fixture, bookmaker, market
`h2h`, selection home/draw/away, decimal_odds, fetched_at). Feeds de-vigging,
the line-movement chart and closing-line analysis. Pruned 14 days after a
fixture finishes.

### prediction_log
The calibration record. When a fixture finishes, its final pre-match
`match_predictions` rows are copied here once per pipeline with the realised
boolean `outcome`, plus synthesized `pipeline='market'` rows (the de-vigged
closing price) for 1X2.

### model_scores
The public scoreboard: per (pipeline, market) → `n` (distinct fixtures),
`brier`, `log_loss`, `beats_baseline`, `computed_at`. Recomputed from the full
`prediction_log` on every run by `engine/compare.py`. `beats_baseline` is true
when the model's log-loss is at or below a constant base-rate predictor's on the
same rows (NULL for the `market` pipeline). A market graduates from
"experimental" only when `n ≥ 30` **and** `beats_baseline` — sample size alone
no longer promotes a market into pick eligibility.

### closing_odds (v4.3)
The CLV record: per (fixture, market `1x2`/`ou25`, selection) → the de-vigged
median `decimal_odds` + `devigged_p`, re-upserted every refresh while the
fixture is `scheduled`. Once it leaves `scheduled` the row freezes, so the last
write is the closing price — and unlike `odds_snapshots` it is never pruned.
`write_db.settle_picks` reads it to stamp each pick's `clv`.

### fixture_incentives (v4.3)
Per unplayed group fixture (free pipeline sim): `P(advance | win/draw/loss)`
for each side, conditioned on the same Monte Carlo runs, plus a `mutual_draw`
flag. The picks engine suppresses dead rubbers (advancement barely moves with
the result for both teams) and mutual-draw fixtures.

### match_stats (Pipeline B)
Per (fixture, team, period `FT`/`1H`/`2H`): shots, shots_on_target, corners,
possession, xg, npxg, fouls, cards, passes, plus `source_payload` jsonb with
the raw vendor payload (kept only 14 days after finish, then nulled — the
modeled columns stay).

### lineups (Pipeline B)
Per (fixture, team): formation, `starters` jsonb `[{player_id, name, position,
shirt}]`, bench, `confirmed` flag.

### api_cache
Worker-side HTTP cache (replaces any external cache service): `cache_key` PK,
`payload` jsonb, `fetched_at`, `ttl_seconds`. Also stores odd bits of worker
state (e.g. the last seen Odds API credit count under `odds:remaining`).

### ops_status (v4.1)
Worker-written operational truth, one jsonb row per key: `fixture_count_fd`,
`fixture_count_statsapi`, `pipeline_a_coverage`, `unmapped_teams` (exact vendor
names, copy-paste-ready for aliases), `fixtures_missing_odds`,
`odds_credits_remaining`, `statsapi_odds` (the §5.0 probe result),
`last_refresh`. `/admin/health` reads this instead of recomputing.

### picks (v4.1, +v4.3)
The public picks ledger: (fixture, market, selection, tier banker|value,
probability, market_odds, edge, rationale jsonb bullets, published_at,
retired_at, outcome, `closing_odds`, `clv`). Immutable once published — material
changes retire the old row and insert a new one. **Every** published pick is
settled, retired ones included (they were followable while live; excluding them
would bias the record toward survivors), and stamped with CLV at settlement.

### shots (v4.1)
Per-shot xG from shotmaps: (fixture, team, minute, xg, is_goal, situation,
body_part, x, y). Feeds the derived signals.

### team_signals / referee_signals (v4.1)
Recomputed each run: per-team `xg_overperf`, `big_chance_rate`/`_against`,
`corner_pace_for`/`_against`, `fh_share`, `set_piece_xg_share`, `form_vs_elo`;
per-referee avg cards/fouls/corners. Display + rationale inputs only — never
model inputs in v4.1.

### players (v4.1)
Per-player season stats (vendor id PK, rating, minutes) refreshed weekly via
cache TTL. Used to compute `lineups.strength` (minutes-weighted XI rating) and
`lineups.key_absences` (top-3-rated squad players missing from the XI).

## Identity mapping

football-data.org, The Odds API and TheStatsAPI all use different ids and
sometimes different names ("Korea Republic" vs "South Korea", "Côte d'Ivoire"
vs "Ivory Coast", "DR Congo" vs "Congo DR").

- `xmap_teams` (PK `team_id`): `fd_id`, `statsapi_id`.
- `xmap_fixtures` (PK `fixture_id`): `fd_id`, `statsapi_id`.
- Pipeline A's ingest owns the `fd_id` side (it creates teams/fixtures);
  Pipeline B's ingest resolves and persists the `statsapi_id` side using the
  resolution ladder in `engine/ingest_statsapi.py`: existing xmap → exact name
  → alias/slug match → **log loudly and skip** (never auto-create a duplicate).
- Each upsert provides only its own vendor column, so the two sides never
  clobber each other.
- The worker asserts after ingest that every fixture has both ids; gaps appear
  on `/admin/health`.

**Never join on names at query time.** The alias dict in `engine/aliases.py`
exists only to *resolve* a vendor name to a canonical slug at ingest.

## Query discipline (the v4 double-counting trap)

After migration 0002, `match_predictions`, `tournament_odds` and
`prediction_log` contain multiple pipelines. **Every read must filter
`pipeline = <selected>`** or markets render duplicated. All frontend reads were
audited for this in Phase 1; keep it true for any new query.
