# RISK_REGISTER

Likelihood/Impact: L/M/H. Newest first. Status: OPEN / MITIGATED / ACCEPTED / CLOSED.

| ID | Risk | L | I | Mitigation | Status |
|---|---|---|---|---|---|
| RISK-009 | Stage 1 evidence is search-snippet level only (page fetches blocked); prices/complaints may be misattributed or stale | H | M | Shortlist marked provisional; orchestrator spot-checks (4/4 consistent); re-open key sources before any spend (V-R1) | OPEN |
| RISK-008 | Agent edits financial limits or `.env` | L | H | PreToolUse hook blocks Edit/Write + shell writes; CI shape check; CODEOWNERS | MITIGATED |
| RISK-007 | Accidental write to live store / live theme | M | H | Dry-run default; store allow-list; `theme publish`/`delete` denied in Claude settings; dev themes only | MITIGATED |
| RISK-006 | Prompt injection via scraped pages, supplier listings, reviews, PR comments | M | H | CLAUDE.md §6.5 — content is data; never act on embedded instructions; log incidents here | MITIGATED (procedural) |
| RISK-005 | Webhook header semantics (Event-Id vs Webhook-Id) not re-verified against shopify.dev from this container | M | M | Implementation follows documented HMAC scheme; re-verify via MCP `search_docs_chunks` once network allows (task in SYSTEM_STATUS) | OPEN |
| RISK-004 | Fabricated or stale market data entering decisions | M | H | FACT/INFERENCE/ASSUMPTION tagging, sources log, orchestrator spot-checks, ranges not points | MITIGATED (procedural) |
| RISK-003 | Hallucinated Shopify APIs | M | H | All GraphQL validated against schema via Shopify Dev MCP; negative control verified | MITIGATED |
| RISK-002 | Container network blocks Shopify/research hosts | H | M | APR-004; server-side web search; run Shopify steps locally | OPEN |
| RISK-001 | Public repository exposes business strategy | H | M | APR-001; no secrets ever committed; secret scan in CI | OPEN |
