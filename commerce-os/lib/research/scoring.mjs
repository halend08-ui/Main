// Product-candidate scoring model. See research/PRODUCT_RESEARCH_WORKFLOW.md.
// Every factor is scored 1–5 where 5 is ALWAYS better for us (risk factors are pre-inverted:
// e.g. returnRisk 5 = very low return risk). null = UNKNOWN (counts against confidence, and is
// scored pessimistically as 2 so unknowns never make a candidate look better).

export const FACTORS = {
  customerProblem:      { weight: 3, label: 'Clear, painful, frequent customer problem' },
  demandEvidence:       { weight: 3, label: 'Evidence of demand (searches, communities, marketplaces)' },
  grossMarginPotential: { weight: 3, label: 'Gross margin potential before ads' },
  differentiation:      { weight: 2, label: 'Realistic differentiation opportunity' },
  competitiveDensity:   { weight: 2, label: 'Competition (5 = not saturated)' },
  demonstrability:      { weight: 2, label: 'Value visible in a short video/image' },
  creativePotential:    { weight: 2, label: 'Number of genuinely distinct angles' },
  shippingSimplicity:   { weight: 2, label: 'Small, light, non-fragile, no batteries/liquids' },
  returnRisk:           { weight: 2, label: 'Return risk (5 = low; sizing/fit issues lower)' },
  breakageRisk:         { weight: 1, label: 'Breakage risk (5 = low)' },
  qualityRisk:          { weight: 2, label: 'Product quality consistency risk (5 = low)' },
  supplierReliability:  { weight: 2, label: 'Verified supplier options' },
  repeatPurchase:       { weight: 1, label: 'Consumable / repeat purchase potential' },
  bundlePotential:      { weight: 1, label: 'Natural bundles / AOV expansion' },
  seasonalityStability: { weight: 1, label: 'Year-round demand (5 = evergreen)' },
  regulatoryRisk:       { weight: 3, label: 'Regulatory/claims risk (5 = minimal)' },
};

const UNKNOWN_SCORE = 2;

/** Hard knockouts: any true -> candidate is rejected regardless of score. */
export const KNOCKOUTS = {
  regulatedCategory: 'Requires certifications/approvals we cannot verify (ingestibles, medical, child-safety-critical, weapons, etc.)',
  ipRisk: 'Likely patent/trademark/design infringement (branded knock-offs)',
  marginBelowFloor: 'Estimated gross margin before ads below floor at realistic price',
  unsafeToShip: 'Hazmat / lithium batteries / aerosols / flammables without a verified compliant fulfillment path',
  platformProhibited: 'Prohibited or restricted under Shopify / ad platform policies',
};

/**
 * @param {{ id:string, name:string, scores: Record<string, number|null>, knockouts?: Record<string, boolean> }} c
 * @returns {{ id, name, total:number, maxTotal:number, pct:number, confidence:number, unknowns:string[], knockedOutBy:string[], verdict:string }}
 */
export function scoreCandidate(c) {
  let total = 0; let maxTotal = 0; let known = 0; let knownWeight = 0;
  const unknowns = [];
  for (const [key, { weight }] of Object.entries(FACTORS)) {
    const raw = c.scores?.[key];
    maxTotal += 5 * weight;
    if (raw === null || raw === undefined) {
      unknowns.push(key);
      total += UNKNOWN_SCORE * weight;
      continue;
    }
    if (!Number.isInteger(raw) || raw < 1 || raw > 5) throw new Error(`${c.id}.${key} must be integer 1–5 or null, got ${raw}`);
    total += raw * weight; known++; knownWeight += weight;
  }
  const allWeight = Object.values(FACTORS).reduce((s, f) => s + f.weight, 0);
  const knockedOutBy = Object.entries(c.knockouts ?? {}).filter(([, v]) => v === true).map(([k]) => k);
  for (const k of knockedOutBy) if (!(k in KNOCKOUTS)) throw new Error(`Unknown knockout ${k}`);
  const pct = total / maxTotal;
  const confidence = knownWeight / allWeight;
  let verdict;
  if (knockedOutBy.length) verdict = 'REJECT';
  else if (confidence < 0.6) verdict = 'NEEDS_RESEARCH';
  else if (pct >= 0.7) verdict = 'SHORTLIST';
  else if (pct >= 0.55) verdict = 'HOLD';
  else verdict = 'REJECT';
  return { id: c.id, name: c.name, total, maxTotal, pct, confidence, unknowns, knockedOutBy, verdict, known };
}

export function rankCandidates(candidates) {
  const order = { SHORTLIST: 0, HOLD: 1, NEEDS_RESEARCH: 2, REJECT: 3 };
  return candidates.map(scoreCandidate).sort((a, b) => order[a.verdict] - order[b.verdict] || b.pct - a.pct);
}
