#!/usr/bin/env node
// Validate every lib/shopify/operations/*.graphql against Shopify's Admin schema using the
// official Shopify Dev MCP server (stdio). Exit 1 if any operation is invalid.
// Usage: node scripts/validate-graphql.mjs [--version 2026-07]
import { spawn } from 'node:child_process';
import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const MCP_PACKAGE = '@shopify/dev-mcp@1.16.0'; // pinned; bump deliberately (see DECISIONS.md DEC-004)
const opsDir = fileURLToPath(new URL('../lib/shopify/operations/', import.meta.url));
const cfg = JSON.parse(readFileSync(new URL('../config/shopify.json', import.meta.url), 'utf8'));
const vIdx = process.argv.indexOf('--version');
const version = vIdx > 0 ? process.argv[vIdx + 1] : cfg.adminApiVersion;

const files = readdirSync(opsDir).filter((f) => f.endsWith('.graphql')).sort();
if (!files.length) { console.log('No operations to validate.'); process.exit(0); }

const child = spawn('npx', ['-y', MCP_PACKAGE], {
  env: { ...process.env, OPT_OUT_INSTRUMENTATION: 'true' },
  stdio: ['pipe', 'pipe', 'inherit'],
});
let buf = '';
const pending = new Map();
child.stdout.on('data', (d) => {
  buf += d;
  let i;
  while ((i = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, i); buf = buf.slice(i + 1);
    try { const m = JSON.parse(line); pending.get(m.id)?.(m); } catch { /* non-JSON log line */ }
  }
});
let nextId = 1;
function rpc(method, params) {
  const id = nextId++;
  child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error(`MCP timeout on ${method}`)), 120_000);
    pending.set(id, (m) => { clearTimeout(t); m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result); });
  });
}
const text = (r) => (r?.content ?? []).map((c) => c.text ?? '').join('\n');

try {
  await rpc('initialize', { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'commerce-os', version: '0.1.0' } });
  child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
  const learn = text(await rpc('tools/call', { name: 'learn_shopify_api', arguments: { api: 'admin', version } }));
  const conversationId = learn.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/)?.[0];
  if (!conversationId) throw new Error('Could not obtain conversationId from learn_shopify_api');

  let failed = 0;
  for (const f of files) {
    const content = readFileSync(opsDir + f, 'utf8');
    const out = text(await rpc('tools/call', {
      name: 'validate',
      arguments: { conversationId, api: 'admin', version, code: [{ content }] },
    }));
    const ok = /\bVALID\b|✅/.test(out) && !/\bINVALID\b|❌/.test(out);
    if (!ok) failed++;
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${f}`);
    if (!ok || process.argv.includes('--verbose')) console.log(out.split('\n').map((l) => '      ' + l).join('\n'));
  }
  console.log(`\n${files.length - failed}/${files.length} operations valid against Admin API ${version}`);
  process.exitCode = failed ? 1 : 0;
} catch (err) {
  console.error('GraphQL validation could not run:', err.message);
  process.exitCode = 2;
} finally {
  child.kill();
}
