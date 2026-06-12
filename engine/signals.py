"""Derived team + referee signals (spec v4.1 §5.2, §5.4) -> team_signals,
referee_signals. Recomputed each run over the tournament window. Pure display
+ rationale inputs — none of these feed the models in v4.1.

Signals:
  xg_overperf          goals − xG (rolling)         regression-risk flag
  big_chance_rate      shots with xG ≥ 0.3 / match  chance-quality profile
  big_chance_against   conceded big chances / match
  corner_pace_for      corners won / match
  corner_pace_against  corners conceded / match
  fh_share             share of xG generated in the first half (shots ≤ 45')
  set_piece_xg_share   share of xG from corner/set-piece situations
  form_vs_elo          last-5 points − Elo-expected points
"""
from collections import defaultdict
from datetime import datetime, timezone

from .db import chunked, sb

WINDOW = "wc2026"
BIG_CHANCE_XG = 0.3
SET_PIECE_SITUATIONS = {"corner", "set_piece", "set piece", "free_kick", "free kick", "throw_in"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def run():
    fixtures = (
        sb().table("fixtures")
        .select("id, home_id, away_id, home_goals, away_goals, host_home, kickoff")
        .eq("status", "finished")
        .order("kickoff")
        .execute().data
    )
    fixtures = [f for f in fixtures if f["home_id"] and f["home_goals"] is not None]
    if not fixtures:
        print("[signals] no finished fixtures yet")
        return

    stats = sb().table("match_stats").select("*").eq("period", "FT").execute().data
    shots = sb().table("shots").select("*").execute().data
    teams = sb().table("teams").select("id, elo").execute().data
    elo = {t["id"]: float(t["elo"]) for t in teams}

    stat_by = {(s["fixture_id"], s["team_id"]): s for s in stats}
    shots_by: dict[int, list] = defaultdict(list)
    for s in shots:
        shots_by[s["team_id"]].append(s)

    acc: dict[int, dict] = defaultdict(lambda: defaultdict(float))
    games: dict[int, int] = defaultdict(int)
    points_hist: dict[int, list] = defaultdict(list)  # [(points, elo_expected_points)]

    for f in fixtures:
        for team, opp, gf, ga, is_home in (
            (f["home_id"], f["away_id"], f["home_goals"], f["away_goals"], True),
            (f["away_id"], f["home_id"], f["away_goals"], f["home_goals"], False),
        ):
            games[team] += 1
            acc[team]["goals"] += gf
            st, opp_st = stat_by.get((f["id"], team)), stat_by.get((f["id"], opp))
            if st and st["xg"] is not None:
                acc[team]["xg"] += float(st["xg"])
                acc[team]["xg_games"] += 1
            if st and st["corners"] is not None:
                acc[team]["corners_for"] += st["corners"]
                acc[team]["corner_games"] += 1
            if opp_st and opp_st["corners"] is not None:
                acc[team]["corners_against"] += opp_st["corners"]
            # form vs Elo expectation (home adv only when host_home)
            adv = 100 if (f["host_home"] and is_home) else (-100 if f["host_home"] else 0)
            we = 1.0 / (10 ** (-(elo[team] - elo[opp] + adv) / 400.0) + 1.0)
            pts = 3 if gf > ga else 1 if gf == ga else 0
            points_hist[team].append((pts, 3 * we))

    now = _now()
    rows = []

    def add(team_id, signal, value):
        if value is not None:
            rows.append({
                "team_id": team_id, "signal": signal,
                "value": round(float(value), 3),
                "window": WINDOW, "computed_at": now,
            })

    opp_big: dict[int, float] = defaultdict(float)
    for f in fixtures:
        for team, opp in ((f["home_id"], f["away_id"]), (f["away_id"], f["home_id"])):
            big = sum(
                1 for s in shots_by[team]
                if s["fixture_id"] == f["id"] and s["xg"] is not None and float(s["xg"]) >= BIG_CHANCE_XG
            )
            opp_big[opp] += big

    for team, n in games.items():
        a = acc[team]
        if a["xg_games"]:
            add(team, "xg_overperf", a["goals"] - a["xg"])
        if a["corner_games"]:
            add(team, "corner_pace_for", a["corners_for"] / a["corner_games"])
            add(team, "corner_pace_against", a["corners_against"] / a["corner_games"])
        tshots = [s for s in shots_by[team] if s["xg"] is not None]
        if tshots:
            big = sum(1 for s in tshots if float(s["xg"]) >= BIG_CHANCE_XG)
            add(team, "big_chance_rate", big / n)
            add(team, "big_chance_against", opp_big[team] / n)
            total_xg = sum(float(s["xg"]) for s in tshots)
            if total_xg > 0:
                fh = sum(float(s["xg"]) for s in tshots if (s["minute"] or 99) <= 45)
                add(team, "fh_share", fh / total_xg)
                sp = sum(
                    float(s["xg"]) for s in tshots
                    if str(s["situation"] or "").lower() in SET_PIECE_SITUATIONS
                )
                add(team, "set_piece_xg_share", sp / total_xg)
        last5 = points_hist[team][-5:]
        if last5:
            add(team, "form_vs_elo", sum(p for p, _ in last5) - sum(e for _, e in last5))

    for batch in chunked(rows):
        sb().table("team_signals").upsert(batch, on_conflict="team_id,signal").execute()
    print(f"[signals] upserted {len(rows)} team signals across {len(games)} teams")

    # ---- referee signals (5.4) ----
    refs = (
        sb().table("fixtures").select("id, referee")
        .eq("status", "finished").not_.is_("referee", "null")
        .execute().data
    )
    by_ref: dict[str, list[int]] = defaultdict(list)
    for f in refs:
        by_ref[f["referee"]].append(f["id"])
    ref_rows = []
    for ref, fids in by_ref.items():
        cards, fouls, corners, n_c, n_f, n_co = 0, 0, 0, 0, 0, 0
        for fid in fids:
            for team_key in [k for k in stat_by if k[0] == fid]:
                st = stat_by[team_key]
                if st["yellows"] is not None:
                    cards += st["yellows"] + (st["reds"] or 0)
                    n_c += 1
                if st["fouls"] is not None:
                    fouls += st["fouls"]
                    n_f += 1
                if st["corners"] is not None:
                    corners += st["corners"]
                    n_co += 1
        ref_rows.append({
            "referee": ref,
            "matches": len(fids),
            "avg_cards": round(2 * cards / n_c, 2) if n_c else None,    # per match (2 team rows/match)
            "avg_fouls": round(2 * fouls / n_f, 2) if n_f else None,
            "avg_corners": round(2 * corners / n_co, 2) if n_co else None,
            "computed_at": now,
        })
    for batch in chunked(ref_rows):
        sb().table("referee_signals").upsert(batch, on_conflict="referee").execute()
    if ref_rows:
        print(f"[signals] upserted {len(ref_rows)} referee signal rows")


if __name__ == "__main__":
    run()
