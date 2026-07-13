"""Layer 1 — Hard Filter and Market Regime Gate (Spec RevC section 2).

Quickly rejects stocks that fail minimum tradability conditions before any
expensive scoring. Also computes the market-wide regime gate from the VN-Index.
"""

import numpy as np
import pandas as pd

from . import indicators as ind
import config


def check_market_regime(vnindex_close: pd.Series):
    """Market Regime Gate (Spec item 10).

    Returns (regime, ratio, message) where regime is 'ok' | 'caution' | 'blocked'.
    """
    if vnindex_close is None or len(vnindex_close) < config.LOOKBACK_MA20:
        return "ok", float("nan"), "Không đủ dữ liệu VN-Index — chạy bình thường."
    ma20 = ind.sma(vnindex_close, config.LOOKBACK_MA20)
    ma5 = ind.sma(vnindex_close, 5)
    close = float(vnindex_close.iloc[-1])
    ratio = close / ma20 if ma20 else float("nan")
    if ratio < 0.97 and ma5 < ma20:
        return "blocked", ratio, "Thị trường downtrend rõ ràng — screener tạm dừng."
    if ratio < 1.00:
        return "caution", ratio, "MARKET CAUTION — thị trường choppy, thận trọng."
    return "ok", ratio, "Thị trường uptrend — chạy bình thường."


def gtgd_series(hist: pd.DataFrame, length: int) -> pd.Series:
    """Daily turnover (close × volume, VND) for the last ``length`` sessions."""
    g = (hist["close"] * hist["volume"]).iloc[-length:]
    return g


def compute_layer1_metrics(hist: pd.DataFrame, live: dict) -> dict:
    """Compute reusable Layer-1 metrics (GTGD20, CV, intraday_ratio)."""
    g20 = gtgd_series(hist, config.LOOKBACK_GTGD)
    gtgd20 = float(g20.mean())
    cv = float(g20.std(ddof=0) / g20.mean() * 100) if g20.mean() else float("inf")

    minutes = max(1.0, float(live.get("minutes_elapsed", config.TRADING_MINUTES)))
    time_ratio = min(1.0, minutes / config.TRADING_MINUTES)
    gtgd_intraday = float(live["close"] * live.get("volume", 0))
    gtgd_expected = gtgd20 * time_ratio
    intraday_ratio = (gtgd_intraday / gtgd_expected * 100) if gtgd_expected else 0.0

    return {
        "gtgd20": gtgd20,
        "cv": cv,
        "intraday_ratio": intraday_ratio,
        "time_ratio": time_ratio,
    }


def _resolve_exchanges(allowed_exchanges):
    """Normalise a user exchange selection to a matching set (HOSE implies HSX)."""
    if not allowed_exchanges:
        return set(config.ALLOWED_EXCHANGES)
    allowed = {e.upper() for e in allowed_exchanges}
    if "HOSE" in allowed or "HSX" in allowed:
        allowed |= {"HOSE", "HSX"}
    return allowed


def passes_hard_filter(hist: pd.DataFrame, live: dict, exchange: str = None,
                       status: str = None, metrics: dict = None,
                       min_price: float = None, min_gtgd20: float = None,
                       allowed_exchanges=None):
    """Run the 9 per-stock hard filters (regime gate is checked separately).

    ``min_price``, ``min_gtgd20``, ``allowed_exchanges`` override config defaults
    when provided (so the dashboard can tune them live).

    Returns (passed: bool, reason: str, metrics: dict).
    """
    min_price = config.MIN_PRICE if min_price is None else min_price
    min_gtgd20 = config.MIN_GTGD20 if min_gtgd20 is None else min_gtgd20
    allowed = _resolve_exchanges(allowed_exchanges)

    # #1 Exchange filter
    if exchange and exchange.upper() not in allowed:
        return False, f"Sàn {exchange} không được chọn", {}

    # #2 Trading status (skipped when unknown)
    if status and any(flag in str(status).upper() for flag in ("ST", "HL", "SUSPEND", "HALT")):
        return False, f"Trạng thái cảnh báo/kiểm soát: {status}", {}

    # #3 Data history >= 60 sessions
    if hist is None or len(hist) < config.MIN_SESSIONS:
        return False, f"Chỉ có {0 if hist is None else len(hist)} phiên (<{config.MIN_SESSIONS})", {}

    # #9 Clean data
    if hist[["close", "volume"]].iloc[-config.LOOKBACK_GTGD:].isna().any().any():
        return False, "Dữ liệu OHLCV thiếu (null) trong 20 phiên gần nhất", {}

    close_today = float(live["close"])

    # #4 Minimum price
    if close_today < min_price:
        return False, f"Giá {close_today:,.0f} < {min_price:,.0f} VND", {}

    m = metrics or compute_layer1_metrics(hist, live)

    # #5 GTGD20 threshold
    if m["gtgd20"] < min_gtgd20:
        return False, f"GTGD20 {m['gtgd20']/1e9:.1f}B < {min_gtgd20/1e9:.0f}B", m

    # #6 Intraday active >= 30% of expected
    if m["intraday_ratio"] < config.MIN_INTRADAY_ACTIVE_PCT:
        return False, f"Intraday active {m['intraday_ratio']:.0f}% < {config.MIN_INTRADAY_ACTIVE_PCT:.0f}%", m

    # #7 Not at ceiling / floor
    ceiling, floor = live.get("ceiling"), live.get("floor")
    if ceiling and abs(close_today - ceiling) / ceiling < 0.001:
        return False, "Đang ở giá trần — không mua được", m
    if floor and abs(close_today - floor) / floor < 0.001:
        return False, "Đang ở giá sàn — không thoát được", m

    # #8 CV cap
    if m["cv"] >= config.CV_CAP:
        return False, f"CV {m['cv']:.0f}% >= {config.CV_CAP:.0f}% (thanh khoản bất thường)", m

    return True, "OK", m


