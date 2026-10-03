# Analytics architecture

Status: **designed, not connected** (no store yet).

## Sources
| Source | Data | Access | Status |
|---|---|---|---|
| Shopify Analytics via ShopifyQL (`shopifyqlQuery`) | sessions, funnel, conversion, sales by channel | scope `read_reports` (read-only) | needs store auth |
| Admin GraphQL `orders` (`OrdersForAnalytics.graphql`) | revenue, refunds, discounts, first-visit UTM | `read_orders` | needs store auth |
| Shopify customer events / pixels | storefront events for ad platforms | owner configures in admin | UNVERIFIED details (V-4) |
| Ad platforms (Meta / Google / TikTok) | spend, impressions, clicks, attributed conv. | platform APIs | after APR for ads |
| Supplier invoices | landed COGS | manual CSV → `analytics/cogs.csv` (no PII) | later |

## Pipeline
1. Daily job pulls ShopifyQL + orders (no PII selected) → aggregates only → `reports/daily/`.
2. `lib/analytics/metrics.mjs` computes metrics (null = UNKNOWN).
3. Data-quality checks (METRICS.md) gate interpretation.
4. Experiments read primary metrics from the same pipeline (`twoProportionTest`).

## UTM convention
`utm_source=<platform>&utm_medium=<paid|organic|email>&utm_campaign=<EXP-###>_<angle>&utm_content=<creative-id>`

## Privacy
Aggregate-only storage in git. No customer names, emails, addresses, or order-level PII in repo or logs.
