import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as m from '../lib/analytics/metrics.mjs';

test('ratios return null (UNKNOWN) instead of guessing', () => {
  assert.equal(m.conversionRate(5, 0), null);
  assert.equal(m.aov(undefined, 3), null);
  assert.equal(m.conversionRate(5, 250), 0.02);
  assert.equal(m.cpm(10, 2000), 5);
});
test('unit contribution and break-even ROAS', () => {
  const u = m.unitContribution({ price: 40, cogs: 10, shippingCost: 5, paymentFeePct: 0.029, paymentFeeFixed: 0.3, expectedRefundRate: 0.05 });
  // revenue 40; fees 1.46; refund loss 2; contribution 40-10-5-1.46-2 = 21.54
  assert.ok(Math.abs(u.contribution - 21.54) < 1e-9);
  assert.ok(Math.abs(u.breakEvenRoas - 40 / 21.54) < 1e-9);
  assert.equal(m.unitContribution({ price: 40 }), null);
});
test('negative contribution has no break-even ROAS', () => {
  const u = m.unitContribution({ price: 10, cogs: 9, shippingCost: 5, paymentFeePct: 0.03, paymentFeeFixed: 0.3 });
  assert.ok(u.contribution < 0);
  assert.equal(u.breakEvenRoas, null);
  assert.equal(u.maxCpa, 0);
});
test('periodSummary leaves contribution null if any cost input missing', () => {
  const s = m.periodSummary({ revenue: 1000, orders: 20, sessions: 1000, cogs: 300, shippingCost: 100, fees: 35, refunds: 0 });
  assert.equal(s.contribution, null);
  assert.equal(s.aov, 50);
  assert.equal(s.conversionRate, 0.02);
});
test('two-proportion test: requires min sample; detects large effect', () => {
  assert.equal(m.twoProportionTest(1, 50, 2, 50), null);
  const r = m.twoProportionTest(100, 5000, 160, 5000);
  assert.ok(r.pValue < 0.01);
  assert.ok(Math.abs(r.lift - 0.6) < 1e-9);
  const same = m.twoProportionTest(100, 5000, 101, 5000);
  assert.ok(same.pValue > 0.5);
});
