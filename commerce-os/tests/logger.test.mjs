import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createLogger, redact } from '../lib/core/logger.mjs';

test('redacts sensitive keys and token-like values', () => {
  const r = redact({ accessToken: 'abc', nested: { email: 'a@b.co', note: `token ${'shp' + 'at_' + '0123456789abcdef'.repeat(2)} here` }, ok: 1 });
  assert.equal(r.accessToken, '[REDACTED]');
  assert.equal(r.nested.email, '[REDACTED]');
  assert.match(r.nested.note, /\[REDACTED\]/);
  assert.doesNotMatch(r.nested.note, /shpat_/);
  assert.equal(r.ok, 1);
});
test('emits structured JSON with timestamp/operation and respects level', () => {
  const lines = [];
  const log = createLogger({ level: 'info', sink: (l) => lines.push(l) });
  log.debug('hidden');
  log.info('product.update', { resource: 'gid://shopify/Product/1', result: 'ok', changeId: 'EXP-001' });
  assert.equal(lines.length, 1);
  const e = JSON.parse(lines[0]);
  assert.equal(e.operation, 'product.update');
  assert.equal(e.changeId, 'EXP-001');
  assert.ok(Date.parse(e.timestamp));
});
