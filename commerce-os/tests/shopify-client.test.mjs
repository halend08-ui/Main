import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createAdminClient, throttleDelayMs, assertNoUserErrors, loadOperation, DEFAULT_API_VERSION } from '../lib/shopify/client.mjs';
import { productSet } from '../lib/shopify/operations.mjs';

const silent = { debug() {}, info() {}, warn() {}, error() {} };
const json = (status, body) => ({ status, ok: status >= 200 && status < 300, json: async () => body });
const mk = (fetchImpl, env = {}) => createAdminClient({
  storeDomain: 'dev-store.myshopify.com', accessToken: 'x', fetchImpl, logger: silent, env, sleepImpl: async () => {},
});

test('pinned API version is the configured one', () => {
  assert.equal(mk(async () => json(200, { data: {} })).endpoint, `https://dev-store.myshopify.com/admin/api/${DEFAULT_API_VERSION}/graphql.json`);
});
test('rejects non-myshopify domains', () => {
  assert.throws(() => createAdminClient({ storeDomain: 'evil.com', accessToken: 'x' }), /myshopify/);
});
test('query returns data and sends token header', async () => {
  let seen;
  const c = mk(async (url, init) => { seen = init; return json(200, { data: { shop: { name: 'S' } } }); });
  assert.deepEqual(await c.query('query { shop { name } }'), { shop: { name: 'S' } });
  assert.equal(seen.headers['X-Shopify-Access-Token'], 'x');
});
test('query() refuses mutations', () => {
  assert.throws(() => mk(async () => json(200, {})).query('mutation X { a }'), /mutate/);
});
test('mutate is dry-run by default and does not call fetch', async () => {
  let called = false;
  const c = mk(async () => { called = true; return json(200, { data: {} }); });
  const r = await productSet(c, { title: 'T' }, { identifier: { handle: 't' } });
  assert.equal(r.dryRun, true);
  assert.equal(called, false);
});
test('live mutate surfaces userErrors', async () => {
  const env = { COMMERCE_OS_WRITE_MODE: 'live', SHOPIFY_ALLOWED_STORES: 'dev-store.myshopify.com' };
  const c = mk(async () => json(200, { data: { productSet: { product: null, userErrors: [{ field: ['input', 'title'], message: 'blank' }] } } }), env);
  await assert.rejects(productSet(c, { title: '' }), /input\.title: blank/);
});
test('retries THROTTLED then succeeds', async () => {
  let n = 0;
  const c = mk(async () => (++n === 1
    ? json(200, { errors: [{ message: 'Throttled', extensions: { code: 'THROTTLED' } }], extensions: { cost: { requestedQueryCost: 100, throttleStatus: { currentlyAvailable: 50, restoreRate: 50 } } } })
    : json(200, { data: { ok: true } })));
  assert.deepEqual(await c.query('query { ok }'), { ok: true });
  assert.equal(n, 2);
});
test('live mutation is NOT retried after network error', async () => {
  const env = { COMMERCE_OS_WRITE_MODE: 'live', SHOPIFY_ALLOWED_STORES: 'dev-store.myshopify.com' };
  let n = 0;
  const c = mk(async () => { n++; throw new Error('ECONNRESET'); }, env);
  await assert.rejects(c.mutate('mutation { x }', {}, { operation: 'X' }), /ECONNRESET/);
  assert.equal(n, 1);
});
test('throttleDelayMs computes deficit / restoreRate', () => {
  assert.equal(throttleDelayMs({ requestedQueryCost: 150, throttleStatus: { currentlyAvailable: 50, restoreRate: 50 } }), 2100);
  assert.equal(throttleDelayMs(undefined, 777), 777);
});
test('assertNoUserErrors passes clean payloads', () => {
  assert.deepEqual(assertNoUserErrors({ userErrors: [], x: 1 }), { userErrors: [], x: 1 });
});
test('all operation files load', () => {
  for (const n of ['ShopInfo', 'ProductSet', 'InventorySetQuantities', 'OrdersForAnalytics', 'WebhookSubscriptionCreate']) {
    assert.match(loadOperation(n), /(query|mutation) /);
  }
});
test('OrdersForAnalytics selects no customer PII', () => {
  assert.doesNotMatch(loadOperation('OrdersForAnalytics'), /\b(email|phone|customer\s*\{|shippingAddress|billingAddress|firstName|lastName)\b/);
});
