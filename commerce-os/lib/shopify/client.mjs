// Shopify GraphQL Admin API client. Zero dependencies (Node >= 22 global fetch).
// - Reads (`query`) always allowed; writes (`mutate`) require live write mode + allow-listed store.
// - Backs off on cost-based throttling (THROTTLED) using extensions.cost.throttleStatus.
// - Mutations are never auto-retried after a network/5xx failure (they may have executed);
//   a THROTTLED response means the operation was not executed, so it is safe to retry.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createLogger } from '../core/logger.mjs';
import { resolveWriteMode } from '../core/guards.mjs';

const CONFIG = JSON.parse(
  readFileSync(fileURLToPath(new URL('../../config/shopify.json', import.meta.url)), 'utf8'),
);
export const DEFAULT_API_VERSION = CONFIG.adminApiVersion;
const OPERATIONS_DIR = new URL('./operations/', import.meta.url);

export class ShopifyGraphQLError extends Error {
  constructor(message, { errors, userErrors, status } = {}) {
    super(message);
    this.name = 'ShopifyGraphQLError';
    this.errors = errors;
    this.userErrors = userErrors;
    this.status = status;
  }
}

/** Load a GraphQL document from lib/shopify/operations/<name>.graphql */
export function loadOperation(name) {
  return readFileSync(new URL(`${name}.graphql`, OPERATIONS_DIR), 'utf8');
}

/** Throw if a mutation payload contains userErrors. Returns the payload otherwise. */
export function assertNoUserErrors(payload, operation = 'mutation') {
  const errs = payload?.userErrors ?? [];
  if (errs.length) {
    throw new ShopifyGraphQLError(
      `${operation} userErrors: ${errs.map((e) => `${(e.field ?? []).join('.')}: ${e.message}`).join('; ')}`,
      { userErrors: errs },
    );
  }
  return payload;
}

/** Milliseconds to wait before retrying a THROTTLED request. */
export function throttleDelayMs(cost, fallbackMs = 1000) {
  const s = cost?.throttleStatus;
  if (!s || !cost.requestedQueryCost) return fallbackMs;
  const deficit = cost.requestedQueryCost - s.currentlyAvailable;
  if (deficit <= 0 || !s.restoreRate) return fallbackMs;
  return Math.ceil((deficit / s.restoreRate) * 1000) + 100;
}

const isThrottled = (body) => body?.errors?.some?.((e) => e?.extensions?.code === 'THROTTLED');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * @param {{
 *   storeDomain: string, accessToken: string, apiVersion?: string,
 *   fetchImpl?: typeof fetch, logger?: ReturnType<typeof createLogger>,
 *   env?: Record<string, string|undefined>, maxRetries?: number, sleepImpl?: (ms:number)=>Promise<void>
 * }} opts
 */
export function createAdminClient(opts) {
  const {
    storeDomain, accessToken, apiVersion = opts.env?.SHOPIFY_API_VERSION || DEFAULT_API_VERSION,
    fetchImpl = globalThis.fetch, logger = createLogger(), env = process.env,
    maxRetries = 3, sleepImpl = sleep,
  } = opts;
  if (!storeDomain || !/^[a-z0-9][a-z0-9-]*\.myshopify\.com$/i.test(storeDomain)) {
    throw new Error(`storeDomain must be a *.myshopify.com domain, got "${storeDomain}"`);
  }
  if (!/^\d{4}-(01|04|07|10)$/.test(apiVersion)) throw new Error(`Invalid API version ${apiVersion}`);
  const endpoint = `https://${storeDomain}/admin/api/${apiVersion}/graphql.json`;

  async function request(doc, variables, { isMutation, operation, changeId }) {
    for (let attempt = 0; ; attempt++) {
      let res;
      try {
        res = await fetchImpl(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Shopify-Access-Token': accessToken },
          body: JSON.stringify({ query: doc, variables }),
        });
      } catch (err) {
        logger.error('shopify.request', { operation, resource: storeDomain, result: 'network_error', errors: err, changeId });
        if (isMutation || attempt >= 1) throw err; // reads: one retry; writes: none
        await sleepImpl(1000);
        continue;
      }
      const body = await res.json().catch(() => null);
      if (res.status === 429 || isThrottled(body)) {
        if (attempt >= maxRetries) {
          throw new ShopifyGraphQLError(`${operation} throttled after ${attempt + 1} attempts`, { status: res.status });
        }
        const delay = throttleDelayMs(body?.extensions?.cost, 1000 * 2 ** attempt);
        logger.warn('shopify.throttled', { operation, attempt, delayMs: delay });
        await sleepImpl(delay);
        continue;
      }
      if (!res.ok) {
        logger.error('shopify.request', { operation, resource: storeDomain, result: `http_${res.status}`, changeId });
        if (!isMutation && res.status >= 500 && attempt < 1) { await sleepImpl(1000); continue; }
        throw new ShopifyGraphQLError(`${operation} failed: HTTP ${res.status}`, { status: res.status, errors: body?.errors });
      }
      if (body?.errors?.length) {
        logger.error('shopify.request', { operation, result: 'graphql_errors', errors: body.errors, changeId });
        throw new ShopifyGraphQLError(`${operation} GraphQL errors: ${body.errors.map((e) => e.message).join('; ')}`, { errors: body.errors });
      }
      logger.info('shopify.request', {
        operation, resource: storeDomain, result: 'ok', changeId,
        cost: body?.extensions?.cost?.actualQueryCost,
      });
      return body.data;
    }
  }

  return {
    endpoint,
    apiVersion,
    /** Read-only query. */
    query(doc, variables = {}, { operation = 'query' } = {}) {
      if (/^\s*mutation\b/m.test(doc.replace(/#.*$/gm, ''))) {
        throw new Error('Use mutate() for mutations');
      }
      return request(doc, variables, { isMutation: false, operation });
    },
    /**
     * Write operation. Returns { dryRun: true, ... } unless live mode is enabled for this store.
     * @param {{ operation: string, changeId?: string }} meta changeId = EXP-### / DEC-### / CHG-###
     */
    async mutate(doc, variables = {}, { operation = 'mutation', changeId } = {}) {
      const mode = resolveWriteMode(storeDomain, env);
      if (!mode.live) {
        logger.info('shopify.mutate', { operation, resource: storeDomain, result: 'dry_run', reason: mode.reason, changeId });
        return { dryRun: true, reason: mode.reason, operation, variables };
      }
      return request(doc, variables, { isMutation: true, operation, changeId });
    },
  };
}
