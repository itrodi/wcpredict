# Engine (`engine/`, Python on GitHub Actions)

Run with `python -m engine.main`. Requirements: `numpy`, `requests`,
`supabase` (see `engine/requirements.txt`). Every tunable model parameter lives
in `engine/config.py` — the v4 knobs in `MODEL_PARAMS` are overridable by env
vars of the same name, so calibration results deploy as workflow env lines
without code changes.

## Module map

| Module | Pipeline | Role |
|---|---|---|
| `config.py` | shared | env keys, model constants, `MODEL_PARAMS` |
| `db.py` | shared | service-role Supabase client + chunked upserts |
| `cache.py` | shared | `api_cache`-backed HTTP cache (get/set with TTL) |
| `aliases.py` | shared | `slugify()` + cross-vendor name alias dict |
| `ingest_fd.py` | A | football-data.org `WC` matches → teams, fixtures, `xmap.fd_id` |
| `ingest_odds.py` | shared | The Odds API bulk h2h → `odds_snapshots`; credit-aware; snapshot pruning |
| `statsapi.py` | B | TheStatsAPI HTTP client: bearer auth, 0.5s spacing, defensive `pick()` field access |
| `ingest_statsapi.py` | B | identity resolution → `xmap`, match_stats (xG/corners), lineups, payload pruning |
| `ratings.py` | A | results-Elo on `teams.elo` (`fixtures.elo_applied`) |
| `ratings_xg.py` | B | xG-adjusted Elo on `teams.elo_xg` (`fixtures.elo_xg_applied`) |
| `match_model.py` | A | Elo→Poisson grid → 1x2/ou25/btts/cs + de-vig edge |
| `match_model_b.py` | B | Dixon-Coles + first-half + corners markets |
| `blend.py` | shared | `blend_free` / `blend_statsapi` 1X2 rows |
| `simulate.py` | A+B | vectorised Monte Carlo, `run(pipeline=...)` |
| `compare.py` | shared | `prediction_log` → Brier/log-loss → `model_scores` |
| `write_db.py` | shared | pipeline-stamped upserts; finished-fixture outcome logging |
| `main.py` | shared | orchestration with per-stage failure fencing |
| `live_statsapi.py` | B | 5-minute live stats poller (self-exits when idle) |
| `verify_statsapi.py` | B | Phase 0 trial-week gate (incl. 2b odds re-test) → `docs/statsapi-verification.md` |
| `calibrate.py` | B | history pulls (paginated), parameter fits incl. per-half shares, WC2022 backtest |
| `ops.py` | shared | `ops_status` writer — operational truth for `/admin/health` |
| `ingest_statsapi_extra.py` | B | §5.0 odds probe/ingest, shotmaps, player stats + lineup strength |
| `signals.py` | B | derived team + referee signals (display/rationale only, never model inputs) |
| `picks.py` | shared | rule-generated, immutable, publicly settled picks (after compare) |

## v4.1 data-correctness disciplines

- **Pagination**: every TheStatsAPI list call goes through `statsapi.get_all()`
  (`per_page=100`, loops `meta.total_pages`) — a plain `get()` silently
  truncates the 104-match season at the default page size.
- **Coverage assertions**: ingest writes per-vendor fixture counts vs the
  official 104 into `ops_status`; `main.py` ends each run with a Pipeline A
  coverage check. Unmapped vendor team names are recorded verbatim so adding an
  alias is copy-paste from `/admin/health`.
- **Picks rules** (`picks.py`): calibrated markets only (`model_scores.n ≥ 30`;
  1x2/ou25/btts exempt), Banker = p ≥ 0.65 ∧ edge ≥ −0.01, Value = edge ≥ 0.04
  ∧ p ≥ 0.25 ranked by Kelly fraction, ≤2/fixture, ≤10/tier. Material changes
  retire-and-republish; `write_db.settle_picks` settles outcomes (corners
  settle from `match_stats`).

## Pipeline A model (`elo-poisson-v1`)

**Elo** (World Football Elo conventions): K=50, goal-diff multiplier
(1 / 1.5 / (11+gd)/8), +100 home advantage only when `host_home`. Updated from
finished results once each (`elo_applied`).

**Elo → goals (v2)**: `λ_home = 1.3·e^(+β·dr)`, `λ_away = 1.3·e^(−β·dr)`
(β = `ELO_GOAL_BETA`, default 0.002), clipped to [0.2, 4.0]. Even matches keep
the 2.6 expected total; strength gaps raise it (a 600-point mismatch expects
~4.4 goals). The v1 mapping split a *fixed* total by win expectancy, which made
every totals market (O/U 2.5, 1H totals, the corners mean) constant across
fixtures — `python -m engine.tests` guards against that regressing.

**Markets** from an 11×11 independent-Poisson scoreline matrix (renormalised):
1X2, O/U 2.5, BTTS, correct score (0–4 each way + `other`).

