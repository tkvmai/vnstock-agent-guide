"""Self-contained indicator math (RSI, MACD, ATR, MA, slope).

Implemented in plain numpy/pandas so the scoring engine stays pure and unit-test
deterministic, with no dependency on a live vnstock_ta instance. Formulas match
the standard definitions referenced by Spec RevC (Wilder RSI-14, MACD 12/26/9,
ATR via true range).
"""

import numpy as np
import pandas as pd


def sma(series: pd.Series, length: int) -> float:
    """Simple moving average of the last ``length`` values (NaN if too short)."""
    if len(series) < length:
        return float("nan")
    return float(series.iloc[-length:].mean())


def wilder_rsi(close: pd.Series, length: int = 14) -> float:
    """Wilder's RSI of the final bar. Returns NaN if insufficient data."""
    if len(close) < length + 1:
        return float("nan")
    delta = close.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder smoothing via EMA with alpha = 1/length
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def macd_histogram(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> float:
    """MACD histogram (macd_line - signal_line) of the final bar."""
    if len(close) < slow + signal:
        return float("nan")
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return float((macd_line - signal_line).iloc[-1])


def true_range(df: pd.DataFrame) -> pd.Series:
    """True range per session: max(h-l, |h-prev_close|, |l-prev_close|)."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def atr(df: pd.DataFrame, length: int) -> float:
    """Average true range over the last ``length`` completed sessions."""
    tr = true_range(df).dropna()
    if len(tr) < length:
        return float("nan")
    return float(tr.iloc[-length:].mean())
