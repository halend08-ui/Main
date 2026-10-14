# DECISIONS

ADR-style log. Newest first. Format: context → options → decision → consequences. IDs never reused.

---

### DEC-011 · 2026-10-14 · Add REA (`rea-agents@6.5.0`) as a project MCP server + skill
- **Context:** Owner asked to download REA ("Reverse Engineer Anything", github.com/morluto/rea), verify it, and add it to the project. It is a third-party package, so §6.4 vetting applies.
- **Vetting (§6.4):**
  - Owner: GitHub user `morluto`; official sources per its README = github.com/morluto/rea, rea.tools, npm `rea-agents`. Not Shopify/Anthropic/GitHub/OpenJS.
  - Purpose: local static/runtime analysis of binaries, JavaScript/Electron apps, .NET, Android and web bundles, exposed as one CLI + MCP server (207 tools).
  - Official alternative: none for this capability. Shopify work does not need it (it already runs on the Shopify Dev MCP + docs).
  - Last release: 6.5.0, npm modified 2026-10-14 (same day as adoption; pinned exactly, see below). License: MIT.
  - Known vulnerabilities: `npm audit` on a clean install of `rea-agents@6.5.0` → **0 vulnerabilities** (173 packages). One deprecation warning (`prebuild-install@7.1.3`).
  - Install scripts: none in its `package.json` (no preinstall/install/postinstall).
  - Telemetry / network: documented as local-only; source grep found no analytics/telemetry client. Runtime-capture tools (browser, process, adb, network) can touch other systems — see usage boundary.
- **Verified in this container (2026-10-14):** source clone → `npm ci` ✅, `npm run build:cached` ✅, `npm run test:fast` ✅ **523/523 test files, 7,868 tests passed, 13 skipped**; published CLI `analyze-javascript-application` on a self-written Electron sample ✅ (traced `api.search` → IPC `search` with file/line evidence); MCP `initialize` + `tools/list` ✅ (server `rea` 6.5.0, protocol 2025-06-18, 207 tools). `rea doctor` = not healthy only because no native engine (Ghidra/Hopper/IDA) is installed. Ghidra download blocked by container egress (github.com release pages → 403).
- **Decision:**
  - Register in `/.mcp.json` as `npx -y rea-agents@6.5.0 mcp`, **pinned exactly** (same policy as DEC-004). REA's docs prefer local scope over `.mcp.json`; we use project scope deliberately so the config is reviewed in git and pinned. It contains no secrets or paths.
  - Vendor REA's bundled skill (built from source at 6.5.0) into `/.claude/skills/reverse-engineer-anything/` with its MIT `LICENSE`. Regenerate it from the matching release on every version bump.
  - Not installed: native engines. Ghidra (free) needs network access to github.com; Hopper/IDA are paid → APR required (§4).
- **Usage boundary (binding):** REA may be used only on targets the owner owns or is authorized to examine (our own theme/app builds, purchased tools whose licence permits analysis, security review of our own stack). **Never** on competitor stores/apps to copy features, code or assets (§10 plagiarism), and never to bypass bot protection, DRM, licence checks, rate limits or ToS (§4). Competitive intelligence stays desk research that extracts principles. Runtime-capture tools that interact with third-party systems need owner approval per target. See RISK-010.
- **Consequences:** New sessions at the repo root will prompt to approve the `rea` project MCP server. Version bump = DEC entry + re-run of the checks above. Remove by deleting the `rea` entry and the skill directory.

### DEC-010 · 2026-10-03 · Stage 1 shortlist: C-005, C-012, C-008 (C-010 alternate)
- **Evidence:** `research/candidates/RANKING.md` (12 candidates, 3 researchers, 105 sources).
- **Decision:** Follow the workflow mechanically — top 3 non-rejected by score: C-005 cat couch deter+redirect kit (71%), C-012 travel jewelry case (69%), C-008 gravity vacation watering kit (68%). C-010 yarn project bag (67%) is first alternate — the gap is within scoring noise.
- **Caveats:** All four are separated by 4 points; evidence is search-snippet level (RISK-009). The shortlist is **provisional** until key sources are re-opened (V-R1). C-008 is summer-skewed and it is October — the deep dive must test the houseplant/holiday-travel angle or defer. C-012 sits in a branded, crowded market (differentiation 2/5).
- **Rejected on margin floor (DEC-008):** C-001, C-003, C-004, C-009, C-011.

