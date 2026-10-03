// Structured JSON logger with recursive redaction of secrets and customer PII.
import { appendFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

const SENSITIVE_KEY = /token|secret|password|passwd|authorization|api[-_]?key|private|cookie|session|email|phone|address|first_?name|last_?name|card|iban|ssn/i;
const SENSITIVE_VALUE = [
  /shp(at|ss|ca|pa|ua)_[A-Za-z0-9]{16,}/g, // Shopify tokens
  /sk-ant-[A-Za-z0-9_-]{10,}/g,           // Anthropic keys
  /gh[pousr]_[A-Za-z0-9]{20,}/g,          // GitHub tokens
  /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, // emails
];
const LEVELS = { debug: 10, info: 20, warn: 30, error: 40 };

/** Deep-copy `value` with sensitive keys/values replaced by "[REDACTED]". */
export function redact(value, depth = 0) {
  if (depth > 8) return '[DEPTH]';
  if (typeof value === 'string') {
    return SENSITIVE_VALUE.reduce((s, re) => s.replace(re, '[REDACTED]'), value);
  }
  if (Array.isArray(value)) return value.map((v) => redact(v, depth + 1));
  if (value instanceof Error) return { name: value.name, message: redact(value.message, depth + 1) };
  if (value && typeof value === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(value)) {
      out[k] = SENSITIVE_KEY.test(k) ? '[REDACTED]' : redact(v, depth + 1);
    }
    return out;
  }
  return value;
}

/**
 * @param {{ level?: string, file?: string, sink?: (line: string) => void }} [opts]
 */
export function createLogger(opts = {}) {
  const min = LEVELS[opts.level ?? process.env.LOG_LEVEL ?? 'info'] ?? LEVELS.info;
  const sink = opts.sink ?? ((line) => process.stderr.write(line + '\n'));
  if (opts.file) mkdirSync(dirname(opts.file), { recursive: true });

  /** @param {string} level @param {string} operation @param {Record<string, unknown>} [fields] */
  function log(level, operation, fields = {}) {
    if ((LEVELS[level] ?? 0) < min) return;
    const entry = redact({ timestamp: new Date().toISOString(), level, operation, ...fields });
    const line = JSON.stringify(entry);
    sink(line);
    if (opts.file) appendFileSync(opts.file, line + '\n');
  }
  return {
    debug: (op, f) => log('debug', op, f),
    info: (op, f) => log('info', op, f),
    warn: (op, f) => log('warn', op, f),
    error: (op, f) => log('error', op, f),
  };
}
