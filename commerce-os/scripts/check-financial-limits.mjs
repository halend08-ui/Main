#!/usr/bin/env node
// Validate config/financial-limits.json shape. Values are owner-controlled; this does not judge them,
// it only guarantees the guard can read them and nothing is negative / missing / non-numeric.
import { loadLimits, LIMIT_KINDS, LIMITS_PATH } from '../lib/core/guards.mjs';

const limits = loadLimits();
const errors = [];
if (typeof limits.currency !== 'string' || !/^[A-Z]{3}$/.test(limits.currency)) errors.push('currency must be ISO 4217 code');
for (const k of LIMIT_KINDS) {
  if (typeof limits[k] !== 'number' || !Number.isFinite(limits[k]) || limits[k] < 0) errors.push(`${k} must be a number >= 0`);
}
const unknown = Object.keys(limits).filter((k) => k.startsWith('MAX_') && !LIMIT_KINDS.includes(k));
if (unknown.length) errors.push(`unknown limit keys: ${unknown.join(', ')}`);
if (errors.length) { console.error(`${LIMITS_PATH} invalid:\n  ` + errors.join('\n  ')); process.exit(1); }
const nonZero = LIMIT_KINDS.filter((k) => limits[k] > 0);
console.log(`Financial limits valid. Non-zero limits: ${nonZero.length ? nonZero.map((k) => `${k}=${limits[k]}`).join(', ') : 'none (all spending requires approval)'}`);
