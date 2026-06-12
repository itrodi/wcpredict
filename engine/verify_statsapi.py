"""Phase 0 — TheStatsAPI trial-week verification (spec v4 §3).

Run manually DURING THE 7-DAY TRIAL, before building on Pipeline B:

    STATSAPI_KEY=... python -m engine.verify_statsapi

Prints PASS/FAIL per assumption and writes docs/statsapi-verification.md.
GATE: if checks 3, 5 or 6 FAIL, stop — Pipeline B's scope must be renegotiated.
Needs no Supabase access; it only talks to TheStatsAPI.
"""
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import config
from .statsapi import as_list, pick

RESULTS: list[tuple[str, bool | None, str]] = []  # (check, passed, detail)


def record(name: str, passed: bool | None, detail: str = ""):
    status = "PASS" if passed else ("FAIL" if passed is False else "INFO")
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    RESULTS.append((name, passed, detail))


def raw_get(path: str, params=None):
    return requests.get(
        f"{config.STATSAPI_BASE}{path}",
        params=params,
        headers={"Authorization": f"Bearer {config.STATSAPI_KEY}"},
        timeout=30,
    )


def main():
    if not config.STATSAPI_KEY:
        print("STATSAPI_KEY not set — export it and re-run.")
        sys.exit(2)

    # 1. Auth + plan
    r = raw_get("/competitions")
    record("1 auth: GET /competitions returns 200", r.status_code == 200, f"status={r.status_code}")
    comps = as_list(r.json() if r.ok else [], "competitions")

    # 2. Odds excluded (expected on stats-only plan)
    ro = raw_get("/odds")
    odds_blocked = ro.status_code in (401, 403, 404) or not as_list(ro.json() if ro.ok else [], "odds")
    record(
        "2 odds endpoints excluded on our key (expected)",
        odds_blocked,
        f"status={ro.status_code}; engine must NEVER call odds endpoints in production",
    )

    # 2b. Re-test match-level odds (spec v4.1 §5.0): the pricing page claims odds
    # on every plan; Phase 0 recorded them excluded. Needs a real match id.
    # (Filled in after matches are pulled below — see the deferred block.)

    # 3. World Cup present with a current 2026 season  [GATE]
    wc = next(
        (c for c in comps if "world cup" in str(pick(c, "name", "title", default="")).lower()
         and "club" not in str(pick(c, "name", "title", default="")).lower()),
        None,
    )
    comp_id = str(pick(wc, "id", "competition_id")) if wc else None
    season = None
    if comp_id:
        seasons = as_list(raw_get(f"/competitions/{comp_id}/seasons").json(), "seasons")
        season = next((s for s in seasons if pick(s, "is_current", "current") is True), None) or next(
            (s for s in seasons if "2026" in str(pick(s, "year", "name", "label", default=""))), None
        )
    season_id = str(pick(season, "id", "season_id")) if season else None
    record("3 GATE world cup + current 2026 season", bool(season_id),
           f"competition_id={comp_id} season_id={season_id}")
    if season_id:
        for sub in ("groups", "standings"):
            rs = raw_get(f"/competitions/{comp_id}/seasons/{season_id}/{sub}")
            record(f"3b {sub} endpoint", rs.status_code == 200, f"status={rs.status_code}")

    # 4. Teams complete (DR Congo + Haiti regression check)
    team_names: set[str] = set()
    matches = []
    if season_id:
        rm = raw_get(f"/competitions/{comp_id}/seasons/{season_id}/matches")
        matches = as_list(rm.json() if rm.ok else [], "matches")
        for m in matches:
            for k in ("home_team", "homeTeam", "away_team", "awayTeam"):
                if isinstance(m.get(k), dict) and pick(m[k], "name"):
                    team_names.add(str(pick(m[k], "name")).lower())
    has_drc = any("congo" in n for n in team_names)
    has_haiti = any("haiti" in n for n in team_names)
    record("4 teams include DR Congo and Haiti", has_drc and has_haiti,
           f"teams seen={len(team_names)} drc={has_drc} haiti={has_haiti}")

    # 5. Fixtures ≈ 104  [GATE]
    record("5 GATE fixture count ≈ 104", 90 <= len(matches) <= 110, f"count={len(matches)}")
    sample = matches[:3]
    for m in sample:
        record("5b spot-check kickoff vs football-data.org (manual)", None,
               f"match={pick(m,'id','match_id')} kickoff={pick(m,'kickoff','utc_date','date','start_time')} "
               f"{pick(m.get('home_team') or m.get('homeTeam') or {}, 'name')} vs "
               f"{pick(m.get('away_team') or m.get('awayTeam') or {}, 'name')}")

    # 2b (deferred): match-level odds with a real match id
    if matches:
        mid = str(pick(matches[0], "id", "match_id"))
        r2b = raw_get(f"/matches/{mid}/odds")
        body = as_list(r2b.json() if r2b.ok else [], "odds", "bookmakers")
        record(
            "2b match odds endpoint accessible on our key",
            r2b.status_code == 200 and bool(body),
            f"status={r2b.status_code}, bookmakers={len(body)} — if PASS, the engine ingests "
            f"opening/closing 1X2 + corners prices (source='statsapi'); if FAIL, ops_status "
            f"records the exclusion and everything else stands",
        )

    # 6. Match stats include corners/shots/xG for internationals  [GATE — make-or-break for corners product]
    finished = [m for m in matches
                if str(pick(m, "status", "state", default="")).lower() in
                {"finished", "ft", "full_time", "ended"}][:3]
    if not finished:
        record("6 GATE corners/shots/xg in intl match stats", None,
               "no finished WC matches yet — test against recent finished internationals "
               "(March 2026 friendlies/qualifiers) by editing this script's competition")
    stats_ok = bool(finished)
    detail6 = []
    for m in finished:
        mid = str(pick(m, "id", "match_id"))
        rs = raw_get(f"/matches/{mid}/stats")
        sides = as_list(rs.json() if rs.ok else [], "stats", "statistics")
        got = {
            "corners": any(pick(s, "corners", "corner_kicks") is not None for s in sides),
            "shots": any(pick(s, "shots", "shots_total") is not None for s in sides),
            "xg": any(pick(s, "xg", "expected_goals") is not None for s in sides),
        }
        detail6.append(f"match {mid}: {got}")
        stats_ok = stats_ok and all(got.values())
    if finished:
        record("6 GATE corners/shots/xg non-null in match stats", stats_ok, "; ".join(detail6))

    # 7. First-half splits present?
    if finished:
        rs = raw_get(f"/matches/{pick(finished[0],'id','match_id')}/stats")
        sides = as_list(rs.json() if rs.ok else [], "stats", "statistics")
        has_ht = any(
            isinstance(s.get(k), dict) for s in sides for k in ("first_half", "1h", "ht")
        )
        record("7 first-half period splits in stats", has_ht,
               "HT markets calibratable" if has_ht else "HT markets will be modeled only (0.45 share)")

    # 8. Lineups
    if matches:
        mid = str(pick(matches[0], "id", "match_id"))
        rl = raw_get(f"/matches/{mid}/lineups")
        sides = as_list(rl.json() if rl.ok else [], "lineups")
        eleven = any(len(pick(s, "starters", "starting_xi", "startXI", default=[]) or []) == 11 for s in sides)
        record("8 lineups: formation + 11 starters", rl.status_code == 200 and eleven,
               f"status={rl.status_code}")

    # 9. History depth: 2018 + 2022 World Cups
    if comp_id:
        hist_seasons = as_list(raw_get(f"/competitions/{comp_id}/seasons").json(), "seasons")
        for year in ("2018", "2022"):
            s = next((x for x in hist_seasons if year in str(pick(x, "year", "name", "label", default=""))), None)
            ok = False
            if s:
                rm = raw_get(f"/competitions/{comp_id}/seasons/{pick(s,'id','season_id')}/matches")
                ok = rm.status_code == 200 and len(as_list(rm.json(), "matches")) > 0
            record(f"9 history: WC {year} matches retrievable", ok, "calibration fuel for §7")

    # 10. Rate limit behavior: ~20-call burst
    t0 = time.monotonic()
    statuses = []
    headers_seen = {}
    for _ in range(20):
        rr = raw_get("/competitions")
        statuses.append(rr.status_code)
        headers_seen = {k: v for k, v in rr.headers.items() if "limit" in k.lower() or "remaining" in k.lower()}
    burst_s = time.monotonic() - t0
    record("10 rate limit: 20-call burst tolerated", all(s == 200 for s in statuses),
           f"{burst_s:.1f}s, statuses={set(statuses)}, headers={headers_seen}")

    # ---- write report ----
    gates = [r for r in RESULTS if "GATE" in r[0]]
    gates_pass = all(r[1] for r in gates if r[1] is not None)
    lines = [
        "# TheStatsAPI verification report (Phase 0)",
        f"\nGenerated: {datetime.now(timezone.utc).isoformat()}  ·  Base: `{config.STATSAPI_BASE}`\n",
        f"**Gate verdict (checks 3, 5, 6): {'PASS — build Pipeline B' if gates_pass else 'FAIL — STOP, renegotiate scope'}**\n",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
    for name, passed, detail in RESULTS:
        status = "✅ PASS" if passed else ("❌ FAIL" if passed is False else "ℹ️")
        lines.append(f"| {name} | {status} | {detail.replace('|', '/')} |")
    out = Path(__file__).resolve().parent.parent / "docs" / "statsapi-verification.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(f"\nReport written to {out}")
    sys.exit(0 if gates_pass else 1)


if __name__ == "__main__":
    main()
