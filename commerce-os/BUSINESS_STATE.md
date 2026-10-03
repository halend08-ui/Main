# BUSINESS_STATE

_Owner: orchestrator · Updated: 2026-10-03 (after Stage 1 pool) · Read after SYSTEM_STATUS.md_

## Current stage

**STAGE 1 — Research validation** (no product, brand, store, or spend yet).

| Stage | Status |
|---|---|
| 0. Operating system build | ✅ complete (see SYSTEM_STATUS.md) |
| 1. Research validation | 🔄 provisional shortlist (DEC-010): C-005, C-012, C-008; alt C-010 |
| 2. Supplier / product validation | ⏳ blocked on Stage 1 shortlist; samples need approval |
| 3. Store / offer prototype | ⏳ needs Shopify store/dev store auth (APR-002) |
| 4. Organic / creative validation | ⏳ |
| 5. Controlled paid test | ⏳ requires approved budget (all limits = 0) |
| 6. Analyze · 7. Iterate or discontinue | ⏳ |

## Business facts (verified only)

- Store: **none connected** (UNKNOWN whether owner has a Shopify account / Partner account).
- Products: none. Brand: none. Revenue: 0 (no store). Ad spend: 0.
- Target market/geography: **UNKNOWN — assumed US-first English-language** (ASSUMPTION A-001, see DECISIONS DEC-006).

## Task queue (orchestrator-maintained, top = next)

| # | Task | Owner agent | Status | Blocked by |
|---|---|---|---|---|
| T-01 | Build candidate pool (12 candidates, 105 sources) | market-intelligence ×3 | ✅ done | — |
| T-02 | Score, spot-check, shortlist 3 (RANKING.md, DEC-008..010) | orchestrator | ✅ done (provisional) | — |
| V-R1 | Re-open key sources for shortlist (prices, complaints, supplier listings) | orchestrator | pending | page access (APR-004) |
| T-03 | Competitive teardown of shortlist (3–5 competitors each) | competitive-intelligence | pending | T-02 |
| T-04 | Supplier desk research for shortlist (no orders) | supplier-research | pending | T-02 |
| T-05 | Unit-economics ranges for shortlist (`lib/analytics/metrics.mjs`) | product-analyst | pending | T-04 |
| T-06 | Recommend 1 product thesis + sample-order approval request | orchestrator | pending | T-03..T-05 |
| T-07 | Brand name candidates + conflict screen | brand-strategist | pending | T-06 |
| T-08 | Theme scaffold on dev theme (Horizon / Skeleton evaluation) | shopify-engineer | pending | APR-002 |
| T-09 | Policy-page workflow + placeholders (owner/legal confirmation) | shopify-engineer + copywriter | pending | T-08 |
| T-11 | Add evidence-quality weighting to scoring.mjs (DEC-009) | product-analyst | pending | — |
| T-10 | Analytics/tracking plan implementation | analytics | pending | T-08 |

## Active blockers

See `APPROVAL_QUEUE.md`. Highest impact: APR-001 (repo is public), APR-002 (Shopify auth).
