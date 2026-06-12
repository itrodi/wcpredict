# Frontend (Next.js App Router on Vercel)

Stack: Next.js 15 + TypeScript + Tailwind CSS + Recharts +
`@supabase/supabase-js`. Server Components read with the publishable key
(`lib/supabase/server.ts`, returns `null` without env so CI builds render empty
states); a browser singleton (`lib/supabase/client.ts`) is used only for
Realtime subscriptions.

## Pipeline selection (`lib/pipeline.ts`)

The global model choice is persisted in the URL as `?model=` with values
`free | statsapi | blend_free | blend_statsapi`, rendered by
`components/ModelSwitcher.tsx` on every prediction page.

- **Default**: `blend_statsapi` once any rows exist for it, else `free`
  (resolved per request by `resolvePipeline`).
- **`basePipeline()`**: blends only exist for 1X2 match rows, so tournament
  sims and exotic markets resolve `blend_* → free|statsapi`.
- Every `match_predictions` / `tournament_odds` query filters `pipeline` —
  omitting the filter double-renders markets (the v4 trap).

## Pages

| Route | Rendering | Content |
|---|---|---|
| `/` | dynamic (searchParams) | next 12 fixtures with 1X2 probability cards (selected pipeline) + last 6 results; model switcher |
| `/matches/[id]` | dynamic | full market breakdown for the selected pipeline (blend selections merge the base model's exotic markets underneath so the page never empties), match stats panel, lineups panel (pre-match), line-movement chart, link to compare view |
| `/matches/[id]/compare` | dynamic | **the flagship v4 page**: per market/selection — Free model \| Stats model \| Market (de-vigged, 1X2) \| A−B difference, rows highlighted where the models disagree by >5 points |
| `/tournament` | dynamic | Monte Carlo advancement table + top-10 title-odds bar chart for the base pipeline; Realtime |
| `/teams/[slug]` | dynamic | team header (group, confederation, Elo), advancement probability bars, full fixture path with 1X2 cards |
| `/models` | ISR 300s | the public scoreboard from `model_scores`: Brier / log-loss / n per pipeline per market, Δ log-loss vs the market baseline (negative = beats the closing line) |
| `/value` | dynamic | upcoming selections sorted by \|edge\| for the selected pipeline, experimental badges, **responsible-gambling warning lives on this page** |
| `/admin/health` | no cache | identity-mapping audit: teams/fixtures missing a vendor id, last scoreboard write time |

Pages reading `searchParams` are server-rendered on demand; `/models` uses ISR
with `revalidate = 300`.

## Components

| Component | Type | Notes |
|---|---|---|
| `ModelSwitcher` | client | pill toggle, rewrites `?model=` preserving the current path |
| `MatchMarkets` | client | all markets grouped + ordered, probability bars, fair/book/edge line on 1X2, experimental badges, Realtime |
| `OddsTable` | client | tournament odds table, Realtime, pipeline-labelled freshness badge |
| `ChampionChart` | client | Recharts bar chart of top-10 title odds |
| `StatsPanel` | client | xG/shots/corners/possession/cards two-column table from `match_stats`; subscribes to Realtime **only while the fixture is live** |
| `LineupsPanel` | server | formation + numbered XI per side, `confirmed`/`probable` badge |
| `LineMovementChart` | client | Recharts step lines of median 1X2 decimal odds per fetch batch from `odds_snapshots` |
| `FixtureCard` | server | fixture row with stage/kickoff/score and 1X2 mini-grid |
| `ProbabilityBar` | server | labelled probability bar; highlighted when edge > 2pts |
| `UpdatedBadge` | client | "Stats model · updated 14m ago" — pipeline label + self-ticking relative time from `computed_at` |

## Realtime conventions

- Subscribe with `event: "*"` — the worker's upserts emit INSERTs the first
  time any row is written; an UPDATE-only subscription silently misses them.
- Postgres-changes filters support a single column, so channels filter on
  `fixture_id` server-side and **drop other-pipeline payloads client-side**.
- Channels are removed on unmount and re-created when the pipeline changes
  (free tier: 200 concurrent connections).
- `StatsPanel` doesn't subscribe at all unless the match is live.

## Honesty rules baked into the UI

- Every prediction surface carries an `UpdatedBadge` with its pipeline name.
- Corners/uncalibrated markets show an "experimental — uncalibrated" badge
  until `model_scores` shows ≥30 scored matches for that market.
- The scoreboard is public; `/value` leads with the no-guaranteed-profit
  warning and a BeGambleAware link, which also sits in the global footer and
  under every market list.
