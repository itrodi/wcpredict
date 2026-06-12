"""Engine configuration — env vars + model constants.

v4: ALL tunable model parameters live in MODEL_PARAMS (spec v4 §8), each
overridable by an env var of the same name — no magic numbers inside model files.
"""
import os


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
FOOTBALL_DATA_ORG_TOKEN = os.environ.get("FOOTBALL_DATA_ORG_TOKEN", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
STATSAPI_KEY = os.environ.get("STATSAPI_KEY", "")

# Pipelines (spec v4 §0)
PIPELINE_FREE = "free"
PIPELINE_STATSAPI = "statsapi"
PIPELINE_BLEND_FREE = "blend_free"
PIPELINE_BLEND_STATSAPI = "blend_statsapi"
PIPELINE_MARKET = "market"

MODEL_VERSION = "elo-poisson-v2"          # Pipeline A (v2: matchup-dependent goal totals)
MODEL_VERSION_B = "xgelo-dc-v2"           # Pipeline B

# Elo (World Football Elo conventions; K=50 ≈ continental championship weight)
ELO_K = 50
ELO_HOME_ADV = 100          # applied only when host_home (USA/CAN/MEX playing at home)
DEFAULT_ELO = 1600          # teams created by ingest that the seed didn't know

# Elo -> Poisson
TOTAL_GOALS = 2.6           # expected total goals in an EVENLY MATCHED WC game
LAMBDA_MIN, LAMBDA_MAX = 0.2, 4.0
GOAL_GRID = 11              # scoreline matrix is 0..10 goals per side

# Monte Carlo
N_SIMS = 20000

# ---- v4 model parameters (spec §5, §8) — fit by engine/calibrate.py, override via env ----
MODEL_PARAMS = {
    # ratings_xg.py: score_eff = w_result*actual + w_xg*xg_result
    "XG_ELO_W_RESULT": _f("XG_ELO_W_RESULT", 0.6),
    "XG_ELO_W_XG": _f("XG_ELO_W_XG", 0.4),
    "XG_ELO_K": _f("XG_ELO_K", 50),
    # match_model: lambda_home = (TOTAL_GOALS/2) * exp(+beta*dr), away exp(-beta*dr).
    # v1 split a FIXED total by win expectancy, which made every totals market
    # (O/U 2.5, 1H totals, corners mean) constant across fixtures — the bug fix
    # is that strength gaps now raise the expected total (mismatch -> more goals).
    # Fit by calibrate.fit_elo_goal_beta (Poisson-implied expectancy must track
    # the Elo curve) — set the printed override; the default underrates favourites.
    "ELO_GOAL_BETA": _f("ELO_GOAL_BETA", 0.002),
    # match_model_b.py: Dixon-Coles low-score correction
    "DC_RHO": _f("DC_RHO", -0.1),
    # first-half goal share: lambda_1H = share * lambda_FT
    "FH_GOAL_SHARE": _f("FH_GOAL_SHARE", 0.45),
    # corners negative binomial: mu = a + b*(lam_h+lam_a) + c*|elo_diff|, dispersion k
    "CORNERS_A": _f("CORNERS_A", 7.0),
    "CORNERS_B": _f("CORNERS_B", 1.1),
    "CORNERS_C": _f("CORNERS_C", -0.002),
    "CORNERS_K": _f("CORNERS_K", 9.0),
    # blend.py: p = w*p_market + (1-w)*p_model
    "BLEND_W_MARKET": _f("BLEND_W_MARKET", 0.7),
    # first-half corners share (v4.1 §5.6): 0 = market disabled; set from
    # calibrate.py output only if the calibration is satisfactory
    "CORNERS_1H_SHARE": _f("CORNERS_1H_SHARE", 0.0),
}

# markets considered experimental until model_scores shows >= this many scored matches
EXPERIMENTAL_MIN_N = 30

# The Odds API (shared market layer for BOTH pipelines — stats plan has no odds)
ODDS_SPORT_KEY = "soccer_fifa_world_cup"
ODDS_REGION = "eu"          # ONE region x ONE market = 1 credit per call (v3 spec §9b)
ODDS_CREDIT_RESERVE = 60    # skip odds refresh when fewer credits remain
ODDS_SNAPSHOT_RETENTION_DAYS = 14

# football-data.org (Pipeline A)
FD_BASE = "https://api.football-data.org/v4"
FD_COMPETITION = "WC"
FD_MIN_CALL_SPACING_S = 6.5   # free tier: 10 req/min
FD_CACHE_TTL_S = 600

# TheStatsAPI (Pipeline B — stats-only plan: odds endpoints are NOT on our key)
STATSAPI_BASE = os.environ.get("STATSAPI_BASE", "https://api.thestatsapi.com/api/football")
STATSAPI_MIN_CALL_SPACING_S = 0.5   # 120/min cap -> stay <= 2 req/s sustained (spec §8)
STATSAPI_CACHE_TTL_S = 600
STATSAPI_WC_NAME = "FIFA World Cup"
STATSAPI_RETENTION_DAYS = 14        # drop raw source_payload for finished fixtures older than this
