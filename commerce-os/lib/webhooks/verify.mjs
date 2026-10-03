// Shopify HTTPS webhook authentication + idempotency.
// HMAC: base64(HMAC-SHA256(rawBody, appClientSecret)) compared to X-Shopify-Hmac-Sha256.
// Dedupe: X-Shopify-Event-Id (same event across retries), falling back to X-Shopify-Webhook-Id.
// NOTE: header semantics re-verify against shopify.dev when network allows (RISK-005).
import { createHmac, timingSafeEqual } from 'node:crypto';

/** Case-insensitive header lookup for plain objects or Headers instances. */
export function header(headers, name) {
  if (typeof headers?.get === 'function') return headers.get(name);
  const key = Object.keys(headers ?? {}).find((k) => k.toLowerCase() === name.toLowerCase());
  const v = key ? headers[key] : undefined;
  return Array.isArray(v) ? v[0] : v;
}

/**
 * @param {Buffer|string} rawBody exact bytes received (do NOT re-serialize parsed JSON)
 * @param {string|undefined} hmacHeader value of X-Shopify-Hmac-Sha256
 * @param {string} secret app client secret
 */
export function verifyShopifyHmac(rawBody, hmacHeader, secret) {
  if (!secret) throw new Error('Webhook secret not configured');
  if (!hmacHeader || typeof hmacHeader !== 'string') return false;
  const digest = createHmac('sha256', secret).update(rawBody).digest();
  let received;
  try { received = Buffer.from(hmacHeader, 'base64'); } catch { return false; }
  return received.length === digest.length && timingSafeEqual(received, digest);
}

/** Simple in-memory TTL idempotency store. Swap for a durable store (KV/DB) in production. */
export function createIdempotencyStore({ ttlMs = 48 * 3600 * 1000, now = () => Date.now() } = {}) {
  const seen = new Map();
  return {
    /** @returns {boolean} true if this id is new (and records it), false if duplicate */
    claim(id) {
      const t = now();
      for (const [k, exp] of seen) if (exp <= t) seen.delete(k);
      if (seen.has(id)) return false;
      seen.set(id, t + ttlMs);
      return true;
    },
    size: () => seen.size,
  };
}

/**
 * Authenticate + dedupe a webhook delivery. Returns { status, process, topic, eventId }.
 * Caller should respond with `status` quickly and run processing asynchronously when `process`.
 */
export function handleWebhookDelivery({ rawBody, headers, secret, store }) {
  if (!verifyShopifyHmac(rawBody, header(headers, 'X-Shopify-Hmac-Sha256'), secret)) {
    return { status: 401, process: false, reason: 'invalid_hmac' };
  }
  const eventId = header(headers, 'X-Shopify-Event-Id') ?? header(headers, 'X-Shopify-Webhook-Id');
  const topic = header(headers, 'X-Shopify-Topic');
  if (!eventId) return { status: 200, process: true, topic, eventId: null, reason: 'no_event_id' };
  if (!store.claim(eventId)) return { status: 200, process: false, topic, eventId, reason: 'duplicate' };
  return { status: 200, process: true, topic, eventId };
}