### DEC-009 · 2026-10-03 · Scoring consistency rules
- `supplierReliability` = null (UNKNOWN) while suppliers are only unverified marketplace listings (one researcher scored 2; normalised).
- `demandEvidence` capped at 3 unless a quantitative demand source exists (search volume, sales rank history, published sales). None exists yet → C-012 lowered 4→3.
- Known gap: the confidence metric counts a factor as "known" even when backed only by search snippets, so 94% confidence overstates certainty. Follow-up: add evidence-quality weighting to `scoring.mjs` (task T-11).

### DEC-008 · 2026-10-03 · Margin floor defined (two-part, mid case)
- **Context:** Workflow said "gross margin before ads < 60%" without saying whether outbound shipping counts; all three researchers flagged it.
- **Decision:** A candidate fails (`marginBelowFloor`) if, in its **mid-case** scenario, either
  (a) product gross margin `(price − landed cost) / price` < **60%**, or
  (b) contribution before ads `(price − landed − shipping − payment fees) / price` < **40%** (⇒ break-even ROAS ≤ 2.5).
- **Rationale:** (b) is what pays for customer acquisition; a 40% floor keeps first-order break-even ROAS achievable for a small paid test. Revisit with real quotes and measured CAC.
- **Applied:** C-001 (55% GM, 13% contrib.), C-003 (~30% contrib.), C-004 (36.6% contrib.), C-009 (~36% contrib., orchestrator mid-case calc), C-011 (60% GM but ~19% contrib.) → rejected.

---

### DEC-007 · 2026-10-03 · Zero runtime npm dependencies for the library
- **Context:** CLAUDE.md requires rejecting unnecessary dependencies; Node 22 provides fetch, crypto, node:test.
- **Decision:** `lib/` uses only Node built-ins. Tests use `node:test`. Linting deferred (syntax is checked by tests importing every module).
- **Consequences:** Smaller attack surface; no lockfile churn. Adding ESLint/TypeScript later requires a DEC entry.

### DEC-006 · 2026-10-03 · Working assumption: US-first, English, USD
- **Context:** Owner has not specified market. Prompt says not to ask dozens of setup questions.
- **Decision:** Research assumes US consumers, USD pricing, English copy. Labelled ASSUMPTION A-001.
- **Consequences:** Owner can override at any time; research templates record geography so it can be re-run.

### DEC-005 · 2026-10-03 · Build inside existing repo under `commerce-os/`
- **Context:** Session scoped to `halend08-ui/Main`, which already contains an unrelated React demo ("Meridian").
- **Options:** (a) new dedicated private repo — needs owner action; (b) subdirectory here.
- **Decision:** (b) now, keeping the demo untouched; recommend (a) in APR-001. `git subtree split --prefix commerce-os` can extract history later.

### DEC-004 · 2026-10-03 · Shopify Dev MCP pinned to `@shopify/dev-mcp@1.16.0`
- **Context:** Shopify's README recommends `@latest`. Vetting: owner = Shopify (npm maintainers @shopify.com), license ISC, published 2026-09-25, official.
- **Decision:** Pin exact version in `.mcp.json` and `scripts/validate-graphql.mjs` for reproducibility/supply-chain safety; bump quarterly with the API version review. Telemetry opted out (`OPT_OUT_INSTRUMENTATION=true`).
- **Verified:** Server starts; tools = learn_shopify_api, search_docs_chunks, validate, validate_theme, feedback. Schema validation works offline (negative control caught a fake field). `search_docs_chunks` needs shopify.dev (blocked in this container).

### DEC-003 · 2026-10-03 · Admin API version pinned to 2026-07
- **Evidence:** Shopify Dev MCP `learn_shopify_api(api: admin)` → "API Version: 2026-07 (default)". The MCP bundle also lists 2026-10 (likely just released / RC; not yet default in this MCP version).
- **Decision:** Pin `2026-07` in `config/shopify.json`; review 2027-01-01.

### DEC-002 · 2026-10-03 · Shopify CLI installed via npm (`@shopify/cli` 4.8.4)
- **Vetting:** Official (github.com/Shopify/cli), MIT, published 2026-10-02, requires Node ≥ 22.12 (have 22.22.0). Official install method is the npm global package.

### DEC-001 · 2026-10-03 · Staged validation before building a store
- **Decision:** Follow stages 1–7 (BUSINESS_STATE.md). No theme/product build before a documented product thesis; no spend before approval.
