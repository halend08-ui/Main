import { test } from 'node:test';
import assert from 'node:assert/strict';
import { scoreCandidate, rankCandidates, FACTORS } from '../lib/research/scoring.mjs';

const all = (v) => Object.fromEntries(Object.keys(FACTORS).map((k) => [k, v]));

test('perfect candidate shortlists with full confidence', () => {
  const r = scoreCandidate({ id: 'C1', name: 'a', scores: all(5) });
  assert.equal(r.pct, 1); assert.equal(r.confidence, 1); assert.equal(r.verdict, 'SHORTLIST');
});
test('unknowns are pessimistic and lower confidence', () => {
  const r = scoreCandidate({ id: 'C2', name: 'b', scores: all(null) });
  assert.equal(r.confidence, 0); assert.equal(r.verdict, 'NEEDS_RESEARCH');
  assert.equal(r.pct, 2 / 5);
});
test('a knockout rejects even a perfect score', () => {
  const r = scoreCandidate({ id: 'C3', name: 'c', scores: all(5), knockouts: { ipRisk: true } });
  assert.equal(r.verdict, 'REJECT'); assert.deepEqual(r.knockedOutBy, ['ipRisk']);
});
test('invalid scores and knockouts throw', () => {
  assert.throws(() => scoreCandidate({ id: 'x', name: 'x', scores: { ...all(3), customerProblem: 6 } }));
  assert.throws(() => scoreCandidate({ id: 'x', name: 'x', scores: all(3), knockouts: { vibes: true } }));
});
test('ranking orders by verdict then score', () => {
  const r = rankCandidates([
    { id: 'low', name: 'l', scores: all(2) },
    { id: 'top', name: 't', scores: all(5) },
    { id: 'mid', name: 'm', scores: all(3) },
  ]);
  assert.deepEqual(r.map((x) => x.id), ['top', 'mid', 'low']);
});