# ── Split static (once/day) vs live (every 5 min) — Spec update-frequency table ──
def static_metrics(hist: pd.DataFrame) -> dict:
    """EOD metrics from completed sessions only (GTGD20, CV) — computed once/day."""
    g20 = gtgd_series(hist, config.LOOKBACK_GTGD)
    mean = g20.mean()
    return {
        "gtgd20": float(mean) if mean == mean else 0.0,
        "cv": float(g20.std(ddof=0) / mean * 100) if mean else float("inf"),
    }


def passes_static(hist: pd.DataFrame, last_close: float, exchange: str = None,
                  status: str = None, min_price: float = None, min_gtgd20: float = None,
                  allowed_exchanges=None, metrics: dict = None):
    """The 7 EOD/static Layer-1 filters (run once/day). Live filters #6/#7 are
    applied separately by ``passes_live``. Returns (passed, reason, metrics)."""
    min_price = config.MIN_PRICE if min_price is None else min_price
    min_gtgd20 = config.MIN_GTGD20 if min_gtgd20 is None else min_gtgd20
    allowed = _resolve_exchanges(allowed_exchanges)

    if exchange and exchange.upper() not in allowed:                       # #1
        return False, f"Sàn {exchange} không được chọn", {}
    if status and any(f in str(status).upper() for f in ("ST", "HL", "SUSPEND", "HALT")):  # #2
        return False, f"Trạng thái cảnh báo/kiểm soát: {status}", {}
    if hist is None or len(hist) < config.MIN_SESSIONS:                    # #3
        return False, f"Chỉ có {0 if hist is None else len(hist)} phiên (<{config.MIN_SESSIONS})", {}
    if hist[["close", "volume"]].iloc[-config.LOOKBACK_GTGD:].isna().any().any():  # #9
        return False, "Dữ liệu OHLCV thiếu (null) trong 20 phiên gần nhất", {}
    if last_close is not None and last_close < min_price:                  # #4
        return False, f"Giá {last_close:,.0f} < {min_price:,.0f} VND", {}

    m = metrics or static_metrics(hist)
    if m["gtgd20"] < min_gtgd20:                                           # #5
        return False, f"GTGD20 {m['gtgd20']/1e9:.1f}B < {min_gtgd20/1e9:.0f}B", m
    if m["cv"] >= config.CV_CAP:                                           # #8
        return False, f"CV {m['cv']:.0f}% >= {config.CV_CAP:.0f}%", m
    return True, "OK", m


def passes_live(static_m: dict, live: dict):
    """Intraday Layer-1 filters #6 (intraday-active) and #7 (ceiling/floor),
    re-checked every 5 min. Returns (passed, reason, intraday_ratio, time_ratio)."""
    minutes = max(1.0, float(live.get("minutes_elapsed", config.TRADING_MINUTES)))
    time_ratio = min(1.0, minutes / config.TRADING_MINUTES)
    close = float(live["close"])
    gtgd_intraday = close * float(live.get("volume", 0))
    gtgd_expected = static_m["gtgd20"] * time_ratio
    intraday_ratio = (gtgd_intraday / gtgd_expected * 100) if gtgd_expected else 0.0

    if intraday_ratio < config.MIN_INTRADAY_ACTIVE_PCT:                    # #6
        return (False, f"Intraday active {intraday_ratio:.0f}% < {config.MIN_INTRADAY_ACTIVE_PCT:.0f}%",
                intraday_ratio, time_ratio)
    ceiling, floor = live.get("ceiling"), live.get("floor")               # #7
    if ceiling and abs(close - ceiling) / ceiling < 0.001:
        return False, "Đang ở giá trần — không mua được", intraday_ratio, time_ratio
    if floor and abs(close - floor) / floor < 0.001:
        return False, "Đang ở giá sàn — không thoát được", intraday_ratio, time_ratio
    return True, "OK", intraday_ratio, time_ratio
