# METRICS

Definitions are canonical; math lives in `lib/analytics/metrics.mjs`. Every value recorded below is
labelled **MEASURED** (source + date range) or **ESTIMATED** (method). Missing = `UNKNOWN`.

## Definitions

| Metric | Definition | Source (planned) |
|---|---|---|
| Sessions | Online store sessions | Shopify Analytics (ShopifyQL) |
| Add-to-cart rate | sessions with ATC ÷ sessions | Shopify Analytics |
| Checkout rate | sessions reaching checkout ÷ sessions | Shopify Analytics |
| Conversion rate (CVR) | orders ÷ sessions | Shopify |
| AOV | net revenue ÷ orders | Admin API orders |
| Revenue (net) | subtotal − discounts − refunds (excl. tax/shipping charged) | Admin API orders |
| Refund rate | refunded amount ÷ revenue | Admin API orders |
| COGS | landed unit cost × units (supplier invoices) | supplier data (manual until integrated) |
| Contribution margin | revenue − COGS − shipping cost − payment fees − refunds − ad spend | computed |
| Break-even ROAS | revenue ÷ unit contribution before ads | computed |
| CTR / CPC / CPM | clicks÷impressions / spend÷clicks / spend÷impressions×1000 | ad platform APIs |
| CPA / CAC | ad spend ÷ conversions / ad spend ÷ new customers | ad platforms + Shopify |
| ROAS | attributed revenue ÷ ad spend (state attribution window) | ad platforms |
| Returning-customer rate | customers with ≥2 orders ÷ customers | Shopify |

## Data-quality checks (run before interpreting)
1. Tracking present on all templates (pixel / analytics events firing — verify in QA).
2. Bot/spam sessions excluded where identifiable.
3. Attribution window stated for every ROAS/CPA.
4. Currency consistent.
5. Refunds lag — mark last 14 days' refund rate as provisional.

## Latest values

_No store yet — all metrics UNKNOWN._
