#!/usr/bin/env node
// Score research/candidates/candidates.json and write research/candidates/RANKING.md
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { rankCandidates, FACTORS } from '../lib/research/scoring.mjs';

const src = new URL('../research/candidates/candidates.json', import.meta.url);
const out = new URL('../research/candidates/RANKING.md', import.meta.url);
if (!existsSync(src)) { console.log('No candidates.json yet.'); process.exit(0); }
const { candidates } = JSON.parse(readFileSync(src, 'utf8'));
const ranked = rankCandidates(candidates);
const pct = (x) => `${Math.round(x * 100)}%`;
const lines = [
  '# Candidate ranking (generated — do not edit; run `npm run score:candidates`)',
  '',
  `Generated: ${new Date().toISOString().slice(0, 10)} · Model: lib/research/scoring.mjs · Factors: ${Object.keys(FACTORS).length}`,
  '',
  '| Rank | ID | Candidate | Score | Confidence | Verdict | Unknowns | Knockouts |',
  '|---|---|---|---|---|---|---|---|',
  ...ranked.map((r, i) => `| ${i + 1} | ${r.id} | ${r.name} | ${pct(r.pct)} | ${pct(r.confidence)} | **${r.verdict}** | ${r.unknowns.join(', ') || '—'} | ${r.knockedOutBy.join(', ') || '—'} |`),
  '',
  'Score = weighted factor total / max. Unknown factors are scored pessimistically (2/5).',
  'Confidence = share of factor weight backed by evidence. < 60% ⇒ NEEDS_RESEARCH.',
];
writeFileSync(out, lines.join('\n') + '\n');
console.log(lines.join('\n'));
