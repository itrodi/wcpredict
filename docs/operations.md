# Operations

## Environments & secrets

| Where | Variable | Purpose |
|---|---|---|
| Vercel | `NEXT_PUBLIC_SUPABASE_URL` | project URL |
| Vercel | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | publishable key — safe in the browser **only because RLS is read-only** |
| GitHub Actions | `SUPABASE_URL` | same project URL |
| GitHub Actions | `SUPABASE_SERVICE_ROLE_KEY` | secret key; bypasses RLS; never leaves the worker |
| GitHub Actions | `FOOTBALL_DATA_ORG_TOKEN` | Pipeline A fixtures/results (free) |
| GitHub Actions | `ODDS_API_KEY` | shared odds layer (free, 500 credits/month) |
| GitHub Actions | `STATSAPI_KEY` | Pipeline B (paid, stats-only plan). **Optional** — absent ⇒ engine runs A only |

Optional engine overrides (also env): `STATSAPI_BASE`, plus every key in
`engine/config.py:MODEL_PARAMS` (`DC_RHO`, `FH_GOAL_SHARE`, `CORNERS_A/B/C/K`,
`BLEND_W_MARKET`, `XG_ELO_W_RESULT`, `XG_ELO_W_XG`, `XG_ELO_K`).

## Workflows

### `refresh.yml` — the main engine run
- Cron: `0 */6 * * *` year-round **plus** `0 * * 6,7 *` (hourly, June–July).
  A `concurrency` group serialises overlapping fires.
- Manual: Actions tab → refresh → Run workflow (use this for the first
  populate and for debugging).
- Runs `python -m engine.main`; every stage is failure-fenced, so check the
  log for `stage 'X' FAILED` lines rather than relying on the job status.

### `live.yml` — matchday live stats
- Cron: `*/5 * * 6,7 *` (June–July). `cancel-in-progress: true`.
- Runs `python -m engine.live_statsapi`: exits in seconds when no fixture is
  live or kicking off within 10 minutes; otherwise polls TheStatsAPI per live
  fixture and upserts `match_stats` + score/status (Realtime pushes them to
  open pages).

## API budgets & disciplines (enforced in code)

| Source | Limit | Discipline |
|---|---|---|
| football-data.org | 10 req/min (free) | 2–3 requests/run, calls spaced ≥6.5s, cached in `api_cache` (TTL 600s) |
| The Odds API | 500 credits/month | one bulk call = 1 credit (single market × single region — never widen casually: 3 regions × 2 markets = 6×); reads `x-requests-remaining`, skips odds below a 60-credit reserve (predictions keep flowing, only `edge` staleness) |
| TheStatsAPI | 100k/month, 120/min | calls spaced ≥0.5s, cached (TTL 600s; 60s live, 24h finished-match stats); live poller self-exits when idle |

Storage (500 MB Supabase free cap): `odds_snapshots` pruned 14 days after a
fixture finishes; `match_stats.source_payload` nulled on the same schedule
(modeled columns kept); prediction rows are tiny.

## First-time setup (condensed)

1. Supabase: run `0001_init.sql`, `0002_dual_pipeline.sql`, then `seed.sql`.
2. Add the GitHub Actions secrets; set the two `NEXT_PUBLIC_*` vars in Vercel.
3. Trigger `refresh` manually once → fixtures, predictions and sims populate.
4. Trigger it again with a browser tab open → confirm Realtime updates land.

## Pipeline B onboarding (inside the 7-day TheStatsAPI trial)

```bash
# Phase 0 gate — writes docs/statsapi-verification.md, exit 1 on gate failure
STATSAPI_KEY=... python -m engine.verify_statsapi

# Calibration sprint — fits params, backtests WC 2022 -> docs/backtest-2022.md
STATSAPI_KEY=... python -m engine.calibrate
```

- If verification checks **3, 5 or 6** fail → stop; Pipeline B's scope must be
  renegotiated (and the trial refunded/cancelled informed by the report).
- Paste the fitted parameters from `calibrate` as env lines on the engine step
  in `refresh.yml`.
