// Metric math. Every function returns null (UNKNOWN) instead of guessing when inputs are missing.
// Definitions mirror METRICS.md — change both together.

const num = (x) => (typeof x === 'number' && Number.isFinite(x) ? x : null);
const ratio = (a, b) => (num(a) === null || num(b) === null || b === 0 ? null : a / b);

export const conversionRate = (orders, sessions) => ratio(orders, sessions);
export const addToCartRate = (sessionsWithAtc, sessions) => ratio(sessionsWithAtc, sessions);
export const checkoutRate = (sessionsReachedCheckout, sessions) => ratio(sessionsReachedCheckout, sessions);
export const aov = (revenue, orders) => ratio(revenue, orders);
export const ctr = (clicks, impressions) => ratio(clicks, impressions);
export const cpc = (spend, clicks) => ratio(spend, clicks);
export const cpm = (spend, impressions) => { const r = ratio(spend, impressions); return r === null ? null : r * 1000; };
export const cpa = (spend, conversions) => ratio(spend, conversions);
export const roas = (attributedRevenue, spend) => ratio(attributedRevenue, spend);
export const refundRate = (refundedAmount, revenue) => ratio(refundedAmount, revenue);

/**
 * Contribution margin per order BEFORE ads (unit economics).
 * @param {{ price:number, cogs:number, shippingCost:number, paymentFeePct:number, paymentFeeFixed:number,
 *           shippingCharged?:number, expectedRefundRate?:number, discount?:number }} u
 */
export function unitContribution(u) {
  const vals = [u.price, u.cogs, u.shippingCost, u.paymentFeePct, u.paymentFeeFixed];
  if (vals.some((v) => num(v) === null)) return null;
  const shippingCharged = u.shippingCharged ?? 0;
  const discount = u.discount ?? 0;
  const revenue = u.price + shippingCharged - discount;
  const fees = revenue * u.paymentFeePct + u.paymentFeeFixed;
  const refundLoss = revenue * (u.expectedRefundRate ?? 0);
  const contribution = revenue - u.cogs - u.shippingCost - fees - refundLoss;
  return {
    revenue,
    contribution,
    marginPct: revenue > 0 ? contribution / revenue : null,
    breakEvenRoas: contribution > 0 ? revenue / contribution : null, // ROAS needed to break even on first order
    maxCpa: contribution > 0 ? contribution : 0,
  };
}

/** Evaluate unit economics across a range: returns {low, high} for each scenario object. */
export function unitContributionRange(low, high) {
  return { low: unitContribution(low), high: unitContribution(high) };
}

/** Aggregate period P&L from measured totals. Missing inputs -> null fields, never zero-filled. */
export function periodSummary(t) {
  const contribution = [t.revenue, t.cogs, t.shippingCost, t.fees, t.refunds, t.adSpend].every((v) => num(v) !== null)
    ? t.revenue - t.cogs - t.shippingCost - t.fees - t.refunds - t.adSpend
    : null;
  return {
    revenue: num(t.revenue),
    orders: num(t.orders),
    aov: aov(t.revenue, t.orders),
    conversionRate: conversionRate(t.orders, t.sessions),
    roas: roas(t.attributedRevenue ?? null, t.adSpend),
    cac: cpa(t.adSpend, t.newCustomers),
    refundRate: refundRate(t.refunds, t.revenue),
    contribution,
  };
}

/**
 * Two-proportion z-test (e.g. CVR control vs variant). Returns null if samples too small.
 * @returns {{ lift:number, z:number, pValue:number } | null}
 */
export function twoProportionTest(convA, nA, convB, nB, minN = 100) {
  if ([convA, nA, convB, nB].some((v) => num(v) === null) || nA < minN || nB < minN) return null;
  const pA = convA / nA; const pB = convB / nB;
  const p = (convA + convB) / (nA + nB);
  const se = Math.sqrt(p * (1 - p) * (1 / nA + 1 / nB));
  if (se === 0) return null;
  const z = (pB - pA) / se;
  return { lift: pA === 0 ? null : (pB - pA) / pA, z, pValue: 2 * (1 - normalCdf(Math.abs(z))) };
}

function normalCdf(x) {
  // Abramowitz–Stegun 7.1.26 approximation of erf
  const t = 1 / (1 + 0.3275911 * (x / Math.SQRT2));
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-(x * x) / 2);
  return 0.5 * (1 + y);
}
