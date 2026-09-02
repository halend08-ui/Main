"""Technical indicators.

Two rules distinguish this from a typical indicator library:

1. **Causality.** ``out[i]`` uses only ``values[:i+1]``. Warm-up periods are
   ``NaN``, never back-filled -- a back-filled warm-up is look-ahead bias.
2. **Honesty about sample size.** Every indicator returns ``NaN`` until it has
   its full window; there is no "partial window" approximation that quietly
   changes meaning.

Technical evidence never determines a recommendation on its own; it enters the
ensemble as one voice among several (see ``analysis.ensemble``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from research_engine.core.series import PriceSeries

NAN = float("nan")


def _prep(values: Sequence[float] | np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1:
        raise ValueError("expected a one-dimensional series")
    return arr


def sma(values: Sequence[float], window: int) -> np.ndarray:
    """Simple moving average; first ``window-1`` entries are NaN."""
    arr = _prep(values)
    n = arr.size
    out = np.full(n, NAN)
    if window <= 0:
        raise ValueError("window must be positive")
    if n < window:
        return out
    # cumulative-sum trick, but NaN-aware: any NaN in the window invalidates it
    valid = np.isfinite(arr)
    filled = np.where(valid, arr, 0.0)
    csum = np.concatenate(([0.0], np.cumsum(filled)))
    cvalid = np.concatenate(([0], np.cumsum(valid.astype(int))))
    for i in range(window - 1, n):
        lo = i - window + 1
        if cvalid[i + 1] - cvalid[lo] == window:
            out[i] = (csum[i + 1] - csum[lo]) / window
    return out


def ema(values: Sequence[float], window: int) -> np.ndarray:
    """Exponential moving average seeded with the first full SMA."""
    arr = _prep(values)
    n = arr.size
    out = np.full(n, NAN)
    if window <= 0:
        raise ValueError("window must be positive")
    if n < window:
        return out
    alpha = 2.0 / (window + 1.0)
    seed_slice = arr[:window]
    if not np.all(np.isfinite(seed_slice)):
        return out
    out[window - 1] = float(np.mean(seed_slice))
    for i in range(window, n):
        if not np.isfinite(arr[i]):
            out[i] = out[i - 1]        # carry forward; a gap is not a price move
            continue
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def wilder_smooth(values: Sequence[float], window: int) -> np.ndarray:
    """Wilder's smoothing (used by RSI/ATR/ADX): alpha = 1/window."""
    arr = _prep(values)
    n = arr.size
    out = np.full(n, NAN)
    if n < window or window <= 0:
        return out
    seed = arr[:window]
    if not np.all(np.isfinite(seed)):
        return out
    out[window - 1] = float(np.mean(seed))
    for i in range(window, n):
        prev = out[i - 1]
        cur = arr[i] if np.isfinite(arr[i]) else prev
        out[i] = prev + (cur - prev) / window
    return out


def rsi(values: Sequence[float], window: int = 14) -> np.ndarray:
    """Wilder's RSI in 0..100. NaN until ``window`` changes are available."""
    arr = _prep(values)
    n = arr.size
    out = np.full(n, NAN)
    if n <= window:
        return out
    delta = np.diff(arr)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    avg_gain = wilder_smooth(gains, window)
    avg_loss = wilder_smooth(losses, window)
    for i in range(window, n):
        g, l = avg_gain[i - 1], avg_loss[i - 1]
        if not (np.isfinite(g) and np.isfinite(l)):
            continue
        if l == 0:
            out[i] = 100.0 if g > 0 else 50.0
        else:
            rs = g / l
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


@dataclass(frozen=True, slots=True)
class MacdResult:
    macd: np.ndarray
    signal: np.ndarray
    histogram: np.ndarray


def macd(values: Sequence[float], fast: int = 12, slow: int = 26,
         signal_window: int = 9) -> MacdResult:
    if fast >= slow:
        raise ValueError("fast window must be shorter than slow window")
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    line = fast_ema - slow_ema
    sig = ema(np.where(np.isfinite(line), line, NAN), signal_window)
    # ema() needs a clean seed; recompute the signal only over the valid region
    valid_from = int(np.argmax(np.isfinite(line))) if np.any(np.isfinite(line)) else 0
    sig = np.full(line.size, NAN)
    tail = line[valid_from:]
    if tail.size >= signal_window:
        sig[valid_from:] = ema(tail, signal_window)
    return MacdResult(macd=line, signal=sig, histogram=line - sig)


def true_range(high: Sequence[float], low: Sequence[float],
               close: Sequence[float]) -> np.ndarray:
    h, l, c = _prep(high), _prep(low), _prep(close)
    n = c.size
    out = np.full(n, NAN)
    for i in range(1, n):
        candidates = []
        if np.isfinite(h[i]) and np.isfinite(l[i]):
            candidates.append(h[i] - l[i])
        if np.isfinite(h[i]) and np.isfinite(c[i - 1]):
            candidates.append(abs(h[i] - c[i - 1]))
        if np.isfinite(l[i]) and np.isfinite(c[i - 1]):
            candidates.append(abs(l[i] - c[i - 1]))
        if candidates:
            out[i] = max(candidates)
        elif np.isfinite(c[i]) and np.isfinite(c[i - 1]):
            out[i] = abs(c[i] - c[i - 1])      # closes only: still a valid range
    return out


