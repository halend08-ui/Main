# LESSONS

Durable lessons learned from evidence (experiments, incidents, audits). Newest first.

- **2026-10-03 · Verify the verifier.** The GraphQL validator was negative-tested with a fake field before trusting PASS results. Apply the same to every new check.
- **2026-10-03 · Container network ≠ owner network.** Shopify hosts are blocked in the cloud container; MCP schema validation still works offline. Plan auth-dependent steps for the owner's machine or an allow-listed environment.
