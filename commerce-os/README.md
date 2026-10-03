# Commerce OS

An evidence-driven operating system for researching, building, and running a Shopify business with
minimal owner involvement — while keeping money, legal commitments, and irreversible actions under
owner control.

**Start here:** [`CLAUDE.md`](CLAUDE.md) (constitution) → [`SYSTEM_STATUS.md`](SYSTEM_STATUS.md) →
[`BUSINESS_STATE.md`](BUSINESS_STATE.md) → [`APPROVAL_QUEUE.md`](APPROVAL_QUEUE.md).

## Quick start

```bash
cd commerce-os
npm test                     # unit tests (node:test, zero dependencies)
npm run check                # tests + secret scan + financial-limit check + link check
npm run validate:graphql     # validate all GraphQL against Shopify's Admin schema (Shopify Dev MCP)
npm run score:candidates     # rank product candidates → research/candidates/RANKING.md
npm run new:experiment -- "Hero headline: problem vs outcome"
```

Requirements: Node ≥ 22.12, Shopify CLI 4.x (`npm i -g @shopify/cli`). Copy `.env.example` to `.env`
only on a trusted machine; never commit it.

## Safety model (short version)
- All financial limits in `config/financial-limits.json` are **0** → any spend needs approval. Agents cannot edit that file (hook + CODEOWNERS).
- Store writes are **dry-run** unless `COMMERCE_OS_WRITE_MODE=live` and the store is allow-listed.
- `shopify theme publish` / `theme delete` / force-push are denied to Claude.
- No secrets in git; CI scans every push.

## Layout
See `CLAUDE.md` §2. Agent roster: [`agents/README.md`](agents/README.md). Shopify architecture:
[`docs/SHOPIFY_ARCHITECTURE.md`](docs/SHOPIFY_ARCHITECTURE.md). Research process:
[`research/PRODUCT_RESEARCH_WORKFLOW.md`](research/PRODUCT_RESEARCH_WORKFLOW.md).
