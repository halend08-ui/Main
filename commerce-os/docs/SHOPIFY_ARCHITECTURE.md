# Shopify development architecture (researched 2026-10-03)

Sources (authoritative, verified this session):
- **[S-SH1]** Shopify Dev MCP `@shopify/dev-mcp@1.16.0` — `learn_shopify_api` guidance for `admin`, `liquid`, `use-shopify-cli` (bundled, offline).
- **[S-SH2]** Shopify CLI `@shopify/cli@4.8.4` `--help` output on this machine.
- **[S-SH3]** `@shopify/dev-mcp` README (npm tarball).
- shopify.dev itself was **not reachable** from this container (egress policy) — anything below not covered by S-SH1..3 is marked UNVERIFIED.

## 1. Tooling stack

| Layer | Choice | Why / evidence |
|---|---|---|
| CLI | Shopify CLI 4.x (`npm i -g @shopify/cli`) | Official; Node ≥ 22.12 [S-SH2] |
| Docs/schema authority | Shopify Dev MCP (`.mcp.json`) | Doc search, GraphQL + Liquid validation [S-SH3] |
| Theme | Online Store 2.0 theme in `store/theme/` | sections / blocks / snippets / JSON templates [S-SH1] |
| Admin automation | GraphQL Admin API `2026-07` | default version reported by MCP [S-SH1] |
| Store ops without an app | `shopify store auth` + `shopify store execute` | CLI-managed scoped token; `--allow-mutations` needed for writes [S-SH1, S-SH2] |
| Bulk reads | `shopify store bulk execute` | large exports [S-SH2] |
| Analytics queries | ShopifyQL via `shopifyqlQuery` (scope `read_reports`, read-only) | sessions, conversion, sales [S-SH1] |
| Webhooks | App-based subscriptions (`webhookSubscriptionCreate`) or app TOML config | needed only once we have an endpoint |

## 2. Two ways to talk to the Admin API

1. **Shopify CLI store session (recommended first)** — no custom app or long-lived token in our
   environment. `shopify store auth --store <shop> --scopes <minimum>` (browser login, owner action),
   then `shopify store execute --store <shop> --query-file lib/shopify/operations/X.graphql`
   (add `--allow-mutations` for writes). Our validator prints the operations; `validate` output lists
   *Required scopes* — request exactly those.
2. **Library client (`lib/shopify/client.mjs`)** — for unattended automation (CI, scheduled jobs,
   webhook processors). Needs an Admin API access token from an app the owner installs, stored only
   as a GitHub Environment secret. Dry-run by default.

## 3. Theme architecture rules [S-SH1]

- Directories: `assets blocks config layout locales sections snippets templates`.
- Sections and blocks must contain `{% schema %}`; use `{{ block.shopify_attributes }}` on block wrappers.
- Theme blocks are nestable via `{% content_for 'blocks' %}`; statically rendered blocks need `{% doc %}`.
- Snippets must start with a LiquidDoc `{% doc %}` header; render with `{% render %}` (isolated scope).
- Per-component CSS/JS via `{% stylesheet %}` / `{% javascript %}` (Liquid not rendered inside).
- Layout must output `{{ content_for_header }}` and `{{ content_for_layout }}`.
- Liquid gotchas: no parentheses or ternaries; `for` loops cap at 50 → use `{% paginate %}`.
- Validate every Liquid file with MCP `validate_theme` and `shopify theme check`.

## 4. Theme starting point (decision pending, T-08)

Options: Shopify's reference theme via `shopify theme init` (clones a Git repo; default = Skeleton
starter) vs Shopify's current free flagship theme (UNVERIFIED which one is current — check in admin).
Leaning: start from Shopify's official starter for full control + performance; record as DEC when chosen.

## 5. Development flow

```
shopify theme dev --store <shop> --path store/theme        # local preview on a development theme
shopify theme check --path store/theme                     # lint
shopify theme push --unpublished --path store/theme        # share an unpublished theme for QA
# shopify theme publish  → APPROVAL REQUIRED (denied in .claude/settings.json)
```
CI: GitHub Actions runs tests, secret scan, GraphQL validation and theme check. Deployment to a store
from CI requires a Theme Access password stored as an Environment secret with required reviewers.

## 6. Webhooks [implementation in lib/webhooks/verify.mjs]

HMAC-SHA256 of the raw body with the app client secret, base64, compared timing-safe to
`X-Shopify-Hmac-Sha256`; dedupe on `X-Shopify-Event-Id` (fallback `X-Shopify-Webhook-Id`); respond
fast and process asynchronously. **UNVERIFIED against current shopify.dev** in this session (RISK-005).
Candidate topics when an endpoint exists: `orders/create`, `orders/updated`, `refunds/create`,
`products/update`, `inventory_levels/update`, `app/uninstalled`; mandatory privacy topics for apps.

## 7. Open verification tasks (need shopify.dev access)
- V-1 Webhook header names/semantics and current recommended delivery method.
- V-2 Current Shopify free flagship theme name and its OS 2.0 block architecture.
- V-3 Whether 2026-10 is now the stable default (MCP 1.16.0 defaults to 2026-07).
- V-4 Customer-events / Web Pixels API for custom tracking events.