def atr(high: Sequence[float], low: Sequence[float], close: Sequence[float],
        window: int = 14) -> np.ndarray:
    """Average True Range (Wilder). NaN until a full window of ranges exists."""
    return _atr_from_tr(true_range(high, low, close), window)


def _atr_from_tr(tr: np.ndarray, window: int) -> np.ndarray:
    n = tr.size
    out = np.full(n, NAN)
    finite = np.isfinite(tr)
    if finite.sum() < window:
        return out
    start = int(np.argmax(finite))
    seed_end = start + window
    if seed_end > n:
        return out
    out[seed_end - 1] = float(np.nanmean(tr[start:seed_end]))
    for i in range(seed_end, n):
        prev = out[i - 1]
        cur = tr[i] if np.isfinite(tr[i]) else prev
        out[i] = prev + (cur - prev) / window
    return out


@dataclass(frozen=True, slots=True)
class BollingerResult:
    middle: np.ndarray
    upper: np.ndarray
    lower: np.ndarray
    bandwidth: np.ndarray
    percent_b: np.ndarray


def bollinger(values: Sequence[float], window: int = 20,
              num_std: float = 2.0) -> BollingerResult:
    arr = _prep(values)
    mid = sma(arr, window)
    n = arr.size
    sd = np.full(n, NAN)
    for i in range(window - 1, n):
        chunk = arr[i - window + 1:i + 1]
        if np.all(np.isfinite(chunk)):
            sd[i] = float(np.std(chunk, ddof=0))
    upper = mid + num_std * sd
    lower = mid - num_std * sd
    with np.errstate(divide="ignore", invalid="ignore"):
        bandwidth = np.where(mid != 0, (upper - lower) / mid, NAN)
        span = upper - lower
        percent_b = np.where(span > 0, (arr - lower) / span, NAN)
    return BollingerResult(mid, upper, lower, bandwidth, percent_b)


def adx(high: Sequence[float], low: Sequence[float], close: Sequence[float],
        window: int = 14) -> dict[str, np.ndarray]:
    """Average Directional Index with +DI/-DI. Measures trend *strength*."""
    h, l, c = _prep(high), _prep(low), _prep(close)
    n = c.size
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        if not (np.isfinite(h[i]) and np.isfinite(h[i - 1])
                and np.isfinite(l[i]) and np.isfinite(l[i - 1])):
            continue
        up = h[i] - h[i - 1]
        down = l[i - 1] - l[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0
    tr = true_range(h, l, c)
    atr_v = _atr_from_tr(tr, window)
    plus_sm = _atr_from_tr(np.where(np.arange(n) == 0, NAN, plus_dm), window)
    minus_sm = _atr_from_tr(np.where(np.arange(n) == 0, NAN, minus_dm), window)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * plus_sm / atr_v
        minus_di = 100.0 * minus_sm / atr_v
        denom = plus_di + minus_di
        dx = np.where(denom > 0, 100.0 * np.abs(plus_di - minus_di) / denom, NAN)
    adx_v = np.full(n, NAN)
    finite = np.isfinite(dx)
    if finite.sum() >= window:
        start = int(np.argmax(finite))
        seed_end = start + window
        if seed_end <= n:
            adx_v[seed_end - 1] = float(np.nanmean(dx[start:seed_end]))
            for i in range(seed_end, n):
                cur = dx[i] if np.isfinite(dx[i]) else adx_v[i - 1]
                adx_v[i] = adx_v[i - 1] + (cur - adx_v[i - 1]) / window
    return {"adx": adx_v, "plus_di": plus_di, "minus_di": minus_di}


def rolling_volatility(returns: Sequence[float], window: int = 20,
                       periods_per_year: int = 252) -> np.ndarray:
    """Annualised rolling standard deviation of returns."""
    arr = _prep(returns)
    n = arr.size
    out = np.full(n, NAN)
    for i in range(window - 1, n):
        chunk = arr[i - window + 1:i + 1]
        chunk = chunk[np.isfinite(chunk)]
        if chunk.size == window:
            out[i] = float(np.std(chunk, ddof=1)) * np.sqrt(periods_per_year)
    return out


def obv(close: Sequence[float], volume: Sequence[float]) -> np.ndarray:
    """On-balance volume: cumulative signed volume."""
    c, v = _prep(close), _prep(volume)
    n = c.size
    out = np.full(n, NAN)
    if n == 0:
        return out
    total = 0.0
    out[0] = 0.0
    for i in range(1, n):
        if np.isfinite(c[i]) and np.isfinite(c[i - 1]) and np.isfinite(v[i]):
            if c[i] > c[i - 1]:
                total += v[i]
            elif c[i] < c[i - 1]:
                total -= v[i]
        out[i] = total
    return out


def money_flow_index(high: Sequence[float], low: Sequence[float],
                     close: Sequence[float], volume: Sequence[float],
                     window: int = 14) -> np.ndarray:
    h, l, c, v = _prep(high), _prep(low), _prep(close), _prep(volume)
    n = c.size
    typical = np.where(np.isfinite(h) & np.isfinite(l), (h + l + c) / 3.0, c)
    flow = typical * v
    out = np.full(n, NAN)
    for i in range(window, n):
        pos = neg = 0.0
        ok = True
        for j in range(i - window + 1, i + 1):
            if not (np.isfinite(typical[j]) and np.isfinite(typical[j - 1])
                    and np.isfinite(flow[j])):
                ok = False
                break
            if typical[j] > typical[j - 1]:
                pos += flow[j]
            elif typical[j] < typical[j - 1]:
                neg += flow[j]
        if not ok:
            continue
        if neg == 0:
            out[i] = 100.0 if pos > 0 else 50.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + pos / neg)
    return out


