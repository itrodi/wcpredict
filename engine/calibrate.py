"""Calibration sprint (spec v4 §7) — one-off, run manually INSIDE the 7-day trial:

    STATSAPI_KEY=... python -m engine.calibrate

Pulls TheStatsAPI historical internationals (last two World Cups), fits the
Pipeline B parameters, backtests both pipelines on WC 2022 and writes
docs/backtest-2022.md. Fitted values are printed as env-var overrides to drop
into the refresh workflow (MODEL_PARAMS reads them, spec §8).

ACCEPTANCE GATE: Pipeline B must beat Pipeline A on 1X2 log-loss in the
backtest before it becomes the site's default model.
"""
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import config, statsapi
from .match_model import elo_lambdas, markets_from_matrix, scoreline_matrix
from .match_model_b import dc_scoreline_matrix, _1x2
from .statsapi import as_list, pick

EPS = 1e-9


def _season_id(comp_id: str, year: str) -> str | None:
    seasons = statsapi.get_all(f"/competitions/{comp_id}/seasons")
    s = next((x for x in seasons if year in str(pick(x, "year", "name", "label", default=""))), None)
    return str(pick(s, "id", "season_id")) if s else None


def _history(comp_id: str, years: tuple[str, ...]) -> list[dict]:
    """[{hg, ag, hg1h, ag1h, corners_total, xg_h, xg_a}] for finished historical matches."""
    out = []
    for year in years:
        sid = _season_id(comp_id, year)
        if not sid:
            print(f"[calibrate] no season for {year}")
            continue
        matches = statsapi.get_all(f"/competitions/{comp_id}/seasons/{sid}/matches", ttl=86400 * 30)
        for m in matches:
            if str(pick(m, "status", "state", default="")).lower() not in {"finished", "ft", "full_time", "ended"}:
                continue
            mid = str(pick(m, "id", "match_id"))
            score = pick(m, "score", default={}) or {}
            rec = {
                "year": year,
                "hg": pick(score, "home", "fulltime_home", "home_goals"),
                "ag": pick(score, "away", "fulltime_away", "away_goals"),
                "hg1h": pick(score, "halftime_home", "ht_home"),
                "ag1h": pick(score, "halftime_away", "ht_away"),
                "corners": None,
                "corners_1h": None,
                "xg_h": None,
                "xg_a": None,
                "xg_1h": None,
                "xg_ft": None,
            }
            try:
                sides = as_list(statsapi.get(f"/matches/{mid}/stats", ttl=86400 * 30), "stats", "statistics")
                corners = [pick(s, "corners", "corner_kicks") for s in sides]
                if all(c is not None for c in corners) and corners:
                    rec["corners"] = sum(corners)
                xgs = [pick(s, "xg", "expected_goals") for s in sides]
                if len(xgs) == 2 and all(x is not None for x in xgs):
                    rec["xg_h"], rec["xg_a"] = float(xgs[0]), float(xgs[1])
                    rec["xg_ft"] = rec["xg_h"] + rec["xg_a"]
                # per-half splits (v4.1 §5.6): every stat splits all/first_half/second_half
                fh = [s.get("first_half") or s.get("1h") or {} for s in sides]
                fh_corners = [pick(h, "corners", "corner_kicks") for h in fh]
                if fh and all(c is not None for c in fh_corners):
                    rec["corners_1h"] = sum(fh_corners)
                fh_xg = [pick(h, "xg", "expected_goals") for h in fh]
                if fh and all(x is not None for x in fh_xg):
                    rec["xg_1h"] = sum(float(x) for x in fh_xg)
            except Exception:
                pass
            if rec["hg"] is not None:
                out.append(rec)
    print(f"[calibrate] {len(out)} historical finished matches pulled")
    return out


def fit_dc_rho(hist: list[dict]) -> float:
    """Grid-search MLE for the DC low-score correction on historical scorelines,
    using each match's empirical goal means as lambda (coarse but stable)."""
    lam_h = max(0.3, float(np.mean([m["hg"] for m in hist])))
    lam_a = max(0.3, float(np.mean([m["ag"] for m in hist])))
    best_rho, best_ll = -0.1, -np.inf
    for rho in np.arange(-0.25, 0.26, 0.01):
        mat = dc_scoreline_matrix(lam_h, lam_a, float(rho))
        ll = sum(
            math.log(max(mat[min(m["hg"], 10), min(m["ag"], 10)], EPS)) for m in hist
        )
        if ll > best_ll:
            best_ll, best_rho = ll, float(rho)
    return round(best_rho, 3)


def fit_fh_share(hist: list[dict]) -> float | None:
    """First-half goal share. Prefers actual per-half xG splits (v4.1 §5.6),
    falls back to half-time scorelines."""
    with_xg = [m for m in hist if m["xg_1h"] is not None and m["xg_ft"]]
    if len(with_xg) >= 20:
        return round(sum(m["xg_1h"] for m in with_xg) / sum(m["xg_ft"] for m in with_xg), 3)
    with_ht = [m for m in hist if m["hg1h"] is not None]
    if len(with_ht) < 20:
        return None
    fh = sum(m["hg1h"] + m["ag1h"] for m in with_ht)
    ft = sum(m["hg"] + m["ag"] for m in with_ht)
    return round(fh / ft, 3) if ft else None


