"""Monte Carlo tournament simulation (>=20,000 runs, vectorised with numpy).

Group stage: finished results are fixed, remaining matches sampled from the
Elo->Poisson model (Pipeline B samples from its Dixon-Coles matrix so the sim
is consistent with its match model). Top 2 per group + 8 best third-placed
teams advance (48-team format).

Knockout rounds follow the OFFICIAL FIFA 2026 bracket (matches 73-104): fixed
group-position slots, the eight third-place slots with their allowed-group
constraints, and the published match-number progression to the final. The
eight qualified thirds are placed by a constraint-respecting matching over the
allowed-group sets (FIFA's regulations pin one assignment per 495-combination
scenario; any constraint-respecting assignment is the same to second order and
the engine switches to the real published pairings as soon as football-data
knows the teams). Matches decided after 90' use the stored result; pens-decided
matches use fixtures.winner_id instead of an Elo coin flip.
"""
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np

from . import config
from .db import sb
from .match_model import elo_lambdas
from .match_model_b import dc_scoreline_matrix

# ---- Official 2026 bracket (FIFA match schedule, matches 73-104) ----
# R32 slots: '1A' = winner of group A, '2A' = runner-up, 'T' = third-place slot
# (allowed source groups in R32_THIRD_GROUPS).
R32_SLOTS = {
    73: ("2A", "2B"), 74: ("1E", "T"), 75: ("1F", "2C"), 76: ("1C", "2F"),
    77: ("1I", "T"), 78: ("2E", "2I"), 79: ("1A", "T"), 80: ("1L", "T"),
    81: ("1D", "T"), 82: ("1G", "T"), 83: ("2K", "2L"), 84: ("1H", "2J"),
    85: ("1B", "T"), 86: ("1J", "2H"), 87: ("1K", "T"), 88: ("2D", "2G"),
}
R32_THIRD_GROUPS = {
    74: "ABCDF", 77: "CDFGH", 79: "CEFHI", 80: "EHIJK",
    81: "BEFIJ", 82: "AEHIJ", 85: "EFGIJ", 87: "DEIJL",
}
R16_MAP = {89: (74, 77), 90: (73, 75), 91: (76, 78), 92: (79, 80),
           93: (83, 84), 94: (81, 82), 95: (86, 88), 96: (85, 87)}
QF_MAP = {97: (89, 90), 98: (93, 94), 99: (91, 92), 100: (95, 96)}
SF_MAP = {101: (97, 98), 102: (99, 100)}
FINAL = (101, 102)

ROUND_OF = {**{m: "R32" for m in R32_SLOTS}, **{m: "R16" for m in R16_MAP},
            **{m: "QF" for m in QF_MAP}, **{m: "SF" for m in SF_MAP}, 104: "F"}


@lru_cache(maxsize=None)
def third_assignment(combo: tuple) -> dict:
    """Assign 8 qualified third-place groups (tuple of letters) to the 8 third
    slots, respecting each slot's allowed-group set. Backtracking with
    most-constrained-slot-first ordering; FIFA's regulations guarantee a
    perfect matching exists for every one of the 495 combinations."""
    remaining = set(combo)
    slots = sorted(R32_THIRD_GROUPS, key=lambda m: len(set(R32_THIRD_GROUPS[m]) & remaining))
    assign: dict = {}

    def backtrack(i: int) -> bool:
        if i == len(slots):
            return True
        m = slots[i]
        for g in sorted(set(R32_THIRD_GROUPS[m]) & remaining):
            assign[m] = g
            remaining.discard(g)
            if backtrack(i + 1):
                return True
            remaining.add(g)
            del assign[m]
        return False

    if not backtrack(0):
        # should be unreachable; degrade to unconstrained assignment
        assign = dict(zip(sorted(R32_THIRD_GROUPS), sorted(combo)))
    return dict(assign)


def _load():
    teams = sb().table("teams").select("id, elo, elo_xg, group_code").execute().data
    fixtures = (
        sb()
        .table("fixtures")
        .select("id, stage, group_code, home_id, away_id, status, home_goals, away_goals, "
                "host_home, winner_id")
        .neq("stage", "3P")
        .execute()
        .data
    )
    return teams, fixtures


