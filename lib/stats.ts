/** Numerical primitives for correlation-aware combo pricing (v4.7): standard
 * normal CDF / inverse-CDF and the bivariate-normal CDF, composed into a
 * Gaussian-copula joint probability. Pure + unit-tested.
 *
 * Used to price same-game parlays from the markets' marginal probabilities: a
 * Gaussian copula maps each marginal to a latent normal, correlates them by ρ,
 * and reads back the joint — the standard same-game-parlay pricing approach. */

/** erf via Abramowitz & Stegun 7.1.26 (|error| < 1.5e-7). */
function erf(x: number): number {
  const t = 1 / (1 + 0.3275911 * Math.abs(x));
  const y =
    1 -
    ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) *
      t *
      Math.exp(-x * x);
  return x >= 0 ? y : -y;
}

/** Standard normal CDF Φ(x). */
export function normalCdf(x: number): number {
  return 0.5 * (1 + erf(x / Math.SQRT2));
}

// Acklam's inverse-normal-CDF rational approximation (|error| < 1.15e-9).
const A = [
  -3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2, 1.38357751867269e2,
  -3.066479806614716e1, 2.506628277459239,
];
const B = [
  -5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2, 6.680131188771972e1,
  -1.328068155288572e1,
];
const C = [
  -7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838, -2.549732539343734,
  4.374664141464968, 2.938163982698783,
];
const D = [7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996, 3.754408661907416];

/** Inverse standard normal CDF Φ⁻¹(p) (probit). */
export function invNormalCdf(p: number): number {
  if (p <= 0) return -Infinity;
  if (p >= 1) return Infinity;
  const plow = 0.02425;
  const phigh = 1 - plow;
  if (p < plow) {
    const q = Math.sqrt(-2 * Math.log(p));
    return (
      (((((C[0] * q + C[1]) * q + C[2]) * q + C[3]) * q + C[4]) * q + C[5]) /
      ((((D[0] * q + D[1]) * q + D[2]) * q + D[3]) * q + 1)
    );
  }
  if (p <= phigh) {
    const q = p - 0.5;
    const r = q * q;
    return (
      ((((((A[0] * r + A[1]) * r + A[2]) * r + A[3]) * r + A[4]) * r + A[5]) * q) /
      (((((B[0] * r + B[1]) * r + B[2]) * r + B[3]) * r + B[4]) * r + 1)
    );
  }
  const q = Math.sqrt(-2 * Math.log(1 - p));
  return -(
    (((((C[0] * q + C[1]) * q + C[2]) * q + C[3]) * q + C[4]) * q + C[5]) /
    ((((D[0] * q + D[1]) * q + D[2]) * q + D[3]) * q + 1)
  );
}

/** Bivariate standard normal CDF P(X≤h, Y≤k) with correlation ρ.
 * Uses the identity ∂Φ₂/∂ρ = φ₂, integrating the bivariate density from 0 to ρ
 * (Simpson's rule); Φ₂(h,k;0) = Φ(h)Φ(k). */
export function biNormalCdf(h: number, k: number, rho: number): number {
  const base = normalCdf(h) * normalCdf(k);
  if (rho === 0) return base;
  const r = Math.max(-0.999999, Math.min(0.999999, rho));
  const density = (s: number) => {
    const d = 1 - s * s;
    return Math.exp(-(h * h - 2 * s * h * k + k * k) / (2 * d)) / (2 * Math.PI * Math.sqrt(d));
  };
  const n = 200; // even; integrand is smooth over our |ρ| ≤ ~0.6 range
  const step = r / n;
  let sum = density(0) + density(r);
  for (let i = 1; i < n; i++) sum += (i % 2 ? 4 : 2) * density(i * step);
  return base + (sum * step) / 3;
}

/** Joint probability P(A and B) of two events with marginals p1, p2 under a
 * Gaussian copula with correlation ρ. ρ=0 → independence (p1·p2); ρ→1 →
 * comonotone (→ min(p1,p2)). Result is clamped to the Fréchet bounds. */
export function gaussianCopulaJoint(p1: number, p2: number, rho: number): number {
  const c = (p: number) => Math.max(1e-6, Math.min(1 - 1e-6, p));
  if (rho === 0) return p1 * p2;
  const joint = biNormalCdf(invNormalCdf(c(p1)), invNormalCdf(c(p2)), rho);
  const lo = Math.max(0, p1 + p2 - 1); // Fréchet lower bound
  const hi = Math.min(p1, p2); // Fréchet upper bound
  return Math.max(lo, Math.min(hi, joint));
}
