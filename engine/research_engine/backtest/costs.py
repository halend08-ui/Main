"""Transaction cost and liquidity model.

Backtests that ignore costs are marketing, not research. The model here charges:

* **commission** in basis points of notional;
* **spread/slippage** in basis points, wider for crypto and for small caps;
* **market impact** that grows with participation in daily volume, because a
  position worth 30% of a day's trading does not fill at the close price;
* **borrow costs** for shorts (when short simulation is enabled).

It also refuses trades that are simply not executable at the modelled size, and
records the refusal instead of silently filling them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from research_engine.core.numeric import clamp


@dataclass(frozen=True, slots=True)
class CostModel:
    commission_bps: float = 5.0
    spread_bps: float = 10.0
    crypto_spread_bps: float = 30.0
    #: Impact coefficient in the square-root law: impact = k * sigma * sqrt(Q/ADV)
    impact_coefficient: float = 0.6
    max_participation: float = 0.10
    min_liquidity_multiple: float = 20.0
    short_borrow_annual_bps: float = 300.0
    #: Opt-in for price series that carry no volume (some vendor extracts and
    #: most monthly data). Fills are then ASSUMED possible and charged a penalty
    #: spread. This weakens the result and every run that uses it says so.
    allow_unknown_liquidity: bool = False
    unknown_liquidity_penalty_bps: float = 25.0

    def spread_for(self, *, is_crypto: bool, market_cap: float | None) -> float:
        if is_crypto:
            return self.crypto_spread_bps
        if market_cap is not None:
            if market_cap < 3e8:
                return self.spread_bps * 4      # micro-cap spreads are far wider
            if market_cap < 2e9:
                return self.spread_bps * 2
        return self.spread_bps

    def estimate(self, *, notional: float, adv: float | None,
                 volatility: float | None = None, is_crypto: bool = False,
                 market_cap: float | None = None,
                 holding_days: int = 0, is_short: bool = False) -> dict[str, Any]:
        """Total round-trip-side cost in currency plus an executability verdict."""
        notional = abs(float(notional))
        spread_bps = self.spread_for(is_crypto=is_crypto, market_cap=market_cap)
        commission = notional * self.commission_bps / 10_000
        spread = notional * spread_bps / 10_000

        impact = 0.0
        participation = None
        executable = True
        reason = ""
        if adv and adv > 0:
            participation = notional / adv
            daily_vol = (volatility or 0.35) / math.sqrt(252)
            impact = (notional * self.impact_coefficient * daily_vol
                      * math.sqrt(min(participation, 1.0)))
            if participation > self.max_participation:
                executable = False
                reason = (f"order is {participation:.0%} of average daily volume "
                          f"(cap {self.max_participation:.0%})")
        elif self.allow_unknown_liquidity:
            spread += notional * self.unknown_liquidity_penalty_bps / 10_000
            reason = ("no volume data: fills assumed under "
                      "allow_unknown_liquidity, with a penalty spread. Position "
                      "sizing is unvalidated and the result overstates tradability")
        else:
            executable = False
            reason = "no volume data: executability cannot be established"

        borrow = 0.0
        if is_short and holding_days:
            borrow = notional * self.short_borrow_annual_bps / 10_000 * holding_days / 365

        total = commission + spread + impact + borrow
        return {"total": total, "commission": commission, "spread": spread,
                "impact": impact, "borrow": borrow,
                "bps": (total / notional * 10_000) if notional else 0.0,
                "participation": participation, "executable": executable,
                "reason": reason}

    def max_position(self, adv: float | None) -> float | None:
        """Largest notional that respects the liquidity constraint."""
        if not adv or adv <= 0:
            return None
        return adv * self.max_participation
