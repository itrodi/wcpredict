/** Numerical primitives for correlation-aware pricing (v4.7): normal CDF,
 * inverse CDF, bivariate-normal CDF and the Gaussian copula. Run: npm test */
import assert from "node:assert/strict";
import test from "node:test";

import { biNormalCdf, gaussianCopulaJoint, invNormalCdf, normalCdf } from "../lib/stats";

const close = (a: number, b: number, tol = 1e-3) => Math.abs(a - b) < tol;

test("normalCdf hits known values", () => {
  assert.ok(close(normalCdf(0), 0.5));
  assert.ok(close(normalCdf(1.959964), 0.975));
  assert.ok(close(normalCdf(-1.959964), 0.025));
});

test("invNormalCdf inverts normalCdf", () => {
  assert.ok(close(invNormalCdf(0.5), 0));
  assert.ok(close(invNormalCdf(0.975), 1.959964));
  for (const p of [0.05, 0.3, 0.62, 0.88]) {
    assert.ok(close(normalCdf(invNormalCdf(p)), p), `round-trip p=${p}`);
  }
});

test("biNormalCdf(0,0;ρ) matches the closed form 1/4 + asin(ρ)/2π", () => {
  for (const rho of [-0.6, -0.3, 0, 0.4, 0.8]) {
    const expected = 0.25 + Math.asin(rho) / (2 * Math.PI);
    assert.ok(close(biNormalCdf(0, 0, rho), expected), `ρ=${rho}`);
  }
});

test("gaussianCopulaJoint: ρ=0 is independence", () => {
  assert.ok(close(gaussianCopulaJoint(0.6, 0.5, 0), 0.3));
});

test("gaussianCopulaJoint: positive ρ raises the joint above the product", () => {
  const indep = 0.6 * 0.5;
  const corr = gaussianCopulaJoint(0.6, 0.5, 0.55);
  assert.ok(corr > indep, "positive correlation lifts the joint");
  assert.ok(corr <= Math.min(0.6, 0.5) + 1e-9, "stays within the Fréchet upper bound");
});

test("gaussianCopulaJoint: ρ→1 approaches min(p1,p2)", () => {
  assert.ok(close(gaussianCopulaJoint(0.6, 0.5, 0.999), 0.5, 5e-3));
});

test("gaussianCopulaJoint: negative ρ lowers the joint below the product", () => {
  assert.ok(gaussianCopulaJoint(0.6, 0.5, -0.5) < 0.3);
});
