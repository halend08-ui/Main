import { test } from 'node:test';
import assert from 'node:assert/strict';
import { assertWithinLimit, ApprovalRequiredError, resolveWriteMode, loadLimits, LIMIT_KINDS } from '../lib/core/guards.mjs';

const limits = (over = {}) => ({ currency: 'USD', ...Object.fromEntries(LIMIT_KINDS.map((k) => [k, 0])), ...over });

test('committed financial limits are all zero (default deny)', () => {
  const l = loadLimits();
  for (const k of LIMIT_KINDS) assert.equal(l[k], 0, `${k} should default to 0`);
});
test('zero limit requires approval for any amount, including 0.01', () => {
  assert.throws(() => assertWithinLimit('MAX_DAILY_AD_SPEND', 0.01, limits()), ApprovalRequiredError);
});
test('amount within positive limit passes; above throws', () => {
  const l = limits({ MAX_REFUND_WITHOUT_APPROVAL: 25 });
  assert.equal(assertWithinLimit('MAX_REFUND_WITHOUT_APPROVAL', 25, l), true);
  assert.throws(() => assertWithinLimit('MAX_REFUND_WITHOUT_APPROVAL', 25.01, l), ApprovalRequiredError);
});
test('rejects unknown kinds and invalid amounts', () => {
  assert.throws(() => assertWithinLimit('MAX_YOLO', 1, limits()), /Unknown limit/);
  assert.throws(() => assertWithinLimit('MAX_APP_PURCHASE', -5, limits()), /Invalid amount/);
  assert.throws(() => assertWithinLimit('MAX_APP_PURCHASE', NaN, limits()), /Invalid amount/);
});
test('write mode defaults to dry-run', () => {
  assert.equal(resolveWriteMode('a.myshopify.com', {}).live, false);
});
test('live mode requires allow-listed store', () => {
  const env = { COMMERCE_OS_WRITE_MODE: 'live', SHOPIFY_ALLOWED_STORES: 'dev-store.myshopify.com' };
  assert.equal(resolveWriteMode('dev-store.myshopify.com', env).live, true);
  assert.equal(resolveWriteMode('prod-store.myshopify.com', env).live, false);
});
