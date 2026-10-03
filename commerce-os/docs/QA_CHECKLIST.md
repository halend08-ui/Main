# QA checklist (report each item as PASS / FAIL / NOT RUN + reason)

## Automated
- [ ] `npm run check` (tests, secret scan, limits, links)
- [ ] `npm run validate:graphql`
- [ ] `shopify theme check --path store/theme` — zero errors
- [ ] MCP `validate_theme` on changed Liquid files
- [ ] Playwright smoke (when preview URL reachable): home, collection, product, cart, search, 404 at 375px and 1440px; no console errors; no failed requests
- [ ] Lighthouse mobile: performance, accessibility, SEO, best practices recorded

## Manual / visual
- [ ] Navigation + footer links resolve; no placeholder links
- [ ] Variant selection updates price, image, availability, URL
- [ ] Add to cart → cart → checkout handoff works (dev store test mode only)
- [ ] Images: alt text, correct aspect ratios, lazy below fold
- [ ] Accessibility basics: keyboard nav, focus visible, contrast, labelled form fields, heading order
- [ ] SEO: unique titles/meta descriptions, canonical, one H1, structured data present
- [ ] Policy pages present and flagged where owner/legal confirmation pending
- [ ] Copy claims traced in `products/<handle>/CLAIMS.md`
- [ ] Tracking: page_view, view_item, add_to_cart, begin_checkout, purchase events fire once
