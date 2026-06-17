"""Same-game market correlations (v4.7) -> market_correlations.

Fits the tetrachoric correlation between same-game market CATEGORIES from
finished tournament matches — the ρ a Gaussian copula consumes to price
same-game combos on /strategies. Mirrors signals.py: reads live finished data
each refresh (no historical pull), empirical-Bayes shrinks toward a conservative
prior by the number of jointly-settleable matches, and upserts a table the
frontend reads. Early in the tournament it degrades to the prior instead of
overfitting a handful of games.

Only the goal-driven categories are modeled (the same set as lib/correlation.ts):
ftgoals (FT goal lines), btts, corners (total/team/1H corners), hfgoals (1H
goals). Result and correct-score are excluded — their dependence on goals isn't
a clean scalar, so the copula pricer never touches them.

Outcomes reuse settle_outcome (the exact definitions picks settle on), so the
correlation is over the same realised events the rest of the engine scores.
"""
import math
from datetime import datetime, timezone

from .config import MODEL_PARAMS
from .db import chunked, sb
from .write_db import _corner_counts, settle_outcome

WINDOW = "wc2026"

# Direction-normalized anchor selection per category (the "more events" side).
# One representative line per category keeps a single ρ per pair, matching the
# frontend's category model; ou25/corners_o95/ou15_1h sit near the middle of
# their ladders, so their marginals aren't degenerate.
ANCHOR = {
    "ftgoals": ("ou25", "over"),
    "btts": ("btts", "yes"),
    "corners": ("corners_o95", "over"),
    "hfgoals": ("ou15_1h", "over"),
}

# Conservative priors — MUST mirror the frontend fallback table
# (lib/correlation.ts BASE_RHO). Keys are sorted category pairs.
PRIOR_RHO = {
    ("btts", "ftgoals"): 0.55,
    ("corners", "ftgoals"): 0.30,
    ("ftgoals", "hfgoals"): 0.45,
    ("btts", "corners"): 0.20,
    ("btts", "hfgoals"): 0.30,
    ("corners", "hfgoals"): 0.20,
}

_SQRT2 = math.sqrt(2.0)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _inv_norm_cdf(p: float) -> float:
    """Φ⁻¹ by bisection on the monotone CDF (the engine has no scipy)."""
    lo, hi = -8.0, 8.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if _norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _binorm_cdf(h: float, k: float, rho: float) -> float:
    """Bivariate-normal CDF P(X≤h, Y≤k; ρ) via ∂Φ₂/∂ρ = φ₂ integrated 0→ρ
    (Simpson). Mirrors lib/stats.ts so engine and frontend price identically."""
    base = _norm_cdf(h) * _norm_cdf(k)
    if rho == 0:
        return base
    r = max(-0.999999, min(0.999999, rho))

    def dens(s: float) -> float:
        d = 1 - s * s
        return math.exp(-(h * h - 2 * s * h * k + k * k) / (2 * d)) / (2 * math.pi * math.sqrt(d))

    n = 200
    step = r / n
    total = dens(0.0) + dens(r)
    for i in range(1, n):
        total += (4 if i % 2 else 2) * dens(i * step)
    return base + total * step / 3


def tetrachoric(p1: float, p2: float, p12: float) -> float | None:
    """ρ such that Φ₂(Φ⁻¹p1, Φ⁻¹p2; ρ) = p12, by bisection (Φ₂ is increasing in
    ρ). None when a marginal is degenerate (no variation to correlate)."""
    eps = 1e-6
    if not (eps < p1 < 1 - eps and eps < p2 < 1 - eps):
        return None
    z1, z2 = _inv_norm_cdf(p1), _inv_norm_cdf(p2)
    lo, hi = -0.999, 0.999
    if p12 <= _binorm_cdf(z1, z2, lo):
        return lo
    if p12 >= _binorm_cdf(z1, z2, hi):
        return hi
    for _ in range(80):
        mid = (lo + hi) / 2
        if _binorm_cdf(z1, z2, mid) < p12:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def run():
    fixtures = (
        sb().table("fixtures")
        .select("id, home_goals, away_goals, duration, ht_home_goals, ht_away_goals, status")
        .eq("status", "finished")
        .execute().data
    )
    fixtures = [f for f in fixtures if f["home_goals"] is not None]
    now = datetime.now(timezone.utc).isoformat()
    if not fixtures:
        # Nothing realised yet — leave the prior to stand (frontend falls back to
        # BASE_RHO when the table is empty). Don't write zero-n rows.
        print("[correlations] no finished fixtures yet — priors stand")
        return

    corners = _corner_counts([f["id"] for f in fixtures])

    def realised(cat: str, f: dict):
        market, sel = ANCHOR[cat]
        ch, ca = corners.get(f["id"], (None, None))
        return settle_outcome(
            market, sel, f["home_goals"], f["away_goals"], ch, ca,
            f.get("duration") or "REGULAR", f.get("ht_home_goals"), f.get("ht_away_goals"),
        )

    # per category: fixture_id -> realised over/yes outcome (or None if unsettleable)
    outcomes = {cat: {f["id"]: realised(cat, f) for f in fixtures} for cat in ANCHOR}

    k0 = MODEL_PARAMS["CORR_PRIOR_K"]
    rows = []
    for (ca, cb), prior in PRIOR_RHO.items():
        pairs = [
            (outcomes[ca][f["id"]], outcomes[cb][f["id"]])
            for f in fixtures
            if outcomes[ca][f["id"]] is not None and outcomes[cb][f["id"]] is not None
        ]
        n = len(pairs)
        rho = prior
        if n:
            p1 = sum(1 for a, _ in pairs if a) / n
            p2 = sum(1 for _, b in pairs if b) / n
            p12 = sum(1 for a, b in pairs if a and b) / n
            emp = tetrachoric(p1, p2, p12)
            if emp is not None:
                # empirical-Bayes shrink toward the prior by sample size
                rho = (n * emp + k0 * prior) / (n + k0)
        rows.append({
            "category_a": ca, "category_b": cb,
            "rho": round(rho, 3), "n": n, "computed_at": now,
        })

    for batch in chunked(rows):
        sb().table("market_correlations").upsert(batch, on_conflict="category_a,category_b").execute()
    fitted = sum(1 for r in rows if r["n"] > 0)
    print(f"[correlations] upserted {len(rows)} pairs ({fitted} with data; {len(fixtures)} finished matches)")


if __name__ == "__main__":
    run()
