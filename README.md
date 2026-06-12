# WC Predict — 2026 World Cup prediction app

Match predictions + Monte Carlo tournament simulation for the 2026 FIFA World Cup.
**Next.js on Vercel + Supabase + Python engine on GitHub Actions.**

v4 runs **two pipelines side by side** over the same tournament so their predictions can be
compared with data instead of vibes:

- **Pipeline A — `free`** (the v3 stack, untouched, forever the fallback): football-data.org
  fixtures/results + The Odds API 1X2 odds → results-Elo → Poisson markets.
- **Pipeline B — `statsapi`** (paid, stats-only plan): TheStatsAPI match stats (xG, corners,
  shots, lineups) → xG-adjusted Elo → Dixon-Coles Poisson + first-half markets + a
  negative-binomial corners model. Odds still come from The Odds API (the stats plan has no
  odds endpoints).
- **Blends** (`blend_free`, `blend_statsapi`): `p = 0.7·p_market + 0.3·p_model` on 1X2.
- **`market`**: de-vigged closing odds, logged as the baseline both models must beat.

Every prediction row carries a `pipeline` discriminator; the UI has a global model switcher
(`?model=`), a per-match compare view (`/matches/[id]/compare`), a public scoreboard
(`/models`, Brier/log-loss per pipeline per market), a value finder (`/value`) and an
identity-mapping health page (`/admin/health`). If TheStatsAPI breaks, the engine degrades
to Pipeline A automatically.

**Full documentation lives in [`docs/`](docs/README.md)** — architecture, database, engine
math, frontend, and operations/runbook.

## Architecture (two planes)

- **Compute plane** (`engine/`, Python, GitHub Actions cron): ingests fixtures/results from
  football-data.org (`WC`) and 1X2 odds from The Odds API, refreshes Elo from finished results,
  builds Poisson scoreline markets, runs a ≥20,000-iteration Monte Carlo of the remaining
  tournament, and upserts everything into Supabase with the service-role key.
  **Heavy work never runs inside a Vercel or Supabase Edge Function.**
- **Serving plane** (`app/`, Next.js App Router on Vercel): Server Components read with the
  publishable key (RLS public-read), ISR `revalidate = 300`, and browsers subscribe to Supabase
  Realtime so open pages update refresh-free the moment the worker writes.

## Setup

1. **Supabase** — create a free project, then in the SQL editor run, in order:
   - `supabase/migrations/0001_init.sql` (schema, RLS, Realtime publication)
   - `supabase/migrations/0002_dual_pipeline.sql` (v4: pipeline column, xmap tables,
     match_stats/lineups/model_scores — additive only)
   - `supabase/seed.sql` (teams + initial Elo from eloratings.net; groups are filled by the
     first ingest run, and any missing team — e.g. playoff winners — is created automatically)
2. **API keys** — register at [football-data.org](https://www.football-data.org/client/register)
   (free token, includes `WC`) and [the-odds-api.com](https://the-odds-api.com) (free 500
   credits/month). For Pipeline B: a TheStatsAPI key (paid, stats-only plan).
3. **GitHub Actions secrets** (repo → Settings → Secrets and variables → Actions):
   `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `FOOTBALL_DATA_ORG_TOKEN`, `ODDS_API_KEY`,
   and `STATSAPI_KEY` (optional — without it the engine runs Pipeline A only).
4. **Vercel** — import the repo, set `NEXT_PUBLIC_SUPABASE_URL` and
   `NEXT_PUBLIC_SUPABASE_ANON_KEY` (publishable key).
5. **First populate** — trigger the `refresh` workflow manually (Actions tab → refresh →
   Run workflow). A second run with a browser tab open should show Realtime updates land.

### Pipeline B: run these INSIDE the 7-day TheStatsAPI trial

```bash
# Phase 0 gate — writes docs/statsapi-verification.md; checks 3,5,6 must PASS
STATSAPI_KEY=... python -m engine.verify_statsapi

# Calibration sprint — fits DC rho / first-half share / corners NB from 2018+2022
# history, backtests on WC 2022, writes docs/backtest-2022.md. Pipeline B may only
# become the site default if it beats A on 1X2 log-loss here.
STATSAPI_KEY=... python -m engine.calibrate
```

Paste the fitted parameters from `engine.calibrate` as env overrides in
`.github/workflows/refresh.yml` (all model knobs in `engine/config.py:MODEL_PARAMS` are
env-overridable). The exact TheStatsAPI payload field names are confirmed by the verify
script — if its report shows different keys, adjust the `pick(...)` calls in
`engine/statsapi.py` / `engine/ingest_statsapi.py`.

A second workflow, `live.yml`, polls live match stats every 5 minutes during June–July and
self-exits when nothing is in play; stats tick into open match pages via Realtime.

### Run the engine locally

```bash
pip install -r engine/requirements.txt
export SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \
       FOOTBALL_DATA_ORG_TOKEN=... ODDS_API_KEY=...
python -m engine.main
```

### Run the app locally

```bash
npm install
cp .env.example .env.local   # fill in the two NEXT_PUBLIC_* values
npm run dev
```

## Model (v1, `elo-poisson-v1`)

- **Elo**: seeded from eloratings.net, refreshed from finished results only (K=50,
  goal-difference multiplier, +100 home advantage for USA/Canada/Mexico at home). Each result
  is applied once, tracked by `fixtures.elo_applied`.
- **Match markets**: the Elo win expectancy splits a fixed 2.6 expected-total-goals into two
  Poisson rates; an 11×11 scoreline matrix yields 1X2, O/U 2.5, BTTS and correct score.
  Latest book h2h prices are de-vigged to give `edge` on the 1X2 market.
- **Tournament sim**: vectorised numpy Monte Carlo. Finished results are fixed; group ranking
  uses points → goal difference → goals for → random. Top 2 + 8 best thirds advance (48-team
  format). Knockout rounds use the **real bracket pairings once football-data.org publishes
  them**; undrawn rounds fall back to a simplified Elo reseed (best vs worst). Swapping that
  for the exact FIFA R32 third-place mapping is the Phase 7 upgrade.
- **Calibration**: when a fixture finishes, its final pre-match probabilities are copied into
  `prediction_log` with realised outcomes, ready for reliability/Brier analysis.

## Free-tier disciplines baked in

- football-data.org: 2–3 requests/run, ≥6.5 s apart, cached in the `api_cache` table.
- The Odds API: one bulk call = 1 credit (single market × single region); the worker reads
  `x-requests-remaining` and skips odds (predictions keep flowing) below a 60-credit reserve.
- `odds_snapshots` are pruned 14 days after a fixture finishes to respect the 500 MB DB cap.
- Realtime subscriptions unsubscribe on unmount (200-connection free cap).
- GitHub Actions cron is best-effort and **scheduled workflows are disabled after 60 days of
  repo inactivity** — push a trivial commit or re-enable in the Actions tab before kickoff.

## Responsible use

Probabilities are model estimates, not guarantees. The UI carries no-guaranteed-profit
messaging and links to responsible-gambling resources. All sources are used within their
published free terms; re-verify before any commercial use (Vercel Hobby is non-commercial).
