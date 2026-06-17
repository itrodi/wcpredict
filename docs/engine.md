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
| `ingest_odds.py` | shared | The Odds API bulk h2h (+optional totals) → `odds_snapshots`; kickoff-aware credit budget; snapshot pruning |
| `statsapi.py` | B | TheStatsAPI HTTP client: bearer auth, 0.5s spacing, `get_all` pagination, WC competition→`current_season_id` resolution |
| `ingest_statsapi.py` | B | identity resolution → `xmap`, match_stats (npxG/corners/shots), payload pruning |
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
| `ingest_statsapi_extra.py` | B | §5.0 odds probe/ingest, shotmaps, per-match player stats (`player_match_stats`), player season stats + lineup strength |
| `signals.py` | B | derived team + referee signals (display/rationale only, never model inputs) |
| `picks.py` | shared | rule-generated, immutable, publicly settled picks (after compare) |

## TheStatsAPI integration (v4.5 — verified against the official reference)

- **Base** `https://api.thestatsapi.com/api/football`, bearer auth. Resources are
  flat, filtered collections — list/JSON are wrapped `{ "data": …, "meta": … }`.
- **WC resolution**: `GET /competitions?search=FIFA World Cup` → the men's senior
  competition → `GET /competitions/{id}` → `current_season_id` (there is **no**
  `/seasons` list endpoint).
- **Matches**: `GET /matches?competition_id={c}&season_id={s}&per_page=100`
  (NOT a nested `/competitions/{c}/seasons/{s}/matches` path — that 404s).
- **Match stats** (`/matches/{id}/stats`) are nested by category with home/away
  inside each stat and an `all`/`first_half`/`second_half` split, e.g.
  `data.attack.corners.all.home`, `data.shots.total.all.home`,
  `data.np_expected_goals.all.home`. `_stat_rows` flattens this into per-team,
  per-period `match_stats` rows. Only **non-penalty xG** is provided, so it
  doubles as the `xg` signal the models consume.
- **No lineups endpoint exists** on this API (only post-match `player-stats`), so
  confirmed-XI / key-absence ingestion is not attempted — the picks engine's
  lineup suppression stays dormant.
- **Odds** (`/matches/{id}/odds`, gated by `odds_available` within 6 days of
  kick-off): `data.bookmakers[].markets.{match_odds, total_goals.over_2_5,
  match_corners.over_9_5, btts}.<sel>.{opening,last_seen}` — parsed into
  `odds_snapshots` with `source='statsapi'` (gives corners a book price → corners
  value/CLV when the plan includes odds).

## v4.1 data-correctness disciplines

- **Pagination**: every TheStatsAPI list call goes through `statsapi.get_all()`
  (`per_page=100`, loops `meta.total_pages`) — a plain `get()` silently
  truncates the 104-match season at the default page size.
- **Coverage assertions**: ingest writes per-vendor fixture counts vs the
  official 104 into `ops_status`; `main.py` ends each run with a Pipeline A
  coverage check. Unmapped vendor team names are recorded verbatim so adding an
  alias is copy-paste from `/admin/health`.
- **Picks rules** (`picks.py`) — results **and** overs (v4.4):
  - **Banker** = high model probability. For 1x2 it comes from the blend
    (p ≥ 0.65, edge ≥ −0.01). For the **overs** markets (ou25, btts, 1H totals,
    corners, team corners) a banker is the over/yes side clearing a per-market
    probability floor (≈0.60–0.66); these are model-only when no book price
    exists, priced at fair odds (1/p) with a 1.25 fair-odds floor so trivially
    short lines are skipped. Corners are banker-eligible **even while
    experimental** (badged in the UI) — we surface the model's confidence and
    settle it publicly, but never claim *value* on an unproven/unpriced market.
  - **Value** = a calibrated market with a book edge that is positive-EV at the
    *quoted* price (edge ≥ 0.04 ∧ p ≥ 0.25 ∧ `p·odds > 1`), ranked by the true
    Kelly fraction `(p·odds − 1)/(odds − 1)`. ou25 gets a book line when
    `ODDS_MARKETS` includes `totals`; corners only if the StatsAPI odds probe
    succeeds.
  - Caps: ≤2/fixture and ≤`MAX_PER_CATEGORY` per tier **per category**
    (`result` vs `overs`) so overs always get airtime instead of being crowded
    out by short-priced favourites. Corner banker floors sit lower (≈0.54–0.58)
    than goals/result because NB-overdispersed corner probabilities compress
    toward 0.5. Material changes retire-and-republish; `write_db.settle_picks`
    settles **every** published pick incl. retired ones (corners settle from
    `match_stats`, 1H markets from the stored HT score) and stamps CLV from
    `closing_odds`.
