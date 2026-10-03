# Product research workflow (Stage 1 → Stage 2)

Goal: choose ONE product thesis worth a small, controlled validation — or conclude none is good enough.

## Pipeline

```
1 Seed        → 2 Screen        → 3 Dossier        → 4 Score          → 5 Verify        → 6 Shortlist (3) → 7 Deep dive  → 8 Thesis + APR
problem spaces   knockouts check   template per       scoring.mjs        orchestrator      competitors,       unit econ,     sample-order
(≥10 cands)      (cheap, fast)     candidate          RANKING.md         spot-checks       suppliers          risks          approval
```

### 1. Seed (problem-first, not product-first)
Start from **customer problems** observable in public evidence: marketplace review complaints,
forum/community threads, search autocomplete, "how to" questions, category bestseller lists.
Generate ≥10 candidates across ≥4 problem spaces. Record why each was seeded.

### 2. Screen — knockouts (`lib/research/scoring.mjs#KNOCKOUTS`)
Reject immediately: regulated categories we cannot verify (ingestibles, cosmetics with claims, medical,
child-safety-critical, electrical without verifiable certification), IP/knock-off risk, unsafe-to-ship
(lithium batteries, aerosols, flammables) without verified compliant fulfillment, platform-prohibited,
or margin below floor — mid case product gross margin < 60% **or** contribution after shipping + payment fees < 40% (DEC-008).

### 3. Dossier
Copy `templates/candidate.md` to `candidates/<ID>-<slug>.md` (IDs `C-001`…). Every claim tagged
FACT / INFERENCE / ASSUMPTION; sources logged in `sources/SOURCES.md`.

### 4. Score
Add the candidate to `candidates/candidates.json` (1–5 per factor, `null` = UNKNOWN, knockouts) and run
`npm run score:candidates` → `candidates/RANKING.md`. Unknowns score 2/5 and reduce confidence.

### 5. Verify (orchestrator)
For every candidate scoring ≥ 55%: re-open ≥ 2 key sources, recompute price/margin arithmetic, and
challenge the weakest factor. Record corrections in the dossier.

### 6. Shortlist
Top 3 SHORTLIST/HOLD candidates with confidence ≥ 60% proceed. If fewer than 3 qualify, seed more.

### 7. Deep dive (per shortlisted candidate)
- Competitive teardown (3–5 stores/listings) → `research/competitors/<ID>.md`
- Supplier desk research (≥ 3 options) → `supplier-research/<ID>.md`
- Unit economics LOW/HIGH scenario using `unitContribution` — show inputs and arithmetic.
- Creative angle inventory (≥ 5 genuinely different angles).
- Risk list → `RISK_REGISTER.md`.

### 8. Thesis + approval
`research/THESIS-<ID>.md`: customer, problem, product, why now, why us/differentiation, evidence,
unit economics ranges, risks, kill criteria, Stage 2 plan (samples: which suppliers, quantity, cost).
Sample purchase → `APPROVAL_QUEUE.md`.

## Evidence quality ladder (prefer higher)
1. First-party observation (live marketplace listings, prices, review text paraphrased, brand sites) with URL + date
2. Official/government statistics, platform-published trend data, regulator pages
3. Published research with methodology
4. Reputable journalism
5. Blogs, SEO listicles, "winning product" lists — **weak signal only, never sole support**

## Hard rules
- No invented numbers. Search volume, sales volume, market size → `UNKNOWN` unless a source states it (and say whose estimate it is).
- Prices observed are "observed on <site> on <date>", not "market price".
- Ranges with stated assumptions over point estimates.
