# C-004 · Safety-first adjustable mandoline kit (spring-advance guard + cut-resistant gloves)

| Field | Value |
|---|---|
| Status | SCREENED (scores proposed, not yet in candidates.json) |
| Problem space | Non-electrical kitchen/cooking tools |
| Geography | US (ASSUMPTION A-001) |
| Researched | 2026-10-03 by market-intelligence + product-analyst subagent · Verified by orchestrator: no |

> **Evidence caveat:** WebFetch was blocked (cambom.org and all other domains tried). FACTs below come from search-engine
> result summaries of the cited URLs on 2026-10-03, not from opening the pages. Injury statistics in particular need
> re-verification (see Unknowns).

## Customer
- [INFERENCE] Home cooks who want thin, even slices (potatoes, cucumbers, onions) faster than with a knife but are scared of — or have already experienced — a mandoline cut; includes people who own a mandoline and stopped using it.

## Problem
- [FACT] A blog analysis claiming to use CPSC NEISS data estimates ~16,800 US ER visits/year from mandolines (2023–2025 average), with 35% of mandoline injuries being avulsions vs 3% for knives; it notes mandolines have no dedicated NEISS code so counts come from narratives and are a lower bound [S-A25]. **This is that blog's estimate, not CPSC's published figure.** Another search summary cited "nearly 40,000" (source unclear) — figures conflict; treat magnitude as UNKNOWN.
- [FACT] A peer-reviewed case report (PMC) describes a partial fingertip amputation from a mandoline slicer [S-A26].
- [FACT] Test-kitchen reviews: most hand guards are "a pain to use", prongs fail to grip food larger than a lemon/small onion; some blades are dull out of the box; hard to push the last part of the food (waste); food trapped in blade mechanism makes cleaning tricky [S-A28].
- [FACT] Review roundups conclude guards are weak on most models and recommend cut-resistant gloves [S-A27].
- Workarounds: cut-resistant gloves sold separately ($5.41–$28.38 at Walmart) [S-A29]; finger-guard holders sold as separate accessories [S-A29].

## Product
Adjustable-thickness mandoline (V- or straight blade, julienne insert) **sold as a safety kit**: food holder that grips large produce and advances it to the end, a pair of cut-resistant gloves with a verifiable cut rating, blade cover/storage case, and cleaning brush.
- Good version: sharp hardened stainless blade, stable non-slip base, guard that grips onions/potatoes, blades that lock and remove without touching the edge, dishwasher-safe body.
- Bad version: dull blade (forces pressure → slips), shallow prong guard, flimsy body, unverified glove ratings [S-A27][S-A28].

## Market evidence
- [FACT] Review-roundup prices: OXO V-Blade $39.99 (named best for safety), Benriner $44.99, Mueller Austria $31.49 [S-A27].
- [FACT] Walmart: stainless adjustable mandoline incl. cut-resistant gloves $28.99; GUQDZOF with gloves $22.98; OXO handheld $18.99 (also $18.99 at Target) [S-A29].
- [FACT] America's Test Kitchen, Reviewed, Food Network and others publish dedicated mandoline tests [S-A28]. [INFERENCE] Ongoing consumer interest and an established category.
- Search / trend signals: **UNKNOWN**.

## Competitive environment
- [FACT] Brands: OXO, Benriner, Mueller Austria, PL8, GoodCook, plus unbranded kits on Walmart [S-A27][S-A28][S-A29].
- [INFERENCE] Competitive and brand-led at the top (OXO/Benriner), price-led at the bottom. "Gloves included" is already common [S-A29], so gloves alone are not a differentiator.

## Price & cost (ranges, with sources)
| Item | Low | High | Basis |
|---|---|---|---|
| Observed retail price | $18.99 | $44.99 | [S-A27][S-A29], via search 2026-10-03 |
| Our assumed sell price | $29.99 | $39.99 | ASSUMPTION — between Walmart kits and OXO V-Blade |
| Unit cost (supplier, FOB) | $3.63 | $12.00 | SUPPLIER-LISTED, unverified: $3.63–5.79 (MOQ 1 set), $6.50–7.50, $6.89–7.29 with gloves (MOQ 12), $8.25–12.00 (MOQ 3), $8.89–9.59 (MOQ 60) [S-A30] |
| Landed unit cost | $4.72 | $24.00 | ASSUMPTION A-LC: FOB × 1.3–2.0 |
| Shipping cost to customer | $7.61 | $11.00 | FACT: USPS GA commercial 1 lb $7.61–8.74 (zones 1–5) [S-A09]; ASSUMPTION packed 1.5–3 lb → up to ~$11 |
| Payment fees | $1.17 | $1.46 | ASSUMPTION 2.9% + $0.30 |
| Est. gross margin before ads | 20% | 88% | see arithmetic |

