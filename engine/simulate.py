"""Monte Carlo tournament simulation (>=20,000 runs, vectorised with numpy).

Group stage: finished results are fixed, remaining matches sampled from the
Elo->Poisson model. Top 2 per group + 8 best third-placed teams advance (48-team
format). Knockout rounds use the real bracket pairings whenever football-data.org
has published them (all matches of a round with both teams known); otherwise a
simplified Elo reseed pairs best vs worst among survivors — swapping that for the
exact FIFA R32 mapping is the Phase 7 upgrade (spec §12).
"""
import numpy as np

from . import config
from .db import sb
from .match_model import elo_lambdas

ROUND_SIZES = {"R32": 16, "R16": 8, "QF": 4, "SF": 2, "F": 1}


def _load():
    teams = sb().table("teams").select("id, elo, group_code").execute().data
    fixtures = (
        sb()
        .table("fixtures")
        .select("stage, group_code, home_id, away_id, status, home_goals, away_goals, host_home")
        .neq("stage", "3P")
        .execute()
        .data
    )
    return teams, fixtures


def run(n_sims: int = config.N_SIMS):
    teams, fixtures = _load()
    rng = np.random.default_rng()

    idx = {t["id"]: i for i, t in enumerate(teams)}
    ids = np.array([t["id"] for t in teams])
    E = np.array([float(t["elo"]) for t in teams])
    nt = len(teams)

    groups: dict[str, list[int]] = {}
    for t in teams:
        if t["group_code"]:
            groups.setdefault(t["group_code"], []).append(idx[t["id"]])
    if len(groups) != 12 or any(len(m) != 4 for m in groups.values()):
        print(f"[simulate] groups incomplete ({len(groups)} groups) — skipping simulation until ingest fills them")
        return None

    group_fx: dict[str, list] = {g: [] for g in groups}
    knockout_fx: dict[str, list] = {s: [] for s in ROUND_SIZES}
    for f in fixtures:
        if f["home_id"] is None or f["away_id"] is None:
            continue
        h, a = idx.get(f["home_id"]), idx.get(f["away_id"])
        if h is None or a is None:
            continue
        rec = (h, a, f["status"] == "finished", f["home_goals"], f["away_goals"], bool(f["host_home"]))
        if f["stage"] == "group" and f["group_code"] in group_fx:
            group_fx[f["group_code"]].append(rec)
        elif f["stage"] in knockout_fx:
            knockout_fx[f["stage"]].append(rec)

    def match_goals(h, a, finished, hg, ag, host_home):
        if finished and hg is not None:
            return np.full(n_sims, hg), np.full(n_sims, ag)
        lam_h, lam_a = elo_lambdas(E[h], E[a], host_home)
        return rng.poisson(lam_h, n_sims), rng.poisson(lam_a, n_sims)

    # ---- group stage ----
    winners, runners, thirds = [], [], []
    third_keys = []
    for g in sorted(groups):
        T = np.array(groups[g])
        local = {t: i for i, t in enumerate(T)}
        pts = np.zeros((n_sims, 4))
        gf = np.zeros((n_sims, 4))
        ga = np.zeros((n_sims, 4))
        matches = group_fx[g] or [
            (int(T[i]), int(T[j]), False, None, None, False) for i in range(4) for j in range(i + 1, 4)
        ]
        for h, a, finished, hg_v, ag_v, host_home in matches:
            i, j = local[h], local[a]
            hg, ag = match_goals(h, a, finished, hg_v, ag_v, host_home)
            pts[:, i] += 3 * (hg > ag) + (hg == ag)
            pts[:, j] += 3 * (ag > hg) + (hg == ag)
            gf[:, i] += hg
            ga[:, i] += ag
            gf[:, j] += ag
            ga[:, j] += hg
        # rank: points, goal diff, goals for, random jitter as the final tiebreak
        key = pts * 10000 + (gf - ga) * 100 + gf + rng.random((n_sims, 4))
        order = np.argsort(-key, axis=1)
        winners.append(T[order[:, 0]])
        runners.append(T[order[:, 1]])
        thirds.append(T[order[:, 2]])
        third_keys.append(np.take_along_axis(key, order[:, 2:3], axis=1)[:, 0])

    winners = np.stack(winners, axis=1)   # (n_sims, 12)
    runners = np.stack(runners, axis=1)
    thirds = np.stack(thirds, axis=1)
    third_keys = np.stack(third_keys, axis=1)

    # best 8 thirds across the 12 groups
    third_order = np.argsort(-third_keys, axis=1)[:, :8]
    thirds_q = np.take_along_axis(thirds, third_order, axis=1)  # (n_sims, 8)

    qualifiers = np.concatenate([winners, runners, thirds_q], axis=1)  # (n_sims, 32)

    # ---- knockout rounds ----
    def play_round(P, stage):
        m = P.shape[1]
        real = knockout_fx.get(stage, [])
        if len(real) == ROUND_SIZES[stage]:
            # real bracket published: use exact pairings, fix finished results
            cols = []
            for h, a, finished, hg, ag, host_home in real:
                if finished and hg is not None and hg != ag:
                    cols.append(np.full(n_sims, h if hg > ag else a))
                else:
                    # unplayed — or finished level after 90' (pens): Elo-weighted coin
                    dr = E[h] - E[a] + (config.ELO_HOME_ADV if host_home else 0)
                    p = 1.0 / (1.0 + 10 ** (-dr / 400.0))
                    cols.append(np.where(rng.random(n_sims) < p, h, a))
            return np.stack(cols, axis=1)
        # simplified reseed: sort survivors by Elo, best vs worst
        order = np.argsort(-(E[P] + rng.random(P.shape) * 1e-6), axis=1)
        S = np.take_along_axis(P, order, axis=1)
        half = m // 2
        home, away = S[:, :half], S[:, ::-1][:, :half]
        p = 1.0 / (1.0 + 10 ** (-(E[home] - E[away]) / 400.0))
        win = rng.random((n_sims, half)) < p
        return np.where(win, home, away)

    p16 = play_round(qualifiers, "R32")
    p8 = play_round(p16, "R16")     # quarter-finalists
    p4 = play_round(p8, "QF")       # semi-finalists
    p2 = play_round(p4, "SF")       # finalists
    champ = play_round(p2, "F")     # (n_sims, 1)

    def freq(P):
        return np.bincount(P.ravel(), minlength=nt) / n_sims

    adv, qf, sf, fin, ch = freq(qualifiers), freq(p8), freq(p4), freq(p2), freq(champ)

    rows = []
    for i in range(nt):
        rows.append(
            {
                "team_id": int(ids[i]),
                "advance_grp": round(float(adv[i]), 4),
                "reach_qf": round(float(qf[i]), 4),
                "reach_sf": round(float(sf[i]), 4),
                "reach_final": round(float(fin[i]), 4),
                "champion": round(float(ch[i]), 4),
                "n_sims": n_sims,
                "model_version": config.MODEL_VERSION,
            }
        )
    print(f"[simulate] {n_sims} sims complete; favourite champion p={ch.max():.3f}")
    return rows


if __name__ == "__main__":
    run()
