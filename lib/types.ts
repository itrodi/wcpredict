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
  fixture_id: number;
  market: "1x2" | "ou25" | "btts" | "cs";
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