- Pipeline B (or its blend) may only become the site's *default* model after
  the backtest shows it beating A on 1X2 log-loss — until then users get
  `free` by default and can still switch manually.
- The vendor's exact payload field names are confirmed by the verification
  report; if they differ from the defensive guesses, adjust the `pick(...)`
  fallback chains in `engine/statsapi.py` / `engine/ingest_statsapi.py`.

## Runbook

| Symptom | Check / fix |
|---|---|
| Site shows no data | Did the refresh workflow ever run? Trigger manually; check Supabase tables have rows |
| Markets render twice | A query is missing its `pipeline` filter — see database.md query discipline |
| `edge` columns all NULL/stale | Odds credits exhausted or reserve hit — see `[ingest_odds]` log lines; resets monthly |
| Pipeline B rows absent | `STATSAPI_KEY` missing/invalid or vendor down — `[main] stage 'ingest_statsapi' FAILED` in the log; A is unaffected by design |
| `/admin/health` lists unmapped teams | Name drift — add an alias in `engine/aliases.py` (resolution never auto-creates teams) |
| Unmapped fixtures | Usually kickoff mismatch >3h between vendors or a TBD knockout slot; re-runs self-heal once both vendors publish |
| Scheduled runs stopped | GitHub disables cron after **60 days without repo activity** — push a trivial commit / re-enable in the Actions tab. Also: cron fires 15–60 min late at peak, by design |
| Supabase project paused | Free projects pause after 7 idle days; the 6-hourly worker prevents it while schedules are active |
| Elo looks wrong after a data fix | Results fold in exactly once via `elo_applied`/`elo_xg_applied`; to recompute from seed, reset those flags and restore seed Elos deliberately |

## v4.1 acceptance gates (re-check after each refresh during the trial)

1. `/admin/health` shows 104/104 fixtures for both vendors and an empty
   unmapped list (DR Congo and Haiti mapped); every fixture visible on `/fixtures`.
2. `npm test` green (market-label registry, incl. the `ou05_1h` ≠ 2.5 case).
3. No page offers more than two model choices; old `?model=` links resolve.
4. Picks generate on a real refresh, settle on finished fixtures, and the
   `/picks` track record computes from settled rows.
5. §5.0 odds probe result recorded in `ops_status.statsapi_odds`; if available,
   `odds_snapshots` gains `source='statsapi'` opening/closing rows.

## Known limitations (current, honest)

- **TheStatsAPI payload shapes unverified** until Phase 0 runs with a real key;
  ingest reads fields through tolerant `pick()` chains as its best guess.
- **Knockout bracket**: rounds not yet drawn use Elo reseed (best vs worst),
  not the exact FIFA R32 third-place allocation — the top remaining accuracy
  upgrade. Drawn rounds use real pairings automatically.
- **HT/FT** is an approximation (representative half-time scoreline per HT
  outcome); its nine cells sum to 1 within ~2%.
- **Pens in finished drawn knockouts** resolve by Elo-weighted coin — full-time
  goals don't identify the shootout winner.
- **Blend weight w=0.7 is static**; the weekly group-stage refit from
  `prediction_log` is a manual exercise for now (re-run with a different
  `BLEND_W_MARKET` env).
- **Market baseline** in `prediction_log` is recovered from the free pipeline's
  `probability − edge` at logging time, i.e. the last pre-finish odds pull —
  close to, but not formally, the closing line. (If the §5.0 odds probe finds
  TheStatsAPI odds available, formal opening/closing lines land in
  `odds_snapshots` with `source='statsapi'` and close this gap.)
- **First-half markets can't be settled** (`prediction_log.outcome` stays NULL
  for `ht_1x2`/`ou*_1h`/`htft`): full-time goals don't record the half-time
  score. They therefore never reach 30 scored matches and never become picks —
  intentional until HT scores are stored.
- **Lineup strength and signals are display/rationale only** — deliberately not
  model inputs in v4.1 (calibration question for later).
- **Live mode updates stats/scores only**; in-play model re-pricing is the
  documented stretch goal.
- **Vercel Hobby is non-commercial**; revisit hosting before any monetisation.