- **Goal rush** (frontend, `lib/goals.ts` + `/picks`): upcoming fixtures ranked
  by how likely they are to be high-scoring, exposing the full over ladder (1.5
  → 5.5) with a headline "top goals call" (the highest line still ≥ 50%). The
  goals ladder is pick-eligible too — `ou35` can banker on a clear goal-fest
  (floor 0.55); `ou45`/`ou55` are shown but essentially never banker.
- **Match verdicts** (frontend, `lib/verdicts.ts` + `/picks`): a browse view,
  separate from the tracked Bankers/Value picks — for every upcoming fixture the
  model's single strongest call in result / goals / corners plus the dominant
  pick overall, each with a confidence %. Surfaces a corners read on *every*
  game even when none clears the banker floor. Pure model projections, not
  published/settled bets.
- **Matchday strategies** (frontend, `lib/strategies.ts` + `/strategies`):
  groups upcoming fixtures by match day and builds singles, cross-match
  accumulators and same-game combos in a Safe (P-of-landing) or Value (EV at the
  book price) mode, with flat + fractional-Kelly staking. Cross-match legs are
  treated as independent (one leg per fixture); same-game pairs price their
  dependence with a Gaussian copula (`lib/stats.ts`). Browse projections, not
  published/settled bets.
- **Exact goal joints** (`engine/match_model.py` `score_grid_rows` ->
  `score_grids`; frontend `lib/scoregrid.ts`): the engine stores each scheduled
  fixture's full-time scoreline grid (free Elo→Poisson model, 0–7 goals a side).
  The `/strategies` pricer computes the **exact** same-game joint for pure goal
  pairs — full-time result × goals over/under × BTTS — straight off that grid
  (no copula), which is what lets the result market combine with goals/BTTS at
  all (it has no clean copula axis). Pairs that touch corners or first-half goals
  aren't on the grid and fall back to the fitted-correlation copula below.
- **Player drivers** (`ingest_statsapi_extra.ingest_player_match_stats` ->
  `player_match_stats`; frontend `lib/playerDrivers.ts`): per-appearance player
  stats are ingested for finished mapped fixtures, and the team page aggregates
  them into "who drives each metric" — tying individual players to the markets
  we price (goals/BTTS via goals + shots on target, chance creation via key
  passes, the corners market via shots + dribbles + fouls won, card risk via
  fouls + bookings). Grows through the tournament; display/context only, not a
  model input. Surfaced on the team page (squad-wide drivers) and the match page
  (`components/KeyPlayers.tsx` — each side's standout per metric beside the
  prediction). Player data is deliberately NOT a model input: there is no
  pre-match lineups endpoint, so who actually starts is unknown before kickoff.
- **Same-game correlations** (`engine/correlations.py` -> `market_correlations`):
  the copula ρ for each goal-driven market-category pair (ftgoals/btts/corners/
  hfgoals — result and correct-score are excluded) is the **tetrachoric
  correlation fitted from finished tournament matches**, reusing `settle_outcome`
  for the realised events. Empirical-Bayes shrunk toward a conservative prior by
  the count of jointly-settleable matches (`CORR_PRIOR_K`), so a handful of early
  games can't yank ρ to an extreme; the frontend (`lib/correlation.ts`) reads the
  table and falls back to the matching static prior for any unwritten pair. Runs
  each refresh after `signals` (goal pairs need only Pipeline A results; corner
  pairs additionally need `match_stats`).
