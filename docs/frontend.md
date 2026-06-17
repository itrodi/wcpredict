# Frontend (Next.js App Router on Vercel)

Stack: Next.js 15 + TypeScript + Tailwind CSS + Recharts +
`@supabase/supabase-js`. Server Components read with the publishable key
(`lib/supabase/server.ts`, returns `null` without env so CI builds render empty
states); a browser singleton (`lib/supabase/client.ts`) is used only for
Realtime subscriptions.

## Two views (`lib/pipeline.ts`, v4.1 Phase 3)

The UI offers exactly two choices, persisted as `?view=site|baseline`
(`ModelSwitcher` is a two-pill toggle; legacy `?model=` deep links still
resolve):

- **Site Picks (default)** → `blend_statsapi` owns 1X2, `statsapi` owns every
  other market and the sims. Badge: "Site model".
- **Baseline (free data)** → `blend_free` / `free`. Badge: "Baseline model".
- **Fallback**: until Pipeline B has rows (or when it's down), Site Picks
  silently resolves to the free family and the badge says "Baseline model" —
  never an empty page.

`resolveView()` returns `{view, label, blendPipeline, modelPipeline}`;
`mergeViewRows()` merges the two pipelines' rows with the blend owning 1X2
(model row as fallback when no odds exist). Every prediction read filters by
those pipelines — omitting the filter double-renders markets.

## Market labels (`lib/markets.ts`, v4.1 Phase 2)

The single source of truth for market labels, grouping, ordering, per-market
selection labels (lines parsed from the key: `ou05_1h` → 0.5) and experimental
thresholds. **No component derives labels inline.** Guarded by unit tests:
`npm test` (the `ou05_1h ≠ 2.5` regression test lives there).

## Pages

| Route | Rendering | Content |
|---|---|---|
| `/` | dynamic (searchParams) | next-12-fixture digest with 1X2 cards + last 6 results; "All fixtures →" link; view switcher |
| `/fixtures` | dynamic | **all 104 matches** — pills for All / By group / By matchday plus a team dropdown; view-aware 1X2 cards |
| `/groups/[code]` | dynamic | live group standings (pts/GD/GF), the group's matches, advance probability per team |
| `/picks` | ISR 300s + Realtime | Bankers + Value pick cards (label, probability bar, odds, edge, rationale bullets, countdown), the always-visible settled track record (W–L, hit rate, flat-stakes P/L), responsible-gambling banner |
| `/matches/[id]` | dynamic | **Insights card above markets** (machine-built bullets from team/referee signals, lineup strength, line movement), full market breakdown for the view, stats panel, lineups (with XI rating + key absences), line-movement chart, referee chip, compare link |
| `/matches/[id]/compare` | dynamic | per market/selection — Site model \| Baseline \| Market (de-vigged) \| difference, rows highlighted where the models disagree by >5 points |
| `/tournament` | dynamic | Monte Carlo advancement table + top-10 title-odds bar chart for the base pipeline; Realtime |
| `/teams/[slug]` | dynamic | team header (group, confederation, Elo), advancement probability bars, full fixture path with 1X2 cards |
| `/models` | ISR 300s | the public scoreboard from `model_scores`: Brier / log-loss / n per pipeline per market, Δ log-loss vs the market baseline (negative = beats the closing line) |
| `/strategies` | dynamic (searchParams) | per-matchday staking board (`lib/strategies.ts`) with a **Safe \| Value** mode toggle (`?strategy=`): singles, cross-match accumulators (double/treble/4-fold), and **correlation-aware same-game combos**. Safe ranks by P(lands); Value ranks by EV at the book price (positive edge only). Same-game pairs price their dependence with a Gaussian copula (`lib/stats.ts` + `lib/correlation.ts`) so the joint probability beats the naive product; each bet carries a flat-1u baseline and a fractional-Kelly stake. **Responsible-gambling warning lives on this page** |
| `/value` | dynamic | upcoming selections sorted by \|edge\| for the selected pipeline, experimental badges, **responsible-gambling warning lives on this page** |
| `/admin/health` | no cache | reads `ops_status`: per-vendor fixture counts vs 104, copy-paste-ready unmapped vendor names, odds credits, last refresh, the §5.0 odds-probe verdict, plus the live xmap audit |

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
