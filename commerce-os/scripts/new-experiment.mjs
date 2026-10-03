#!/usr/bin/env node
// Allocate the next EXP-### id, append a registry row to EXPERIMENTS.md and create experiments/EXP-###.md
import { readFileSync, writeFileSync, existsSync } from 'node:fs';

const title = process.argv.slice(2).join(' ').trim();
if (!title) { console.error('Usage: npm run new:experiment -- "<title>"'); process.exit(1); }
const regPath = new URL('../EXPERIMENTS.md', import.meta.url);
const reg = readFileSync(regPath, 'utf8');
const ids = [...reg.matchAll(/EXP-(\d{3,})/g)].map((m) => Number(m[1]));
const id = `EXP-${String((ids.length ? Math.max(...ids) : 0) + 1).padStart(3, '0')}`;
const today = new Date().toISOString().slice(0, 10);
const detail = new URL(`../experiments/${id}.md`, import.meta.url);
if (existsSync(detail)) { console.error(`${id} already exists`); process.exit(1); }
const tpl = readFileSync(new URL('../experiments/TEMPLATE.md', import.meta.url), 'utf8');
writeFileSync(detail, tpl.replaceAll('EXP-XXX', id).replace('<title>', title).replace('YYYY-MM-DD', today));
const row = `| [${id}](experiments/${id}.md) | ${today} | ${title} | DRAFT | — | — |`;
writeFileSync(regPath, reg.replace('<!-- EXPERIMENTS:ROWS -->', `<!-- EXPERIMENTS:ROWS -->\n${row}`));
console.log(`Created ${id}: experiments/${id}.md`);