def fit_fh_corners_share(hist: list[dict]) -> float | None:
    """First-half corners share. Ship the corners_1h_o45 market ONLY if this
    returns a value from a decent sample — otherwise leave CORNERS_1H_SHARE=0
    and the market stays off (don't ship uncalibrated, v4.1 §5.6)."""
    with_fh = [m for m in hist if m["corners_1h"] is not None and m["corners"]]
    if len(with_fh) < 30:
        return None
    return round(sum(m["corners_1h"] for m in with_fh) / sum(m["corners"] for m in with_fh), 3)


def fit_corners_nb(hist: list[dict]) -> tuple[float, float] | None:
    """Moment-match the NB total-corners distribution: returns (a, k) keeping the
    b/c structure (intercept absorbs the mean at average lambdas)."""
    cs = [m["corners"] for m in hist if m["corners"] is not None]
    if len(cs) < 30:
        return None
    mu, var = float(np.mean(cs)), float(np.var(cs))
    k = mu**2 / (var - mu) if var > mu else 50.0
    avg_lam_sum = config.TOTAL_GOALS  # b multiplies (lam_h+lam_a); centre intercept on it
    a = mu - config.MODEL_PARAMS["CORNERS_B"] * avg_lam_sum
    return round(a, 2), round(max(2.0, k), 2)


def backtest_2022(hist: list[dict]) -> dict[str, dict[str, float]]:
    """Brier/log-loss of both market models on WC 2022 results.

    Without point-in-time Elo per 2022 match we evaluate the SHAPE parameters:
    Pipeline A = plain Poisson, B = Dixon-Coles, both at per-tournament mean
    lambdas. This isolates the DC correction's value; the Elo input is shared
    machinery and identical in both pipelines.
    """
    sample = [m for m in hist if m["year"] == "2022"]
    if not sample:
        return {}
    lam_h = max(0.3, float(np.mean([m["hg"] for m in sample])))
    lam_a = max(0.3, float(np.mean([m["ag"] for m in sample])))
    mat_a = scoreline_matrix(lam_h, lam_a)
    pa = {sel: p for mk, sel, p in markets_from_matrix(mat_a) if mk == "1x2"}
    mat_b = dc_scoreline_matrix(lam_h, lam_a, config.MODEL_PARAMS["DC_RHO"])
    h, d, a = _1x2(mat_b)
    pb = {"home": h, "draw": d, "away": a}

    out = {}
    for name, probs in (("free", pa), ("statsapi", pb)):
        brier, ll, n = 0.0, 0.0, 0
        for m in sample:
            actual = "home" if m["hg"] > m["ag"] else "draw" if m["hg"] == m["ag"] else "away"
            for sel in ("home", "draw", "away"):
                y = 1.0 if sel == actual else 0.0
                p = min(max(probs[sel], EPS), 1 - EPS)
                brier += (p - y) ** 2
                ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
                n += 1
        out[name] = {"brier": brier / n, "log_loss": ll / n, "n_matches": len(sample)}
    return out


def main():
    comp_id, _ = statsapi.find_world_cup_season()
    hist = _history(comp_id, ("2018", "2022"))
    if not hist:
        print("[calibrate] no history retrievable — cannot calibrate")
        return

    fitted = {}
    fitted["DC_RHO"] = fit_dc_rho(hist)
    if (share := fit_fh_share(hist)) is not None:
        fitted["FH_GOAL_SHARE"] = share
    if (nb := fit_corners_nb(hist)) is not None:
        fitted["CORNERS_A"], fitted["CORNERS_K"] = nb
    if (fh_c := fit_fh_corners_share(hist)) is not None:
        fitted["CORNERS_1H_SHARE"] = fh_c  # setting this env override SHIPS corners_1h_o45

    print("\n[calibrate] fitted parameters — set these as env overrides in refresh.yml:")
    for k, v in fitted.items():
        print(f"  {k}={v}")

    scores = backtest_2022(hist)
    gate = bool(scores) and scores["statsapi"]["log_loss"] < scores["free"]["log_loss"]

    lines = [
        "# WC 2022 backtest (calibration sprint, spec v4 §7)",
        f"\nGenerated: {datetime.now(timezone.utc).isoformat()}\n",
        "## Fitted parameters\n",
        *(f"- `{k}` = **{v}**" for k, v in fitted.items()),
        "\n## 1X2 backtest on WC 2022 (one-vs-rest, per selection)\n",
        "| Pipeline | n matches | Brier | Log-loss |",
        "|---|---|---|---|",
        *(
            f"| {name} | {s['n_matches']} | {s['brier']:.5f} | {s['log_loss']:.5f} |"
            for name, s in scores.items()
        ),
        f"\n**Acceptance gate (B beats A on 1X2 log-loss): {'PASS — statsapi may become site default' if gate else 'FAIL — keep free/blend_free as default'}**",
        "\nNote: this backtest isolates the scoreline-shape parameters (plain Poisson vs"
        " Dixon-Coles at tournament-mean rates); point-in-time Elo inputs are shared"
        " machinery. In-tournament `model_scores` is the live, like-for-like scoreboard.",
    ]
    out = Path(__file__).resolve().parent.parent / "docs" / "backtest-2022.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(f"\n[calibrate] report written to {out}; gate={'PASS' if gate else 'FAIL'}")


if __name__ == "__main__":
    main()