**Edge**: the latest fetch batch of `odds_snapshots` per fixture → median
decimal odds per selection across bookmakers → implied probs normalised
(de-vig) → `edge = p_model − p_market`. Stored on 1X2 rows only.

## Pipeline B model (`xgelo-dc-v1`)

**xG-Elo** (`ratings_xg.py`): the Elo "result" is blended,
`score_eff = 0.6·actual + 0.4·xg_result`, where `xg_result` treats the two
teams' match xG as Poisson rates and computes `P(win) + ½P(draw)`. Falls back
to the pure result when xG is missing. Same K/home-advantage machinery,
fully separate bookkeeping from A.

**Dixon-Coles**: same Elo→λ mapping (with `elo_xg`), then the DC tau correction
on the {0,1}×{0,1} cells with `rho = −0.1` (default until fitted), renormalised.

**First-half markets**: `λ_1H = 0.45·λ_FT` per team → HT 1X2, 1H O/U 0.5 and
1.5. HT/FT is a v1 approximation: the second half is an independent DC matrix
at the remaining goal share, convolved over one representative half-time
scoreline per HT outcome (1-0 / 0-0 / 0-1) — its nine probabilities sum to ~1
within ~2%.

**Corners (negative binomial, NB2)**: total mean
`μ = 7.0 + 1.1·(λ_h+λ_a) − 0.002·|elo_diff|`, dispersion `k = 9`
(var = μ + μ²/k). Markets: totals over/under 8.5 / 9.5 / 10.5; team corners
over/under 4.5 with μ split by λ share. **Experimental-badged** until
`model_scores.n ≥ 30` for the market.

## Blend (`blend-w0.7`)

For 1X2 rows of either model that carry an `edge` (i.e. the market price
exists): `p_market = probability − edge`, then
`p = 0.7·p_market + 0.3·p_model`. The blend's own `edge` is recomputed against
the same `p_market`. Refit of `w` is manual for now (see operations.md).

## Market catalogue

| market | selections | pipelines |
|---|---|---|
| `1x2` | home, draw, away | free, statsapi, blend_* |
| `ou25` | over, under | free, statsapi |
| `btts` | yes, no | free, statsapi |
| `cs` | `0-0`…`4-4`, other | free, statsapi |
| `ht_1x2` | home, draw, away | statsapi |
| `ou05_1h`, `ou15_1h` | over, under | statsapi |
| `htft` | `home_home` … `away_away` (9) | statsapi |
| `corners_o85` / `o95` / `o105` | over, under | statsapi |
| `team_corners_home_o45`, `team_corners_away_o45` | over, under | statsapi |

≈59 rows per fixture for Pipeline B, ≈37 for Pipeline A.

## Tournament simulation (`simulate.py`)

Fully vectorised numpy, 20,000 runs per pipeline per refresh.

- **Group stage**: finished matches fixed, the rest sampled
  `Poisson(λ)` from the pipeline's rating. Ranking key: points → goal diff →
  goals for → random jitter. Top 2 from each of the 12 groups + the 8 best
  thirds (ranked on the same key) → 32 qualifiers.
- **Knockouts**: if football-data.org has published a round's full pairings
  (all matches with both teams known), those exact pairings are used and
  finished results fixed; otherwise survivors are **reseeded by Elo, best vs
  worst** — the documented v1 simplification (the exact FIFA R32 third-place
  allocation mapping is the known upgrade). Knockout winners are drawn from the
  no-draw Elo expectancy; a real match finished level (pens) also falls back to
  that weighted coin since full-time goals don't identify the winner.
- Outputs per team: `advance_grp`, `reach_qf`, `reach_sf`, `reach_final`,
  `champion` (occurrence counts / n_sims).

## Scoring (`compare.py` + `write_db.log_finished_predictions`)

When a fixture turns `finished`, every pipeline's stored pre-match rows are
copied to `prediction_log` with boolean outcomes (one-vs-rest per selection),
plus the de-vigged market price as `pipeline='market'` 1X2 rows. `compare.py`
then recomputes per (pipeline, market): `brier = mean((p−y)²)` and
`log_loss = −mean(y·ln p + (1−y)·ln(1−p))` over all logged selections, with
`n` = distinct fixtures, and upserts `model_scores`. The group stage alone
yields ~72 scored matches before the knockouts.

## One-off scripts

- **`verify_statsapi.py`** — the ten Phase 0 trial checks (auth, odds excluded,
  WC season present, DR Congo/Haiti, ~104 fixtures, corners/xG in stats,
  HT splits, lineups, 2018/2022 history, rate-limit burst). Gate: checks 3, 5,
  6 must pass before building further on Pipeline B. Exit code 1 on gate fail.
- **`calibrate.py`** — pulls 2018+2022 WC history, fits DC rho (grid MLE),
  first-half goal share, corners NB (moment matching), backtests plain-Poisson
  vs Dixon-Coles on WC 2022 and writes the acceptance verdict (B must beat A on
  1X2 log-loss to become the site default). Prints fitted params as env
  overrides.
