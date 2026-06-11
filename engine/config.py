"""Engine configuration — env vars + model constants."""
import os

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
FOOTBALL_DATA_ORG_TOKEN = os.environ.get("FOOTBALL_DATA_ORG_TOKEN", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

MODEL_VERSION = "elo-poisson-v1"

# Elo (World Football Elo conventions; K=50 ≈ continental championship weight)
ELO_K = 50
ELO_HOME_ADV = 100          # applied only when host_home (USA/CAN/MEX playing at home)
DEFAULT_ELO = 1600          # teams created by ingest that the seed didn't know

# Elo -> Poisson
TOTAL_GOALS = 2.6           # expected total goals in an evenly matched WC game
LAMBDA_MIN, LAMBDA_MAX = 0.25, 3.5
GOAL_GRID = 11              # scoreline matrix is 0..10 goals per side

# Monte Carlo
N_SIMS = 20000

# The Odds API
ODDS_SPORT_KEY = "soccer_fifa_world_cup"
ODDS_REGION = "eu"          # ONE region x ONE market = 1 credit per call (spec §9b)
ODDS_CREDIT_RESERVE = 60    # skip odds refresh when fewer credits remain
ODDS_SNAPSHOT_RETENTION_DAYS = 14

# football-data.org
FD_BASE = "https://api.football-data.org/v4"
FD_COMPETITION = "WC"
FD_MIN_CALL_SPACING_S = 6.5   # free tier: 10 req/min
FD_CACHE_TTL_S = 600