def conditional_advancement(adv: np.ndarray, hg: np.ndarray, ag: np.ndarray, min_n: int = 200):
    """(P(adv|win), P(adv|draw), P(adv|loss)) from the first team's perspective,
    None for results that occurred in fewer than min_n sims."""
    out = []
    for mask in (hg > ag, hg == ag, hg < ag):
        n = int(mask.sum())
        out.append(round(float(adv[mask].mean()), 4) if n >= min_n else None)
    return tuple(out)


def run(n_sims: int = config.N_SIMS, pipeline: str = config.PIPELINE_FREE):
    """Runs once per pipeline (v4 §1): 'free' uses results-Elo, 'statsapi' uses
    xG-Elo (falling back per-team to elo where elo_xg is not yet set)."""
    teams, fixtures = _load()
    rng = np.random.default_rng()

    use_xg = pipeline == config.PIPELINE_STATSAPI
    model_version = config.MODEL_VERSION_B if use_xg else config.MODEL_VERSION
    dc_rho = config.MODEL_PARAMS["DC_RHO"] if use_xg else 0.0

    idx = {t["id"]: i for i, t in enumerate(teams)}
    ids = np.array([t["id"] for t in teams])
    E = np.array(
        [
            float(t["elo_xg"] if use_xg and t.get("elo_xg") is not None else t["elo"])
            for t in teams
        ]
    )
    nt = len(teams)

    groups: dict[str, list[int]] = {}
    for t in teams:
        if t["group_code"]:
            groups.setdefault(t["group_code"], []).append(idx[t["id"]])
    if len(groups) != 12 or any(len(m) != 4 for m in groups.values()):
        print(f"[simulate] groups incomplete ({len(groups)} groups) — skipping simulation until ingest fills them")
        return None
    group_letters = sorted(groups)
    gidx = {g: i for i, g in enumerate(group_letters)}

    group_fx: dict[str, list] = {g: [] for g in groups}
    ko_fx: dict[str, list] = {s: [] for s in ("R32", "R16", "QF", "SF", "F")}
    for f in fixtures:
        if f["home_id"] is None or f["away_id"] is None:
            continue
        h, a = idx.get(f["home_id"]), idx.get(f["away_id"])
        if h is None or a is None:
            continue
        if f["stage"] == "group" and f["group_code"] in group_fx:
            group_fx[f["group_code"]].append(
                (h, a, f["status"] == "finished", f["home_goals"], f["away_goals"],
                 bool(f["host_home"]), f["id"])
            )
        elif f["stage"] in ko_fx:
            # 90'-decided winner from goals; pens/ET winner from winner_id
            w = idx.get(f["winner_id"]) if f.get("winner_id") else None
            if w is None and f["status"] == "finished" and f["home_goals"] is not None \
                    and f["home_goals"] != f["away_goals"]:
                w = h if f["home_goals"] > f["away_goals"] else a
            ko_fx[f["stage"]].append(
                {"h": h, "a": a, "winner": w,
                 "finished": f["status"] == "finished", "host_home": bool(f["host_home"])}
            )

    def match_goals(h, a, finished, hg, ag, host_home):
        if finished and hg is not None:
            return np.full(n_sims, hg), np.full(n_sims, ag)
        lam_h, lam_a = elo_lambdas(E[h], E[a], host_home)
        if use_xg:
            # sample the joint DC scoreline so the sim matches Pipeline B's model
            m = dc_scoreline_matrix(lam_h, lam_a, dc_rho)
            flat = rng.choice(m.size, size=n_sims, p=m.ravel())
            return flat // m.shape[1], flat % m.shape[1]
        return rng.poisson(lam_h, n_sims), rng.poisson(lam_a, n_sims)

    # ---- group stage ----
    winners, runners, thirds = [], [], []
    third_keys = []
    sampled_group = []  # (fixture_id, h, a, hg, ag) for unplayed real fixtures — feeds incentives
    for g in group_letters:
        T = np.array(groups[g])
        local = {t: i for i, t in enumerate(T)}
        pts = np.zeros((n_sims, 4))
        gf = np.zeros((n_sims, 4))
        ga = np.zeros((n_sims, 4))
        matches = group_fx[g] or [
            (int(T[i]), int(T[j]), False, None, None, False, None)
            for i in range(4) for j in range(i + 1, 4)
        ]
        for h, a, finished, hg_v, ag_v, host_home, fid in matches:
            i, j = local[h], local[a]
            hg, ag = match_goals(h, a, finished, hg_v, ag_v, host_home)
            if not finished and fid is not None:
                sampled_group.append((fid, h, a, hg, ag))
            pts[:, i] += 3 * (hg > ag) + (hg == ag)
            pts[:, j] += 3 * (ag > hg) + (hg == ag)
            gf[:, i] += hg
            ga[:, i] += ag
            gf[:, j] += ag
            ga[:, j] += hg
        # rank: points, goal diff, goals for, random jitter as the final tiebreak
        # (FIFA's later tiebreakers — head-to-head, fair play — approximated by the jitter)
        key = pts * 10000 + (gf - ga) * 100 + gf + rng.random((n_sims, 4))
        order = np.argsort(-key, axis=1)
        winners.append(T[order[:, 0]])
        runners.append(T[order[:, 1]])
        thirds.append(T[order[:, 2]])
        third_keys.append(np.take_along_axis(key, order[:, 2:3], axis=1)[:, 0])

    winners = np.stack(winners, axis=1)   # (n_sims, 12), columns in group order A..L
    runners = np.stack(runners, axis=1)
    thirds = np.stack(thirds, axis=1)
    third_keys = np.stack(third_keys, axis=1)

    # best 8 thirds across the 12 groups (points, GD, GF — same composite key)
    third_order = np.argsort(-third_keys, axis=1)[:, :8]
    qual = np.zeros((n_sims, 12), dtype=bool)
    np.put_along_axis(qual, third_order, True, axis=1)
    thirds_q = np.take_along_axis(thirds, third_order, axis=1)  # (n_sims, 8)
    qualifiers = np.concatenate([winners, runners, thirds_q], axis=1)  # (n_sims, 32)

    # ---- group-stage incentives (free pipeline only): P(advance | result) ----
    # Conditioning the SAME sims on the sampled result costs nothing extra and
    # exposes dead rubbers / mutual-draw fixtures, which the ratings are blind
    # to — the picks engine steps aside from those.
    if pipeline == config.PIPELINE_FREE and sampled_group:
        adv_mask = np.zeros((n_sims, nt), dtype=bool)
        adv_mask[np.arange(n_sims)[:, None], qualifiers] = True
        inc_rows = []
        for fid, h, a, hg, ag in sampled_group:
            hw, hd, hl = conditional_advancement(adv_mask[:, h], hg, ag)
            aw, ad, al = conditional_advancement(adv_mask[:, a], ag, hg)
            h_vals = [v for v in (hw, hd, hl) if v is not None]
            a_vals = [v for v in (aw, ad, al) if v is not None]
            mutual_draw = bool(
                hd is not None and ad is not None and h_vals and a_vals
                and hd >= max(h_vals) - 0.02 and ad >= max(a_vals) - 0.02
            )
            inc_rows.append({
                "fixture_id": fid,
                "home_adv_win": hw, "home_adv_draw": hd, "home_adv_loss": hl,
                "away_adv_win": aw, "away_adv_draw": ad, "away_adv_loss": al,
                "mutual_draw": mutual_draw,
                "n_sims": n_sims,
                "computed_at": datetime.now(timezone.utc).isoformat(),
            })
        sb().table("fixture_incentives").upsert(inc_rows, on_conflict="fixture_id").execute()
        print(f"[simulate] incentives recorded for {len(inc_rows)} unplayed group fixtures")

    # ---- knockout bracket (official match-number progression) ----
    # place qualified thirds into their slots, per combination scenario
    combo_id = qual @ (1 << np.arange(12, dtype=np.int64))
    third_team = {m: np.zeros(n_sims, dtype=winners.dtype) for m in R32_THIRD_GROUPS}
    for cid in np.unique(combo_id):
        mask = combo_id == cid
        combo = tuple(g for g in group_letters if (int(cid) >> gidx[g]) & 1)
        for m, g in third_assignment(combo).items():
            third_team[m][mask] = thirds[mask, gidx[g]]

    def slot_team(m: int, slot: str):
        if slot == "T":
            return third_team[m]
        col = winners if slot[0] == "1" else runners
        return col[:, gidx[slot[1]]]

    pairs = {m: (slot_team(m, s1), slot_team(m, s2), False) for m, (s1, s2) in R32_SLOTS.items()}

    # Once football-data publishes the real R32 (groups final, thirds placed by
    # FIFA's own table), force the exact pairings: resolve each real fixture to
    # its match number via the winner/runner slot, which is unique per match.
    r32_real = ko_fx["R32"]
    if len(r32_real) == len(R32_SLOTS):
        const = {}
        for m, (s1, s2) in R32_SLOTS.items():
            for s in (s1, s2):
                if s != "T":
                    col = winners if s[0] == "1" else runners
                    v = col[:, gidx[s[1]]]
                    if (v == v[0]).all():
                        const.setdefault(m, set()).add(int(v[0]))
        resolved = {}
        for fx in r32_real:
            mno = next((m for m, teams_ in const.items()
                        if fx["h"] in teams_ or fx["a"] in teams_), None)
            if mno is not None and mno not in resolved:
                resolved[mno] = fx
        if len(resolved) == len(R32_SLOTS):
            pairs = {
                m: (np.full(n_sims, fx["h"]), np.full(n_sims, fx["a"]), fx["host_home"])
                for m, fx in resolved.items()
            }

    # knockout advancement decomposed on the goals model: the raw Elo
    # expectancy W_e credits half a draw as half a win, overrating favourites
    # who actually face near-coin pens after a 90' draw. Tabulate P(win 90')
    # and P(draw 90') over the Elo-gap range once, interpolate per match.
    ko_drs = np.arange(-800.0, 801.0, 20.0)
    ko_pw, ko_pd = [], []
    for dr in ko_drs:
        mat = dc_scoreline_matrix(*elo_lambdas(1600.0 + dr, 1600.0, False), dc_rho)
        ko_pw.append(float(np.tril(mat, -1).sum()))
        ko_pd.append(float(np.trace(mat)))
    ko_pw, ko_pd = np.array(ko_pw), np.array(ko_pd)
    et_shrink = config.MODEL_PARAMS["KO_ET_SHRINK"]

    def play(h, a, stage, host_home=False):
        """Winner per sim: P(win 90') + P(draw 90')·p_ET-and-pens, overridden by
        real decided results wherever the simulated pairing matches a real fixture."""
        adv = config.ELO_HOME_ADV if host_home else 0
        dr = E[h] - E[a] + adv
        we = 1.0 / (1.0 + 10 ** (-dr / 400.0))
        p_et = 0.5 + (we - 0.5) * et_shrink
        p = np.interp(dr, ko_drs, ko_pw) + np.interp(dr, ko_drs, ko_pd) * p_et
        w = np.where(rng.random(n_sims) < p, h, a)
        for fx in ko_fx.get(stage, []):
            if fx["winner"] is None:
                continue
            mask = ((h == fx["h"]) & (a == fx["a"])) | ((h == fx["a"]) & (a == fx["h"]))
            if mask.any():
                w = np.where(mask, fx["winner"], w)
        return w

    wins: dict[int, np.ndarray] = {}
    for m in sorted(R32_SLOTS):
        h, a, host = pairs[m]
        wins[m] = play(h, a, "R32", host)
    for stage, mapping in (("R16", R16_MAP), ("QF", QF_MAP), ("SF", SF_MAP)):
        for m, (m1, m2) in mapping.items():
            wins[m] = play(wins[m1], wins[m2], stage)
    champ = play(wins[FINAL[0]], wins[FINAL[1]], "F")

    def freq(*arrays):
        stacked = np.stack(arrays, axis=1) if len(arrays) > 1 else arrays[0]
        return np.bincount(stacked.ravel(), minlength=nt) / n_sims

    adv = freq(qualifiers)
    r16 = freq(*(wins[m] for m in R32_SLOTS))      # won their R32 match
    qf = freq(*(wins[m] for m in R16_MAP))         # won their R16 match
    sf = freq(*(wins[m] for m in QF_MAP))
    fin = freq(*(wins[m] for m in SF_MAP))
    ch = freq(champ)

    rows = []
    for i in range(nt):
        rows.append(
            {
                "pipeline": pipeline,
                "team_id": int(ids[i]),
                "advance_grp": round(float(adv[i]), 4),
                "reach_r16": round(float(r16[i]), 4),
                "reach_qf": round(float(qf[i]), 4),
                "reach_sf": round(float(sf[i]), 4),
                "reach_final": round(float(fin[i]), 4),
                "champion": round(float(ch[i]), 4),
                "n_sims": n_sims,
                "model_version": model_version,
            }
        )
    print(f"[simulate:{pipeline}] {n_sims} sims complete; favourite champion p={ch.max():.3f}")
    return rows


if __name__ == "__main__":
    run()
