# SYSTEM_STATUS

_Generated 2026-10-03 by orchestrator. Everything below was actually run/inspected this session unless marked otherwise._

## Environment
✅ **Ready** — cloud container (Linux x86_64), ephemeral; durable state = git.

| Tool | Version | Status |
|---|---|---|
| Git | 2.43.0 | ✅ |
| Node.js | 22.22.0 | ✅ (Shopify CLI needs ≥ 22.12) |
| npm | 10.9.4 | ✅ |
| GitHub CLI | 2.89.0 | ⚠️ installed but not used — GitHub access is via the GitHub MCP connector in this environment |
| Claude Code | 2.1.288 | ✅ |
| Shopify CLI | 4.8.4 | ✅ installed this session (`npm i -g @shopify/cli@latest`, official method) — **reinstall needed in a fresh container** |

**Network:** npm registry, GitHub ✅. `shopify.dev`, `*.shopify.com`, `*.myshopify.com`, Google, Reddit, Amazon ❌ blocked by the container's egress policy (APR-004). Server-side web search ✅.

## Shopify
⛔ **Not connected.** No store, no authentication.
- Library ready in dry-run: GraphQL Admin client + 13 operations, **13/13 validated** against Admin API `2026-07` schema (negative control confirmed the validator rejects fake fields).
- Recommended auth path: `shopify store auth` (CLI-managed, scoped) → needs owner login + network (APR-002, APR-004).
- Pinned API version `2026-07` (Shopify Dev MCP default). Review 2027-01-01.

## Git/GitHub
✅ Repo `halend08-ui/Main`, branch `claude/repo-initialization-nb7dsm`, pushes via session proxy.
⚠️ **Repository is PUBLIC** (APR-001). CODEOWNERS added; branch protection not configured (owner setting).
✅ CI workflow `.github/workflows/commerce-os-ci.yml` (tests, secret scan, limits, links, GraphQL validation, theme check when a theme exists).
⏸ Claude Code Action `.github/workflows/claude.yml` ready, skips until `ANTHROPIC_API_KEY` secret + Claude GitHub App (APR-003).

## MCP
✅ Shopify Dev MCP `@shopify/dev-mcp@1.16.0` configured project-wide in `/.mcp.json` (telemetry opted out). Verified tools: `learn_shopify_api`, `search_docs_chunks`, `validate`, `validate_theme`, `feedback`.
- Works offline: API guidance, GraphQL validation, Liquid validation.
- Needs network: `search_docs_chunks` (shopify.dev) — blocked here.
- Loads automatically in new Claude Code sessions opened at the repo root (approve the project MCP server when prompted).

## Repository
✅ `commerce-os/` structure created (agents, research, brand, store, products, marketing, ads, organic, email, seo, analytics, experiments, operations, customer-support, supplier-research, scripts, tests, docs, memory, decisions, reports, assets, archive, config, lib).
✅ Unrelated root "Meridian" React demo untouched.

## Agents
✅ 15 specialist subagents in `/.claude/agents/` + orchestrator (main session). Roster: `agents/README.md`.

## Memory
✅ `CLAUDE.md`, `BUSINESS_STATE.md`, `DECISIONS.md` (7 decisions), `EXPERIMENTS.md`, `METRICS.md`, `APPROVAL_QUEUE.md` (4 open), `RISK_REGISTER.md` (8 risks), `CHANGELOG.md`, `memory/LESSONS.md`, `operations/INCIDENTS.md`.

## Testing
✅ `npm test` — **37/37 passing** (guards, guard hook, logger, Shopify client, webhooks, metrics, scoring).
✅ `npm run scan:secrets`, `npm run check:limits`, `npm run check:links` passing.
✅ `npm run validate:graphql` 13/13.
⏳ Theme check / Playwright / Lighthouse — NOT RUN (no theme yet).
⏳ CI has not yet run on GitHub — first run happens when a PR is opened.

## Analytics architecture
📐 Designed, not connected (`docs/ANALYTICS_ARCHITECTURE.md`): ShopifyQL (`read_reports`) + orders (no PII) + ad-platform APIs → `lib/analytics/metrics.mjs` → daily/weekly reports. Metric definitions in `METRICS.md`.

## Safety controls
✅ Financial limits all 0 (`config/financial-limits.json`), guarded by PreToolUse hook (blocks edits + shell writes; tested), CODEOWNERS, CI shape check, `assertWithinLimit()` in code.
✅ Write mode dry-run default + store allow-list. `theme publish`/`theme delete`/force-push denied in `.claude/settings.json`.

## Missing authentication
1. **Shopify** — store login via `shopify store auth` (APR-002); needs network allow-list (APR-004) or run locally.
2. **Claude Code GitHub Action** — Claude GitHub App + `ANTHROPIC_API_KEY` repo secret (APR-003).
3. **Ad platforms / email / social** — not needed until Stage 4–5.

## Human approvals currently required
- APR-001 Make repo private (or create a dedicated private repo).
- APR-002 Shopify store login (and whether to create a dev store).
- APR-003 Claude GitHub App + API key secret.
- APR-004 Network allow-list for Shopify hosts in this cloud environment.
_No spending approvals pending._

## Next autonomous action
Stage 1 pool complete (12 candidates, provisional shortlist C-005 / C-012 / C-008 — DEC-010). Next: deep dives on the shortlist — competitor teardowns (T-03), supplier desk research (T-04), unit economics (T-05) — then a product thesis and a sample-order approval request (T-06). Evidence quality is limited until retail/forum page access is allowed (APR-004, RISK-009).

## Known verification gaps
V-1..V-4 in `docs/SHOPIFY_ARCHITECTURE.md` (webhook headers, current flagship theme, 2026-10 status, customer events) — need shopify.dev access.
