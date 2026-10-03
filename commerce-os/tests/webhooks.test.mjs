import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { verifyShopifyHmac, createIdempotencyStore, handleWebhookDelivery } from '../lib/webhooks/verify.mjs';

const secret = 'test-secret';
const body = Buffer.from('{"id":1,"title":"x"}');
const sig = createHmac('sha256', secret).update(body).digest('base64');

test('valid HMAC verifies; tampered body or wrong secret fails', () => {
  assert.equal(verifyShopifyHmac(body, sig, secret), true);
  assert.equal(verifyShopifyHmac(Buffer.from('{"id":2}'), sig, secret), false);
  assert.equal(verifyShopifyHmac(body, sig, 'other'), false);
  assert.equal(verifyShopifyHmac(body, undefined, secret), false);
  assert.equal(verifyShopifyHmac(body, 'short', secret), false);
});
test('missing secret throws (fail closed)', () => {
  assert.throws(() => verifyShopifyHmac(body, sig, ''), /secret/);
});
test('idempotency store dedupes and expires', () => {
  let t = 0;
  const s = createIdempotencyStore({ ttlMs: 10, now: () => t });
  assert.equal(s.claim('a'), true);
  assert.equal(s.claim('a'), false);
  t = 11;
  assert.equal(s.claim('a'), true);
});
test('handleWebhookDelivery: 401 on bad HMAC, dedupes by event id (case-insensitive headers)', () => {
  const store = createIdempotencyStore();
  const headers = { 'x-shopify-hmac-sha256': sig, 'X-Shopify-Event-Id': 'evt-1', 'X-Shopify-Topic': 'orders/create' };
  assert.equal(handleWebhookDelivery({ rawBody: body, headers: { ...headers, 'x-shopify-hmac-sha256': 'bad' }, secret, store }).status, 401);
  const first = handleWebhookDelivery({ rawBody: body, headers, secret, store });
  assert.deepEqual([first.status, first.process, first.topic], [200, true, 'orders/create']);
  const dup = handleWebhookDelivery({ rawBody: body, headers, secret, store });
  assert.deepEqual([dup.status, dup.process, dup.reason], [200, false, 'duplicate']);
});
