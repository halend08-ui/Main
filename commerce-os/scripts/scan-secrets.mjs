#!/usr/bin/env node
// Scan git-tracked (and staged) files for likely secrets. Exit 1 on findings.
import { execSync } from 'node:child_process';
import { readFileSync, statSync } from 'node:fs';

const PATTERNS = [
  ['Shopify access token', /\bshp(at|ca|pa|ua)_[a-fA-F0-9]{32}\b/],
  ['Shopify shared secret', /\bshpss_[a-fA-F0-9]{32}\b/],
  ['Anthropic API key', /\bsk-ant-[A-Za-z0-9_-]{20,}/],
  ['OpenAI-style key', /\bsk-[A-Za-z0-9]{32,}\b/],
  ['GitHub token', /\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}/],
  ['AWS access key', /\bAKIA[0-9A-Z]{16}\b/],
  ['Google API key', /\bAIza[0-9A-Za-z_-]{35}\b/],
  ['Stripe live key', /\b(sk|rk)_live_[0-9a-zA-Z]{20,}/],
  ['Slack token', /\bxox[baprs]-[0-9A-Za-z-]{10,}/],
  ['Private key block', /-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----/],
  ['Meta access token', /\bEAA[A-Za-z0-9]{100,}/],
];
const SELF = 'scan-secrets.mjs';

const root = execSync('git rev-parse --show-toplevel').toString().trim();
const files = [...new Set(
  execSync('git ls-files -co --exclude-standard', { cwd: root }).toString().split('\n').filter(Boolean),
)].filter((f) => !f.endsWith(SELF) && !/node_modules\//.test(f));

const findings = [];
for (const f of files) {
  const p = `${root}/${f}`;
  let st; try { st = statSync(p); } catch { continue; }
  if (!st.isFile() || st.size > 2_000_000) continue;
  const text = readFileSync(p, 'utf8');
  if (text.includes('\u0000')) continue; // binary
  text.split('\n').forEach((line, i) => {
    for (const [name, re] of PATTERNS) if (re.test(line)) findings.push(`${f}:${i + 1}  ${name}`);
  });
  if (/(^|\/)\.env\.example$/.test(f)) {
    const SAFE = /^(dry-run|live|info|debug|warn|error|true|false|)$/;
    text.split('\n').forEach((line, i) => {
      const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*([^#\s]*)/);
      if (m && !SAFE.test(m[2])) findings.push(`${f}:${i + 1}  .env.example must not contain values (${m[1]})`);
    });
  }
  if (/(^|\/)\.env(\.[^/]*)?$/.test(f) && !f.endsWith('.env.example')) findings.push(`${f}  .env file is tracked by git`);
}
if (findings.length) {
  console.error(`Secret scan FAILED (${findings.length}):\n` + findings.map((x) => '  ' + x).join('\n'));
  process.exit(1);
}
console.log(`Secret scan passed (${files.length} files).`);
