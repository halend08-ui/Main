# APPROVAL_QUEUE

Things only the human owner can do or approve. Newest first. Work is prepared before asking.
Resolve by replying in chat (or editing **Status**); the orchestrator records the outcome.

| ID | Item | Type | Cost | Status |
|---|---|---|---|---|
| APR-004 | Allow Shopify/research domains in the cloud environment network policy | environment setting | none | OPEN |
| APR-003 | Add `ANTHROPIC_API_KEY` (or OAuth token) secret + install Claude GitHub App for Claude Code Action | auth | API usage billing | OPEN |
| APR-002 | Shopify authentication: create/choose a development store and log in to Shopify CLI | auth | none for a dev store | OPEN |
| APR-001 | Repository `halend08-ui/Main` is **public** — make private or create a dedicated private repo | repo setting | none | OPEN |

---

### APR-001 · Repository is public
- **Prepared:** All commerce-os work is in `commerce-os/`; no secrets are committed (secret scan in CI).
- **Why it matters:** Research, product thesis, pricing and supplier notes are competitive information. Public repos are also indexed/cached.
- **Action requested (pick one):** GitHub → Settings → General → Danger Zone → *Change visibility → Private*; **or** create a new private repo (e.g. `commerce-os`) and tell me its name — I will move `commerce-os/` there with history (`git subtree split`).
- **Reversible:** yes · **Recommendation:** make private before Stage 2 (supplier/pricing data).

### APR-002 · Shopify authentication
- **Prepared:** Shopify CLI 4.8.4 installed; GraphQL library (13 schema-validated operations) ready in dry-run mode.
- **Blocker:** This cloud container's network policy blocks `*.shopify.com`/`shopify.dev`, so `shopify auth login` cannot run here. Two options:
  1. Allow the domains (APR-004) and run `shopify theme dev --store <your-store>.myshopify.com` — a login URL will be printed; open it and log in.
  2. Or run Claude Code locally on your machine in this repo; the browser login opens automatically.
- **Shortest path once network allows:** `shopify store auth --store <your-store>.myshopify.com --scopes read_products` → a browser login opens → log in → tell me "done". I widen scopes only when a validated operation needs them.
- **No store yet?** Shopify CLI 4.x can create one (`shopify store create dev` or `shopify store create preview`), but creating a store accepts Shopify's terms on your behalf, so I will not run it without your explicit go-ahead.
- **Recommendation:** Use a free **development store** for Stages 3–4 (no plan purchase needed until launch).

### APR-003 · Claude Code GitHub Action
- **Prepared:** `.github/workflows/claude.yml` (responds to `@claude` mentions; least-privilege permissions).
- **Action requested:** install the Claude GitHub App on the repo (https://github.com/apps/claude) and add repo secret `ANTHROPIC_API_KEY` (Settings → Secrets and variables → Actions). Usage is billed to that key.
- **Until then:** the workflow skips cleanly when the secret is missing.

### APR-004 · Network allow-list for this cloud environment
- **Blocked hosts observed 2026-10-03:** `shopify.dev`, `accounts.shopify.com`, `partners.shopify.com`, `admin.shopify.com`, `*.myshopify.com`, `google.com`, `trends.google.com`, `reddit.com`, `amazon.com`.
- **Action:** Environment settings → Network access → Custom → add `shopify.dev`, `*.shopify.com`, `*.myshopify.com`, `*.shopifycdn.com`, `cdn.shopify.com` (keep default package-manager list). Docs: https://code.claude.com/docs/en/cloud-environments#network-access
- **Also useful for research (optional):** `amazon.com`, `walmart.com`, `target.com`, `etsy.com`, `reddit.com`, `alibaba.com` — research agents could only see search-result summaries, not the pages themselves (RISK-009).
- **Impact if declined:** Shopify docs search via MCP and store operations must run from your local machine; web research continues via the server-side search tool.
