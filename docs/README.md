# WC Predict — Documentation

Living documentation for the 2026 World Cup prediction site as currently built
(v4 dual-pipeline). Each page documents what is actually in the repo, not the
aspirational spec.

| Doc | Covers |
|---|---|
| [architecture.md](architecture.md) | The two planes, the two pipelines, data flow, failure isolation |
| [database.md](database.md) | Every table, column conventions, RLS, Realtime, identity mapping |
| [engine.md](engine.md) | Every engine module, the model math, markets, simulation, scoring |
| [frontend.md](frontend.md) | Every page and component, pipeline switching, Realtime behaviour |
| [operations.md](operations.md) | Setup, secrets, workflows, budgets, runbooks, known limitations |

Reports generated at runtime (not committed until you run the scripts):

- `statsapi-verification.md` — written by `python -m engine.verify_statsapi` (Phase 0 trial gate)
- `backtest-2022.md` — written by `python -m engine.calibrate` (calibration sprint)

## The site in one paragraph

Two prediction pipelines run side by side over the same World Cup. **Pipeline A
(`free`)** is the all-free-tier stack: football-data.org fixtures/results feed a
results-Elo which drives a Poisson scoreline model. **Pipeline B (`statsapi`)**
adds TheStatsAPI's paid match stats (xG, corners, shots, lineups) to drive an
xG-adjusted Elo, a Dixon-Coles correction, first-half markets and a corners
model. Both share The Odds API as the single odds source and are blended with
the de-vigged market price (`blend_free`, `blend_statsapi`). Every finished
fixture scores all pipelines (plus the market itself as a baseline) on Brier and
log-loss in a public scoreboard, so "which model is better" is answered with
data. A Python engine on GitHub Actions computes everything and upserts into
Supabase; a Next.js app on Vercel reads it and Supabase Realtime pushes changes
to open pages without refresh. If the paid vendor dies, Pipeline A keeps the
site alive untouched.

## Version history

- **v3** — single free pipeline (Elo→Poisson), tournament sim, Realtime serving.
- **v4 (current)** — adds Pipeline B, identity mapping, blends, model scoreboard,
  match stats/lineups panels, line movement, value finder, live polling.
