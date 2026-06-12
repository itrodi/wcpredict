export type Team = {
  id: number;
  slug: string;
  name: string;
  group_code: string | null;
  elo: number;
  confederation: string | null;
};

export type Fixture = {
  id: number;
  ext_id: string | null;
  stage: string;
  group_code: string | null;
  home_id: number | null;
  away_id: number | null;
  kickoff: string;
  venue: string | null;
  host_home: boolean;
  status: "scheduled" | "live" | "finished";
  home_goals: number | null;
  away_goals: number | null;
  home?: Pick<Team, "name" | "slug"> | null;
  away?: Pick<Team, "name" | "slug"> | null;
};

export type MatchPrediction = {
  id: number;
  pipeline: string;
  fixture_id: number;
  market: string; // 1x2 | ou25 | btts | cs | ht_1x2 | ou05_1h | ou15_1h | htft | corners_* | team_corners_*
  selection: string;
  probability: number;
  fair_odds: number | null;
  market_odds: number | null;
  edge: number | null;
  model_version: string | null;
  computed_at: string;
};

export type TournamentOdds = {
  id: number;
  pipeline: string;
  team_id: number;
  advance_grp: number | null;
  reach_qf: number | null;
  reach_sf: number | null;
  reach_final: number | null;
  champion: number | null;
  n_sims: number | null;
  model_version: string | null;
  computed_at: string;
  teams?: Pick<Team, "name" | "slug" | "elo" | "group_code"> | null;
};

export type MatchStat = {
  id: number;
  fixture_id: number;
  team_id: number;
  is_home: boolean | null;
  period: string;
  shots: number | null;
  shots_on_target: number | null;
  corners: number | null;
  possession: number | null;
  xg: number | null;
  npxg: number | null;
  fouls: number | null;
  yellows: number | null;
  reds: number | null;
  fetched_at: string;
};

export type Lineup = {
  id: number;
  fixture_id: number;
  team_id: number;
  formation: string | null;
  starters: { player_id?: string; name: string; position?: string; shirt?: number }[];
  bench: unknown;
  confirmed: boolean;
  fetched_at: string;
};

export type ModelScore = {
  id: number;
  pipeline: string;
  market: string;
  n: number;
  brier: number | null;
  log_loss: number | null;
  computed_at: string;
};

export type OddsSnapshot = {
  id: number;
  fixture_id: number;
  bookmaker: string;
  market: string;
  selection: string;
  decimal_odds: number;
  fetched_at: string;
};