def donchian(high: Sequence[float], low: Sequence[float],
             window: int = 55) -> dict[str, np.ndarray]:
    """Rolling highest high / lowest low, EXCLUDING the current bar.

    Excluding the current bar matters: a breakout must be measured against the
    prior range, otherwise every new high trivially equals its own channel top.
    """
    h, l = _prep(high), _prep(low)
    n = h.size
    upper = np.full(n, NAN)
    lower = np.full(n, NAN)
    for i in range(window, n):
        hw = h[i - window:i]
        lw = l[i - window:i]
        if np.all(np.isfinite(hw)) and np.all(np.isfinite(lw)):
            upper[i] = float(np.max(hw))
            lower[i] = float(np.min(lw))
    return {"upper": upper, "lower": lower}


def drawdown_series(values: Sequence[float]) -> np.ndarray:
    """Drawdown from the running peak (<= 0)."""
    arr = _prep(values)
    out = np.full(arr.size, NAN)
    peak = NAN
    for i, v in enumerate(arr):
        if not np.isfinite(v):
            continue
        peak = v if not np.isfinite(peak) else max(peak, v)
        out[i] = v / peak - 1.0 if peak > 0 else NAN
    return out


def relative_strength(asset: Sequence[float], benchmark: Sequence[float],
                      window: int = 63) -> np.ndarray:
    """Asset return minus benchmark return over ``window`` periods."""
    a, b = _prep(asset), _prep(benchmark)
    n = min(a.size, b.size)
    out = np.full(n, NAN)
    for i in range(window, n):
        if all(np.isfinite(x) and x > 0 for x in (a[i], a[i - window], b[i], b[i - window])):
            out[i] = (a[i] / a[i - window]) - (b[i] / b[i - window])
    return out


def compute_all(series: PriceSeries, *, config: dict | None = None) -> dict[str, np.ndarray]:
    """Compute the standard indicator set for a price series."""
    cfg = config or {}
    close = series.adj_close
    out: dict[str, np.ndarray] = {}
    for window in cfg.get("sma_windows", (20, 50, 100, 200)):
        out[f"sma_{window}"] = sma(close, int(window))
    for window in cfg.get("ema_windows", (12, 26, 50)):
        out[f"ema_{window}"] = ema(close, int(window))
    out["rsi"] = rsi(close, int(cfg.get("rsi_window", 14)))
    fast, slow, sig = cfg.get("macd", (12, 26, 9))
    macd_res = macd(close, int(fast), int(slow), int(sig))
    out["macd"] = macd_res.macd
    out["macd_signal"] = macd_res.signal
    out["macd_hist"] = macd_res.histogram
    out["atr"] = atr(series.high, series.low, series.close,
                     int(cfg.get("atr_window", 14)))
    bb_window, bb_std = cfg.get("bollinger", (20, 2.0))
    bb = bollinger(close, int(bb_window), float(bb_std))
    out["bb_upper"], out["bb_lower"] = bb.upper, bb.lower
    out["bb_bandwidth"], out["bb_percent_b"] = bb.bandwidth, bb.percent_b
    adx_res = adx(series.high, series.low, series.close,
                  int(cfg.get("adx_window", 14)))
    out.update({f"{k}": v for k, v in adx_res.items()})
    out["obv"] = obv(close, series.volume)
    out["mfi"] = money_flow_index(series.high, series.low, series.close, series.volume)
    channel = donchian(series.high, series.low, int(cfg.get("donchian_window", 55)))
    out["donchian_upper"], out["donchian_lower"] = channel["upper"], channel["lower"]
    out["drawdown"] = drawdown_series(close)
    returns = series.returns()
    padded = np.concatenate(([NAN], returns)) if returns.size else np.full(close.size, NAN)
    out["vol_20d"] = rolling_volatility(padded, 20, series.periods_per_year)
    out["vol_60d"] = rolling_volatility(padded, 60, series.periods_per_year)
    return out
