"""Numeric helpers with an opinion about honesty.

Two rules are enforced here rather than in every call site:

1. **No fabricated values.** Every helper returns ``None`` when its inputs are
   insufficient. Nothing defaults to zero.
2. **No false precision.** ``round_sig`` / ``fmt_pct`` keep the number of
   reported digits proportional to what the estimate can actually support.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np

Number = float | int


def is_finite(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def clean(values: Iterable[object]) -> list[float]:
    """Drop ``None``/NaN/inf, preserving order."""
    return [float(v) for v in values if is_finite(v)]


def as_array(values: Sequence[object]) -> np.ndarray:
    """Convert to a float array where unusable entries become NaN (not 0)."""
    out = np.empty(len(values), dtype=float)
    for i, v in enumerate(values):
        out[i] = float(v) if is_finite(v) else np.nan
    return out


def safe_div(numerator: object, denominator: object, *,
             eps: float = 1e-12) -> float | None:
    """Divide, returning ``None`` for missing inputs or a ~zero denominator."""
    if not is_finite(numerator) or not is_finite(denominator):
        return None
    d = float(denominator)
    if abs(d) < eps:
        return None
    return float(numerator) / d


def pct_change(new: object, old: object) -> float | None:
    """Fractional change. ``None`` if either side is missing or ``old`` <= 0 in
    magnitude terms (percentage change off a zero or sign-flipping base is not
    meaningful and must not be reported)."""
    if not is_finite(new) or not is_finite(old):
        return None
    o = float(old)
    if o == 0:
        return None
    if o < 0:
        # A change measured off a negative base (e.g. negative earnings) is
        # ambiguous; callers must handle it explicitly instead of being handed
        # a sign-flipped number.
        return None
    return (float(new) - o) / o


def cagr(begin: object, end: object, years: float) -> float | None:
    """Compound annual growth rate; undefined for non-positive endpoints."""
    if not is_finite(begin) or not is_finite(end) or not is_finite(years):
        return None
    b, e, y = float(begin), float(end), float(years)
    if b <= 0 or e <= 0 or y <= 0:
        return None
    return (e / b) ** (1.0 / y) - 1.0


def mean(values: Iterable[object]) -> float | None:
    vals = clean(values)
    return sum(vals) / len(vals) if vals else None


def median(values: Iterable[object]) -> float | None:
    vals = sorted(clean(values))
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else 0.5 * (vals[mid - 1] + vals[mid])


def stdev(values: Iterable[object], *, ddof: int = 1) -> float | None:
    vals = clean(values)
    if len(vals) <= ddof:
        return None
    m = sum(vals) / len(vals)
    var = sum((v - m) ** 2 for v in vals) / (len(vals) - ddof)
    return math.sqrt(var)


def percentile(values: Iterable[object], q: float) -> float | None:
    """Linear-interpolation percentile with ``q`` in 0..1."""
    vals = sorted(clean(values))
    if not vals:
        return None
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be in [0, 1]")
    if len(vals) == 1:
        return vals[0]
    pos = q * (len(vals) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def percentile_rank(values: Iterable[object], x: object) -> float | None:
    """Fraction of ``values`` at or below ``x`` (0..1)."""
    vals = clean(values)
    if not vals or not is_finite(x):
        return None
    below = sum(1 for v in vals if v <= float(x))
    return below / len(vals)


def zscore(values: Iterable[object], x: object) -> float | None:
    vals = clean(values)
    if len(vals) < 3 or not is_finite(x):
        return None
    m = sum(vals) / len(vals)
    sd = stdev(vals)
    if sd is None or sd < 1e-12:
        return None
    return (float(x) - m) / sd


def robust_zscore(values: Iterable[object], x: object) -> float | None:
    """Median/MAD based z-score; far less sensitive to the outliers that are
    routine in financial data."""
    vals = clean(values)
    if len(vals) < 5 or not is_finite(x):
        return None
    med = median(vals)
    assert med is not None
    mad = median([abs(v - med) for v in vals])
    if mad is None or mad < 1e-12:
        return None
    return 0.6745 * (float(x) - med) / mad


def winsorize(values: Sequence[float], lower: float = 0.02,
              upper: float = 0.98) -> list[float]:
    """Clip extremes so a single bad print cannot dominate a cross-section."""
    vals = clean(values)
    if len(vals) < 5:
        return list(vals)
    lo = percentile(vals, lower)
    hi = percentile(vals, upper)
    if lo is None or hi is None:
        return list(vals)
    return [min(max(v, lo), hi) for v in vals]


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def logistic(x: float, *, k: float = 1.0, x0: float = 0.0) -> float:
    """Numerically stable logistic squashing."""
    z = k * (x - x0)
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def linear_score(x: object, worst: float, best: float) -> float | None:
    """Map ``x`` onto 0..1 between ``worst`` and ``best`` (either direction)."""
    if not is_finite(x):
        return None
    v = float(x)
    if worst == best:
        return None
    t = (v - worst) / (best - worst)
    return clamp(t, 0.0, 1.0)


def round_sig(x: object, sig: int = 3) -> float | None:
    """Round to ``sig`` significant digits. Guards against false precision."""
    if not is_finite(x):
        return None
    v = float(x)
    if v == 0:
        return 0.0
    digits = sig - int(math.floor(math.log10(abs(v)))) - 1
    return round(v, digits)


def fmt_pct(x: object, digits: int = 1) -> str:
    """Format a fraction as a percentage, or the explicit unavailable marker."""
    if not is_finite(x):
        return "n/a"
    return f"{float(x) * 100:.{digits}f}%"


def fmt_money(x: object, currency: str = "USD", digits: int = 2) -> str:
    if not is_finite(x):
        return "n/a"
    v = float(x)
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(v) >= cutoff:
            return f"{v / cutoff:,.2f}{suffix} {currency}"
    return f"{v:,.{digits}f} {currency}"


def geometric_mean(values: Iterable[object]) -> float | None:
    """Geometric mean of gross values (all must be > 0)."""
    vals = clean(values)
    if not vals or any(v <= 0 for v in vals):
        return None
    return math.exp(sum(math.log(v) for v in vals) / len(vals))


def correlation(a: Sequence[object], b: Sequence[object]) -> float | None:
    """Pearson correlation over pairwise-complete observations."""
    pairs = [(float(x), float(y)) for x, y in zip(a, b)
             if is_finite(x) and is_finite(y)]
    if len(pairs) < 5:
        return None
    xs = np.array([p[0] for p in pairs])
    ys = np.array([p[1] for p in pairs])
    sx, sy = xs.std(ddof=1), ys.std(ddof=1)
    if sx < 1e-15 or sy < 1e-15:
        return None
    return float(np.corrcoef(xs, ys)[0, 1])


def ols_beta_alpha(asset: Sequence[object],
                   benchmark: Sequence[object]) -> tuple[float, float] | None:
    """Return (beta, alpha_per_period) from a simple regression of asset on
    benchmark returns. ``None`` when there is not enough overlap."""
    pairs = [(float(x), float(y)) for x, y in zip(asset, benchmark)
             if is_finite(x) and is_finite(y)]
    if len(pairs) < 20:
        return None
    y = np.array([p[0] for p in pairs])
    x = np.array([p[1] for p in pairs])
    var = x.var(ddof=1)
    if var < 1e-18:
        return None
    beta = float(np.cov(y, x, ddof=1)[0, 1] / var)
    alpha = float(y.mean() - beta * x.mean())
    return beta, alpha
