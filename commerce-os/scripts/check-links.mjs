#!/usr/bin/env node
// Check that relative links in commerce-os markdown files point to existing files.
import { readdirSync, readFileSync, existsSync, statSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const base = fileURLToPath(new URL('..', import.meta.url));
const skip = new Set(['node_modules', 'logs', 'archive', '.git']);
const mdFiles = [];
(function walk(d) {
  for (const e of readdirSync(d)) {
    if (skip.has(e)) continue;
    const p = join(d, e);
    if (statSync(p).isDirectory()) walk(p); else if (p.endsWith('.md')) mdFiles.push(p);
  }
})(base);
const broken = [];
for (const f of mdFiles) {
  const text = readFileSync(f, 'utf8').replace(/```[\s\S]*?```/g, '');
  for (const [, target] of text.matchAll(/\]\(([^)\s]+)\)/g)) {
    if (/^(https?:|mailto:|#)/.test(target)) continue;
    const path = target.split('#')[0];
    const abs = path.startsWith('/') ? resolve(base, '..', '.' + path) : resolve(dirname(f), path);
    if (!existsSync(abs)) broken.push(`${f.slice(base.length)} -> ${target}`);
  }
}
if (broken.length) { console.error(`Broken links (${broken.length}):\n  ` + broken.join('\n  ')); process.exit(1); }
console.log(`Link check passed (${mdFiles.length} markdown files).`);
