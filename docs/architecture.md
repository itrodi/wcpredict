# Architecture

## The defining rule

Monte Carlo simulation, model fitting and bulk ingestion **never run inside a
Vercel function or a Supabase Edge Function**. They run on GitHub Actions (no
meaningful time limit, full Python/numpy), write to Supabase with the
service-role key, and the website only ever *reads*.

```
   ┌──────────────────────────────────┐                ┌──────────────────────────────┐
   │ COMPUTE PLANE (GitHub Actions)    │   writes via   │ SUPABASE                      │
   │ Python engine (engine/)           │  service role  │ • Postgres (all data)         │
   │                                   │ ─────────────▶ │ • RLS: public read-only       │
   │ Pipeline A: football-data.org    │                │ • Realtime (row pushes)       │
   │ Pipeline B: TheStatsAPI          │                │ • auto REST API (PostgREST)   │
   │ Shared:     The Odds API         │                └───────────────┬──────────────┘
   └──────────────────────────────────┘                                │
              ▲                                          reads + subscribes
              │ cron                                                   │
     refresh.yml (6h / hourly)                        ┌───────────────▼──────────────┐
     live.yml    (5 min, matchdays)                   │ VERCEL — Next.js App Router    │
                                                      │ • Server Components read DB    │
                                                      │ • clients subscribe Realtime   │
                                                      │ • ISR revalidate = 300s        │
                                                      └────────────────────────────────┘
```

## The two pipelines

Everything prediction-shaped carries a `pipeline` discriminator so data never
mixes silently.

| | Pipeline A — `free` | Pipeline B — `statsapi` |
|---|---|---|
| Fixtures/results | football-data.org (`WC`, free tier) | TheStatsAPI (paid, stats-only plan) |
| Extra inputs | — | match stats: xG, corners, shots, possession; lineups |
| Odds | The Odds API (shared) | The Odds API (shared — the stats plan has **no** odds endpoints) |
| Rating | results-Elo (`teams.elo`) | xG-adjusted Elo (`teams.elo_xg`) |
| Match model | Elo→Poisson | xG-Elo→Dixon-Coles Poisson |
| Markets | 1x2, O/U 2.5, BTTS, correct score | A's markets **plus** HT 1X2, 1H O/U 0.5 & 1.5, HT/FT, total + team corners |
| Tournament sim | yes (`tournament_odds.pipeline='free'`) | yes (`'statsapi'`) |
| Model version | `elo-poisson-v2` | `xgelo-dc-v2` |

Derived pipelines (1X2 only):

- **`blend_free`**, **`blend_statsapi`** — `p = 0.7·p_market + 0.3·p_model`,
  written by `engine/blend.py` per parent model.
- **`market`** — the de-vigged consensus price itself. Exists only in
  `prediction_log` / `model_scores` as the scoring baseline both models must beat.

## Hard rules (inherited from the v4 spec)

1. **Pipeline A is never removed or rewired.** Migrations are additive only. If
   TheStatsAPI breaks (or `STATSAPI_KEY` is simply absent), the engine runs A
   alone and the site keeps working — every Pipeline B stage in
   `engine/main.py` is individually try/except-fenced.
2. The Odds API is the single odds source for **both** pipelines.
3. Cross-vendor joins go only through `xmap_teams` / `xmap_fixtures`, never
   through names at query time (see [database.md](database.md#identity-mapping)).
4. All worker keys (including `STATSAPI_KEY`) live only in GitHub Actions
   secrets. The browser only ever holds the Supabase publishable key, which is
   safe solely because RLS is read-only.

## Data flow per refresh run (`engine/main.py`)

```
ingest_fd ─▶ ingest_odds ─▶ ingest_statsapi ─▶ ratings ─▶ ratings_xg
                                                              │
   prediction_log (outcomes for newly finished fixtures) ◀────┘
                                                              │
match_model (A rows) + match_model_b (B rows) ─▶ blend ─▶ upsert match_predictions
                                                              │
simulate(free) ─▶ upsert  ·  simulate(statsapi) ─▶ upsert tournament_odds
                                                              │
compare (Brier/log-loss → model_scores) ─▶ prune snapshots + raw payloads
```

Each stage logs and continues on failure; an exhausted odds budget or a vendor
outage degrades the run (stale `edge`, missing B rows) but never blocks fresh
Pipeline A predictions.

## Realtime, correctly framed

Supabase Realtime pushes row changes to subscribed browsers the moment the
worker upserts. Users see refresh-free updates **at worker cadence** (hourly on
matchdays for predictions; every ~5 minutes for live match stats via
`live.yml`) — not tick-by-tick in-play repricing. Live model re-pricing remains
the documented stretch goal.