Arithmetic (product GM = (price − landed)/price):
- Best: ($39.99 − $4.72)/$39.99 = 88.2%
- Worst: ($29.99 − $24.00)/$29.99 = 20.0%
- Mid: FOB $7.10 (gloves-included listing) × 1.6 = $11.36; ($34.99 − $11.36)/$34.99 = 67.5%

Contribution after free shipping + fees:
- Best: $39.99 − $4.72 − $7.61 − $1.46 = $26.20 (65.5%)
- Mid: $34.99 − $11.36 − $9.50 − $1.31 = $12.82 (36.6%)
- Worst: $29.99 − $24.00 − $11.00 − $1.17 = −$6.18

## Shipping concerns
- [INFERENCE] Light and compact; no batteries/liquids. Sharp blades need secure packaging (blade guard) to protect warehouse staff and customers on unboxing. Not hazmat.

## Return / breakage concerns
- [INFERENCE] Returns driven by dull blades, guard not gripping, and injury fear; plastic bodies can crack in transit. No sizing issue.

## Creative angles (genuinely different)
1. Speed: knife vs mandoline on a potato (timed).
2. Safety-to-the-last-slice: guard advancing food to the end, zero fingertip exposure.
3. "I gave up on my mandoline" — re-entry for lapsed users [S-A27][S-A28].
4. Recipe-led: gratin, chips, slaw — consistent thickness.
5. Glove proof test (only with a verified cut-rating report).
6. Cleanup: blades out without touching the edge.

## Differentiation opportunity
- [INFERENCE] A guard that genuinely grips large produce + verified-rating gloves + blade storage, positioned on safety with evidence. Real gap per test-kitchen complaints [S-A28], but OXO already owns "safest" positioning [S-A27].

## Risks (regulatory, IP, quality, platform policy)
- Regulatory / claims: [INFERENCE] food-contact materials need FDA-compliant documentation (UNKNOWN). Glove cut-level claims (e.g., ANSI/ISEA cut level) require test reports — unverified supplier claims would be a claims risk. Safety marketing ("cut-proof") must not overstate.
- Liability: [INFERENCE] documented severe injury mechanism [S-A25][S-A26] → product-liability exposure; insurance UNKNOWN/not priced.
- IP: [INFERENCE] specific guard and V-blade mechanisms (e.g., OXO) may be patented — not searched (UNKNOWN).
- Platform: [FACT] Meta's ad standards prohibit weapons ads but exempt culinary/kitchen knives [S-A31]; [INFERENCE] a mandoline is a culinary tool and should be allowed, but blade imagery may trip automated review (reported enforcement inconsistency for knife sellers per same search summary).
- Quality: dull/variable blades are a documented failure mode [S-A28].

## Unknowns (what would change the verdict)
- True mandoline injury magnitude from CPSC NEISS directly (UNKNOWN; conflicting secondary figures).
- Patent landscape for guard mechanisms (UNKNOWN).
- Supplier food-contact + glove test documentation (UNKNOWN).
- Product liability insurance cost (UNKNOWN).
- Search demand (UNKNOWN).

## Knockout check
| Knockout | Triggered? | Note |
|---|---|---|
| regulatedCategory | No | Kitchen utensil; not medical, not child-safety-critical. Claims/food-contact docs required |
| ipRisk | No — open | Mechanism patents not searched |
| marginBelowFloor | No | Mid-case 67.5% product GM |
| unsafeToShip | No | Sharp, but not hazmat; needs blade-safe packaging |
| platformProhibited | No (INFERENCE) | Culinary blades exempt under Meta weapons policy [S-A31]; enforcement risk noted |
