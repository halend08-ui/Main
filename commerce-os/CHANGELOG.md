# CHANGELOG

## Unreleased — 2026-10-14
- Added REA (`rea-agents@6.5.0`) as a project MCP server in `/.mcp.json` and its skill in `/.claude/skills/reverse-engineer-anything/` (DEC-011, RISK-010). Native engines not installed.

## 0.1.0 — 2026-10-03 — Operating system bootstrap
- Repository structure, constitution (`CLAUDE.md`), memory/state files.
- Shopify CLI 4.8.4 installed; Shopify Dev MCP 1.16.0 configured (`.mcp.json`).
- Zero-dependency library: GraphQL Admin client (dry-run default, throttle handling), 13 schema-validated operations, webhook HMAC + idempotency, structured redacting logger, financial guards, metric math, candidate scoring.
- 34 unit tests; secret scan; financial-limit check; link check; GraphQL validation script.
- Guard hook blocking edits to financial limits / `.env`; CI workflow; Claude Code GitHub Action workflow.
- Agent roster (13 Claude Code subagents).
- Product research workflow + templates.
