// Financial and production-write guards. See CLAUDE.md §4–§6.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export const LIMITS_PATH = fileURLToPath(new URL('../../config/financial-limits.json', import.meta.url));
export const LIMIT_KINDS = [
  'MAX_DAILY_AD_SPEND',
  'MAX_CAMPAIGN_TEST_SPEND',
  'MAX_APP_PURCHASE',
  'MAX_REFUND_WITHOUT_APPROVAL',
  'MAX_SUPPLIER_ORDER_WITHOUT_APPROVAL',
];

export class ApprovalRequiredError extends Error {
  constructor(message, details = {}) {
    super(message);
    this.name = 'ApprovalRequiredError';
    this.details = details;
  }
}

export function loadLimits(path = LIMITS_PATH) {
  return JSON.parse(readFileSync(path, 'utf8'));
}

/**
 * Throws ApprovalRequiredError unless 0 < amount <= configured limit.
 * A limit of 0 (the default) means every amount needs owner approval.
 * @param {string} kind one of LIMIT_KINDS
 * @param {number} amount in limits.currency
 */
export function assertWithinLimit(kind, amount, limits = loadLimits()) {
  if (!LIMIT_KINDS.includes(kind)) throw new Error(`Unknown limit kind: ${kind}`);
  if (typeof amount !== 'number' || !Number.isFinite(amount) || amount < 0) {
    throw new Error(`Invalid amount for ${kind}: ${amount}`);
  }
  const limit = limits[kind];
  if (typeof limit !== 'number' || limit <= 0 || amount > limit) {
    throw new ApprovalRequiredError(
      `${kind}: ${amount} ${limits.currency} exceeds limit ${limit} — owner approval required (add to APPROVAL_QUEUE.md)`,
      { kind, amount, limit, currency: limits.currency },
    );
  }
  return true;
}

/**
 * Decide whether a mutation may hit a real store.
 * @returns {{ live: boolean, reason: string }}
 */
export function resolveWriteMode(storeDomain, env = process.env) {
  const mode = (env.COMMERCE_OS_WRITE_MODE ?? 'dry-run').toLowerCase();
  if (mode !== 'live') return { live: false, reason: `write mode is "${mode}"` };
  const allowed = (env.SHOPIFY_ALLOWED_STORES ?? '')
    .split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (!storeDomain || !allowed.includes(storeDomain.toLowerCase())) {
    return { live: false, reason: `store "${storeDomain}" not in SHOPIFY_ALLOWED_STORES` };
  }
  return { live: true, reason: 'live mode and store allow-listed' };
}
