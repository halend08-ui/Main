# Agent roster

Agents are Claude Code subagents defined in [`/.claude/agents/`](../../.claude/agents/). Each carries the
non-negotiables from `CLAUDE.md`. The **orchestrator** is the main Claude session.

| Role | Subagent | Primary outputs |
|---|---|---|
| Orchestrator | main session | `BUSINESS_STATE.md`, task queue, verification of agent claims, `DECISIONS.md` |
| Market intelligence | `market-intelligence` | `research/`, `research/sources/SOURCES.md` |
| Product analyst | `product-analyst` | `research/candidates/candidates.json`, theses, unit economics |
| Competitive intelligence | `competitive-intelligence` | `research/competitors/` |
| Supplier research | `supplier-research` | `supplier-research/` |
| Brand strategist | `brand-strategist` | `brand/` |
| Shopify engineer | `shopify-engineer` | `store/theme/`, `lib/shopify/` |
| UX/CRO | `ux-cro` | `analytics/cro-audits/`, experiment proposals |
| Copywriter | `copywriter` | `products/`, `seo/`, page copy, claims tables |
| Creative strategist | `creative-strategist` | `marketing/CREATIVE_MATRIX.md`, briefs |
| Paid media | `paid-media` | `ads/` campaign drafts (never launched without approval) |
| Organic growth | `organic-growth` | `organic/`, `seo/` |
| Email/lifecycle | `email-lifecycle` | `email/` |
| Analytics | `analytics` | `METRICS.md`, `reports/` |
| QA | `qa` | QA reports (PASS/FAIL/NOT RUN) |
| Security/risk | `security-risk` | `RISK_REGISTER.md` |

## Orchestration rules
1. One owner per task (see task queue in `BUSINESS_STATE.md`) — prevents duplicated work.
2. Delegate bounded tasks with explicit output paths and acceptance criteria.
3. **Verify before trusting:** re-open ≥2 cited sources per important claim; re-run numeric calculations.
4. Conflicts between agents are resolved by evidence; the decision is logged in `DECISIONS.md`.
5. Agents never approve their own spend; approvals go to `APPROVAL_QUEUE.md`.