- **Leak plugs** (v4.3): the picks engine suppresses a fixture entirely when a
  confirmed lineup shows ≥2 key absences for any side (rotation — the market
  reprices on the team sheet, an Elo model does not), or `fixture_incentives`
  marks it a dead rubber / mutual-draw situation (matchday-3 incentive traps the
  ratings are blind to). Existing picks on a suppressed fixture are retired with
  the reason.
- **CLV** (`write_db.record_closing_odds`): every refresh re-records the
  de-vigged median per still-`scheduled` fixture into `closing_odds`; the final
  write is the closing price and survives snapshot pruning, so each pick gets a
  closing-line-value stamp — the leading indicator of edge at WC sample sizes.
- **Closing odds means closing**: odds ingest and both match models only touch
  `status='scheduled'` fixtures (and the odds ingest skips events whose
  commence time has passed), so the last stored prediction row — the one
  `prediction_log` copies — is genuinely pre-kickoff, never an in-play price.
- **90-minute settlement**: football-data's `fullTime` includes extra time, so
  `fixtures.duration` is stored and `settle_outcome` settles every market on
  90' conventions — a knockout match that went to ET settles 1x2 as a draw,
  while goal-count and corners markets are unsettleable (no 90' record).
  Pens winners persist in `fixtures.winner_id` for the simulation.
- **Pagination everywhere**: any table that can outgrow PostgREST's 1000-row
  cap (`prediction_log`, `shots`, `odds_snapshots`, pick candidates) is read
  via `db.fetch_all` or latest-pull-bounded batches; `compare.py` additionally
  dedupes log rows defensively so the scoreboard can never double-count.

## Pipeline A model (`elo-poisson-v1`)

**Elo** (World Football Elo conventions): K=50, goal-diff multiplier
(1 / 1.5 / (11+gd)/8), +100 home advantage only when `host_home`. Updated from
finished results once each (`elo_applied`).

**Elo → goals (v2)**: `λ_home = 1.3·e^(+β·dr)`, `λ_away = 1.3·e^(−β·dr)`
(β = `ELO_GOAL_BETA`, default 0.002 — fit it with
`calibrate.fit_elo_goal_beta`, which picks β so the Poisson-implied match
expectancy tracks the Elo curve; the fitted value is ≈0.0022 and the default
underrates favourites by ~3pts at moderate gaps), clipped to [0.2, 4.0]. Even
matches keep
the 2.6 expected total; strength gaps raise it (a 600-point mismatch expects
~4.4 goals). The v1 mapping split a *fixed* total by win expectancy, which made
every totals market (O/U 2.5, 1H totals, the corners mean) constant across
fixtures — `python -m engine.tests` guards against that regressing.

**Markets** from an 11×11 independent-Poisson scoreline matrix (renormalised):
1X2, the full goal over/under ladder (1.5 / 2.5 / 3.5 / 4.5 / 5.5, where
over X.5 = P(total ≥ X+1)), BTTS, correct score (0–4 each way + `other`).

**Edge**: the latest fetch batch of `odds_snapshots` per fixture → median
decimal odds per selection across bookmakers → **power de-vig** (`q_i = p_i^k`
with `k` solved so `Σq = 1`; proportional normalisation over-taxes favourites
and manufactures phantom value on draws/longshots — the favourite-longshot
bias) → `edge = p_model − p_market`. Stored on 1X2 always, and on `ou25` when
`ODDS_MARKETS` includes `totals` (a paid-plan opt-in — `h2h,totals` doubles the
credit spend per pull).

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

**Corners (negative binomial, NB2)** — team-level attack/defense (v4.4, refined
v4.5): each side's mean is `μ_team = W·(own corner-pace-for) + (1−W)·(opponent's
corner-pace-against)`, where the rates come from `team_signals` (built from
`match_stats` corners) and are **empirical-Bayes shrunk** toward a per-team
**shot-informed prior** by a `CORNER_SHRINK_K0`-match pseudo-count. The prior is
`0.5·league_mean + 0.5·(team avg shots × league corners-per-shot)` — shots
accumulate far faster than a stable corner rate, so a team that is shooting a
lot is expected to win more corners before its own corner sample is reliable
(this is what makes corners useful in week one instead of everything shrinking
to a flat average). The pair is then scaled by match tempo `(λ_h+λ_a)/2.6`
(clamped) and a small strength tilt. `μ_total = μ_home + μ_away`; dispersion `k`
is fitted from the empirical variance of finished-match total corners (falls
back to 9). With no shots the prior is the flat league mean; with no finished
matches the league mean is the formula's implied average. Markets: totals
over/under 8.5 / 9.5 / 10.5; team corners over/under 4.5. **As of v4.5 corners
are a first-class market — no longer experimental-badged** (the model is
data-driven); they are banker-eligible and, when a book price exists,
value-eligible without the calibration wait.

## Blend (`blend-w0.7`)

For 1X2 rows of either model that carry an `edge` (i.e. the market price
exists): `p_market = probability − edge`, then
`p = 0.7·p_market + 0.3·p_model`. The blend's own `edge` is recomputed against
the same `p_market`. Refit of `w` is manual for now (see operations.md).

## Market catalogue

| market | selections | pipelines |
|---|---|---|
| `1x2` | home, draw, away | free, statsapi, blend_* |
| `ou15` / `ou25` / `ou35` / `ou45` / `ou55` | over, under | free, statsapi |
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

- **Group stage**: finished matches fixed, the rest sampled `Poisson(λ)` from
  the pipeline's rating — Pipeline B samples the joint Dixon-Coles scoreline so
  the sim matches its match model. Ranking key: points → goal diff → goals for
  → random jitter (FIFA's later head-to-head/fair-play tiebreakers are
  approximated by the jitter). Top 2 from each of the 12 groups + the 8 best
  thirds (ranked on the same key) → 32 qualifiers.
- **Knockouts follow the official FIFA bracket** (matches 73–104): fixed
  winner/runner-up slots per R32 match, the eight third-place slots with their
  allowed-group sets, and the published match-number progression to the final.
  The eight qualified thirds are placed by a constraint-respecting matching
  (memoised over the 495 scenarios; `engine/tests.py` verifies all of them).
  Once football-data publishes the real R32 — FIFA's own thirds placement —
  the exact pairings are forced. Knockout advancement is decomposed as
  `P(win 90') + P(draw 90')·p_ET` where `p_ET = 0.5 + (W_e − 0.5)·KO_ET_SHRINK`
  — raw `W_e` credits half a draw as half a win, overrating favourites who
  actually face near-coin pens after 90' level, compounding over five rounds.
  Real decided results override wherever the simulated pairing matches a real
  fixture, and pens winners come from `fixtures.winner_id` rather than a coin.
- Outputs per team: `advance_grp`, `reach_r16`, `reach_qf`, `reach_sf`,
  `reach_final`, `champion` (occurrence counts / n_sims). The free pipeline also
  writes `fixture_incentives`: `P(advance | win/draw/loss)` per side for each
  unplayed group fixture, conditioned on the same runs (feeds the picks engine's
  dead-rubber / mutual-draw suppression).

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
- **`calibrate.py`** — fits `ELO_GOAL_BETA` (Poisson-implied expectancy vs the
  Elo curve — no history needed), then pulls 2018+2022 WC history and fits DC
  rho (grid MLE), first-half goal share, corners NB (moment matching).
  Backtests plain-Poisson vs Dixon-Coles on WC 2022 and writes the acceptance
  verdict (B must beat A on 1X2 log-loss to become the site default); the
  gate's rho is fitted on pre-2022 history only so the verdict is
  out-of-sample. Prints fitted params as env overrides.
