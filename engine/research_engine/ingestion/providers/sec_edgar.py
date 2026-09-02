"""SEC EDGAR provider -- primary-source US fundamentals.

Upstream: https://data.sec.gov (XBRL "company facts" API) plus
https://www.sec.gov/files/company_tickers.json for the ticker->CIK map.
Free, no key. The SEC requires a descriptive ``User-Agent`` containing a
contact address and asks for <= 10 requests/second.

Why this is the default fundamentals source: it is a **regulatory filing**, the
top of our source hierarchy, and every fact carries the accession number, form
type and *filing date* -- which is what makes point-in-time analysis honest.
Vendor-normalised fundamentals are convenient but obscure restatements.

Mapping caveat (documented, not hidden): companies tag revenue under several
US-GAAP concepts. We try a documented priority list per canonical metric and
record which concept was used, rather than silently blending them.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from research_engine.core.errors import DataUnavailable, ProviderError
from research_engine.core.logging import get_logger
from research_engine.core.timeutil import to_date
from research_engine.core.types import DataQuality, SourceTier
from research_engine.ingestion.base import Capability, DataProvider, ProviderResult

log = get_logger(__name__)

#: canonical metric -> ordered US-GAAP (or dei) concepts, most specific first
CONCEPT_MAP: dict[str, tuple[str, ...]] = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax",
                "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet"),
    "cost_of_revenue": ("CostOfRevenue", "CostOfGoodsAndServicesSold",
                        "CostOfGoodsSold"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "eps_diluted": ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"),
    "eps_basic": ("EarningsPerShareBasic",),
    "shares_diluted": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
    "shares_outstanding": ("CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"),
    "rd_expense": ("ResearchAndDevelopmentExpense",),
    "sgna_expense": ("SellingGeneralAndAdministrativeExpense",),
    "interest_expense": ("InterestExpense", "InterestIncomeExpenseNet"),
    "income_tax": ("IncomeTaxExpenseBenefit",),
    "pretax_income": ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"),
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "total_equity": ("StockholdersEquity",
                     "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "cash_and_equivalents": ("CashAndCashEquivalentsAtCarryingValue",
                             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
    "short_term_investments": ("ShortTermInvestments", "MarketableSecuritiesCurrent"),
    "total_debt": ("DebtLongtermAndShorttermCombinedAmount",),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "short_term_debt": ("LongTermDebtCurrent", "ShortTermBorrowings",
                        "DebtCurrent"),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "inventory": ("InventoryNet",),
    "goodwill": ("Goodwill",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",
                            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets"),
    "dividends_paid": ("PaymentsOfDividendsCommonStock", "PaymentsOfDividends"),
    "buybacks": ("PaymentsForRepurchaseOfCommonStock",),
    "stock_compensation": ("ShareBasedCompensation",),
    "depreciation_amortization": ("DepreciationDepletionAndAmortization",
                                  "DepreciationAndAmortization"),
}

_STATEMENT_BY_METRIC = {
    "revenue": "income", "cost_of_revenue": "income", "gross_profit": "income",
    "operating_income": "income", "net_income": "income", "eps_diluted": "income",
    "eps_basic": "income", "rd_expense": "income", "sgna_expense": "income",
    "interest_expense": "income", "income_tax": "income", "pretax_income": "income",
    "total_assets": "balance", "total_liabilities": "balance", "total_equity": "balance",
    "cash_and_equivalents": "balance", "short_term_investments": "balance",
    "total_debt": "balance", "long_term_debt": "balance", "short_term_debt": "balance",
    "current_assets": "balance", "current_liabilities": "balance",
    "inventory": "balance", "goodwill": "balance", "shares_outstanding": "balance",
    "operating_cash_flow": "cashflow", "capex": "cashflow",
    "dividends_paid": "cashflow", "buybacks": "cashflow",
    "stock_compensation": "cashflow", "depreciation_amortization": "cashflow",
}

_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "40-F"}
_QUARTERLY_FORMS = {"10-Q", "10-Q/A"}


class SecEdgarProvider(DataProvider):
    name = "sec_edgar"
    capabilities = frozenset({Capability.FUNDAMENTALS, Capability.UNIVERSE_EQUITY})
    source_tier = SourceTier.REGULATORY_FILING
    default_quality = DataQuality.EXCELLENT
    requires_key = False
    base_url = "https://data.sec.gov"
    documentation = ("Primary-source XBRL facts with filing dates and accession "
                     "numbers. US registrants only (plus 20-F/40-F filers).")

    def __init__(self, *, base_url: str | None = None,
                 tickers_url: str = "https://www.sec.gov/files/company_tickers.json",
                 contact_email: str | None = None,
                 ttl_seconds: float = 86_400, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.base_url = base_url or type(self).base_url
        self.tickers_url = tickers_url
        self.contact_email = contact_email
        self.ttl_seconds = ttl_seconds

    def headers(self) -> dict[str, str]:
        ua = self.user_agent
        if self.contact_email and self.contact_email not in ua:
            ua = f"{ua} ({self.contact_email})"
        return {"User-Agent": ua, "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate"}

    # -- universe ----------------------------------------------------------
    def fetch_universe(self) -> ProviderResult:
        payload = self.request_json(self.tickers_url, cache_key="company_tickers",
                                    ttl_seconds=self.ttl_seconds)
        rows = payload.values() if isinstance(payload, dict) else payload
        records = []
        for row in rows:
            ticker = (row.get("ticker") or "").strip().upper()
            cik = row.get("cik_str") or row.get("cik")
            if not ticker or cik is None:
                continue
            records.append({"symbol": ticker, "name": row.get("title"),
                            "cik": str(int(cik)).zfill(10), "country": "US"})
        if not records:
            raise DataUnavailable(f"{self.name}: empty ticker map")
        return self.result(Capability.UNIVERSE_EQUITY, records, url=self.tickers_url,
                           missing=("exchange", "sector", "industry", "market_cap"),
                           partial=True)

    # -- fundamentals ------------------------------------------------------
    def fetch_fundamentals(self, symbol: str, *,
                           identifiers: Mapping[str, Any] | None = None,
                           metrics: Sequence[str] | None = None) -> ProviderResult:
        cik = (identifiers or {}).get("cik")
        if not cik:
            raise DataUnavailable(
                f"{self.name}: CIK required for {symbol}; resolve it from the "
                f"universe map first")
        cik10 = str(cik).zfill(10)
        url = f"{self.base_url}/api/xbrl/companyfacts/CIK{cik10}.json"
        payload = self.request_json(url, cache_key=f"facts:{cik10}",
                                    ttl_seconds=self.ttl_seconds)
        facts = payload.get("facts") or {}
        if not facts:
            raise DataUnavailable(f"{self.name}: no XBRL facts for CIK {cik10}")

        wanted = list(metrics) if metrics else list(CONCEPT_MAP)
        points: list[dict[str, Any]] = []
        resolved: dict[str, str] = {}
        missing: list[str] = []

        for metric in wanted:
            concepts = CONCEPT_MAP.get(metric)
            if not concepts:
                continue
            extracted = None
            for concept in concepts:
                extracted = self._extract_concept(facts, concept, metric)
                if extracted:
                    resolved[metric] = concept
                    break
            if not extracted:
                missing.append(metric)
                continue
            points.extend(extracted)

        if not points:
            raise DataUnavailable(f"{self.name}: no usable facts for {symbol}")
        return self.result(
            Capability.FUNDAMENTALS, points, url=url, missing=tuple(missing),
            partial=bool(missing),
            notes=(f"concepts: {', '.join(f'{k}={v}' for k, v in sorted(resolved.items()))}",))

    def _extract_concept(self, facts: Mapping[str, Any], concept: str,
                         metric: str) -> list[dict[str, Any]]:
        for taxonomy in ("us-gaap", "ifrs-full", "dei"):
            block = (facts.get(taxonomy) or {}).get(concept)
            if not block:
                continue
            units = block.get("units") or {}
            unit_key = next((u for u in ("USD", "USD/shares", "shares", "pure")
                             if u in units), None)
            if unit_key is None:
                unit_key = next(iter(units), None)
            if unit_key is None:
                continue
            out: list[dict[str, Any]] = []
            for item in units[unit_key]:
                value = item.get("val")
                end = item.get("end")
                filed = item.get("filed")
                form = item.get("form")
                if value is None or not end or not filed:
                    continue
                if form in _ANNUAL_FORMS:
                    period = "annual"
                elif form in _QUARTERLY_FORMS:
                    period = "quarterly"
                else:
                    continue
                # Annual forms also carry quarterly-length facts; keep only
                # durations consistent with the declared period.
                start = item.get("start")
                if start and _STATEMENT_BY_METRIC.get(metric) in ("income", "cashflow"):
                    span = (to_date(end) - to_date(start)).days
                    if period == "annual" and not (330 <= span <= 400):
                        continue
                    if period == "quarterly" and not (60 <= span <= 120):
                        continue
                out.append({
                    "metric": metric,
                    "statement": _STATEMENT_BY_METRIC.get(metric, "derived"),
                    "period": period,
                    "period_start": to_date(start) if start else None,
                    "period_end": to_date(end),
                    "fiscal_year": item.get("fy"),
                    "fiscal_period": item.get("fp"),
                    "value": float(value),
                    "unit": unit_key,
                    "filed_date": to_date(filed),
                    "accession": item.get("accn"),
                    "form": form,
                    "concept": concept,
                    "quality": DataQuality.EXCELLENT,
                })
            if out:
                return out
        return []
