"""Technical analysis tools: compute indicators on price data via vnstock_ta."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from utils.df_converter import to_claude_text
from utils.error_handler import handle_vnstock_error, validate_date

# Default period for indicators that accept a single length parameter.
# Indicators with complex or no period use None.
INDICATOR_DEFAULTS = {
    # Trend
    "sma": 20, "ema": 20, "wma": 10, "hma": 10, "smma": 7, "alma": 10,
    "vwma": 20, "adx": 14, "ichimoku": None, "psar": None, "supertrend": 10,
    "dm": 14, "linreg": 14, "aroon": 14,
    # Momentum
    "rsi": 14, "stoch": 14, "stochrsi": 14, "roc": 9, "ao": None, "cci": 14,
    "willr": 14, "tsi": None, "cmo": 9, "uo": None, "fisher": 9, "cg": 10,
    "kst": None, "macd": None,
    # Volatility
    "bbands": 20, "kc": 20, "atr": 14, "stdev": 14, "donchian": 20,
    "massi": None, "ui": 14, "squeeze": None, "squeeze_pro": None,
    "true_range": None,
    # Volume
    "obv": None, "cmf": 20, "ad": None, "vp": 10, "vwap": None,
    "pvo": None, "efi": 13, "eom": 14, "nvi": None, "mfi": 14,
    # Statistics
    "pivots": None, "mad": 30, "variance": 30, "hl2": None, "hlc3": None,
    "ohlc4": None, "midprice": 14, "decreasing": None, "increasing": None,
}

ALL_INDICATORS = sorted(INDICATOR_DEFAULTS.keys())


def _parse_indicator(indicator_str: str):
    """Parse 'rsi_14' → ('rsi', 14) or 'macd' → ('macd', None)."""
    parts = indicator_str.lower().strip().split("_", 1)
    name = parts[0]
    period = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else INDICATOR_DEFAULTS.get(name)
    return name, period


def _assign(result_df: pd.DataFrame, col_name: str, result):
    """Assign a Series or DataFrame result into result_df."""
    if isinstance(result, pd.DataFrame):
        for c in result.columns:
            result_df[c] = result[c].values
    else:
        result_df[col_name] = result


def _compute_indicator(ta, name: str, period, result_df: pd.DataFrame):
    """Dispatch one indicator by name to the correct vnstock_ta method."""
    col = f"{name}_{period}" if period else name

    # ── TREND ────────────────────────────────────────────────────────────────
    if name == "sma":
        _assign(result_df, col, ta.trend.sma(length=period or 20))
    elif name == "ema":
        _assign(result_df, col, ta.trend.ema(length=period or 20))
    elif name == "wma":
        _assign(result_df, col, ta.trend.wma(length=period or 10))
    elif name == "hma":
        _assign(result_df, col, ta.trend.hma(length=period or 10))
    elif name == "smma":
        _assign(result_df, col, ta.trend.smma(length=period or 7))
    elif name == "alma":
        _assign(result_df, col, ta.trend.alma(length=period or 10))
    elif name == "vwma":
        _assign(result_df, col, ta.trend.vwma(length=period or 20))
    elif name == "adx":
        _assign(result_df, col, ta.trend.adx(length=period or 14))
    elif name == "ichimoku":
        _assign(result_df, col, ta.trend.ichimoku())
    elif name == "psar":
        _assign(result_df, col, ta.trend.psar())
    elif name == "supertrend":
        _assign(result_df, col, ta.trend.supertrend(length=period or 10))
    elif name == "dm":
        _assign(result_df, col, ta.trend.dm(length=period or 14))
    elif name == "linreg":
        _assign(result_df, col, ta.trend.linreg(length=period or 14))
    elif name == "aroon":
        _assign(result_df, col, ta.trend.aroon(length=period or 14))

    # ── MOMENTUM ─────────────────────────────────────────────────────────────
    elif name == "rsi":
        _assign(result_df, col, ta.momentum.rsi(length=period or 14))
    elif name == "stoch":
        _assign(result_df, col, ta.momentum.stoch(k=period or 14))
    elif name == "stochrsi":
        _assign(result_df, col, ta.momentum.stochrsi(length=period or 14))
    elif name == "roc":
        _assign(result_df, col, ta.momentum.roc(length=period or 9))
    elif name == "ao":
        _assign(result_df, col, ta.momentum.ao())
    elif name == "cci":
        _assign(result_df, col, ta.momentum.cci(length=period or 14))
    elif name == "willr":
        _assign(result_df, col, ta.momentum.willr(length=period or 14))
    elif name == "tsi":
        _assign(result_df, col, ta.momentum.tsi())
    elif name == "cmo":
        _assign(result_df, col, ta.momentum.cmo(length=period or 9))
    elif name == "uo":
        _assign(result_df, col, ta.momentum.uo())
    elif name == "fisher":
        _assign(result_df, col, ta.momentum.fisher(length=period or 9))
    elif name == "cg":
        _assign(result_df, col, ta.momentum.cg(length=period or 10))
    elif name == "kst":
        _assign(result_df, col, ta.momentum.kst())
    elif name == "macd":
        _assign(result_df, col, ta.momentum.macd())

    # ── VOLATILITY ────────────────────────────────────────────────────────────
    elif name == "bbands":
        _assign(result_df, col, ta.volatility.bbands(length=period or 20))
    elif name == "kc":
        _assign(result_df, col, ta.volatility.kc(length=period or 20))
    elif name == "atr":
        _assign(result_df, col, ta.volatility.atr(length=period or 14))
    elif name == "stdev":
        _assign(result_df, col, ta.volatility.stdev(length=period or 14))
    elif name == "donchian":
        _assign(result_df, col, ta.volatility.donchian(lower_length=period or 20, upper_length=period or 20))
    elif name == "massi":
        _assign(result_df, col, ta.volatility.massi())
    elif name == "ui":
        _assign(result_df, col, ta.volatility.ui(length=period or 14))
    elif name == "squeeze":
        _assign(result_df, col, ta.volatility.squeeze())
    elif name == "squeeze_pro":
        _assign(result_df, col, ta.volatility.squeeze_pro())
    elif name == "true_range":
        _assign(result_df, col, ta.volatility.true_range())

    # ── VOLUME ────────────────────────────────────────────────────────────────
    elif name == "obv":
        _assign(result_df, col, ta.volume.obv())
    elif name == "cmf":
        _assign(result_df, col, ta.volume.cmf(length=period or 20))
    elif name == "ad":
        _assign(result_df, col, ta.volume.ad())
    elif name == "vp":
        _assign(result_df, col, ta.volume.vp(width=period or 10))
    elif name == "vwap":
        _assign(result_df, col, ta.volume.vwap())
    elif name == "pvo":
        _assign(result_df, col, ta.volume.pvo())
    elif name == "efi":
        _assign(result_df, col, ta.volume.efi(length=period or 13))
    elif name == "eom":
        _assign(result_df, col, ta.volume.eom(length=period or 14))
    elif name == "nvi":
        _assign(result_df, col, ta.volume.nvi())
    elif name == "mfi":
        _assign(result_df, col, ta.volume.mfi(length=period or 14))

    # ── STATISTICS ────────────────────────────────────────────────────────────
    elif name == "pivots":
        _assign(result_df, col, ta.statistics.pivots())
    elif name == "mad":
        _assign(result_df, col, ta.statistics.mad(length=period or 30))
    elif name == "variance":
        _assign(result_df, col, ta.statistics.variance(length=period or 30))
    elif name == "hl2":
        _assign(result_df, col, ta.statistics.hl2())
    elif name == "hlc3":
        _assign(result_df, col, ta.statistics.hlc3())
    elif name == "ohlc4":
        _assign(result_df, col, ta.statistics.ohlc4())
    elif name == "midprice":
        _assign(result_df, col, ta.statistics.midprice(length=period or 14))
    elif name == "decreasing":
        _assign(result_df, col, ta.statistics.decreasing())
    elif name == "increasing":
        _assign(result_df, col, ta.statistics.increasing())

    else:
        result_df[f"{col}_unsupported"] = None


def get_technical_indicators(
    symbol: str,
    start: str,
    end: str,
    indicators: list = None,
) -> str:
    """
    Calculate technical indicators for a stock symbol using vnstock_ta.

    Args:
        symbol: Stock ticker (e.g. 'TCB')
        start: Start date YYYY-MM-DD
        end: End date YYYY-MM-DD
        indicators: List of indicator strings formatted as 'name' or 'name_period'.
            Examples: ['sma_20', 'ema_50', 'rsi_14', 'macd', 'bbands_20', 'atr_14']

            TREND (14): sma, ema, wma, hma, smma, alma, vwma, adx, ichimoku,
                        psar, supertrend, dm, linreg, aroon
            MOMENTUM (14): rsi, stoch, stochrsi, roc, ao, cci, willr, tsi,
                           cmo, uo, fisher, cg, kst, macd
            VOLATILITY (10): bbands, kc, atr, stdev, donchian, massi, ui,
                             squeeze, squeeze_pro, true_range
            VOLUME (10): obv, cmf, ad, vp, vwap, pvo, efi, eom, nvi, mfi
            STATISTICS (9): pivots, mad, variance, hl2, hlc3, ohlc4,
                            midprice, decreasing, increasing
    """
    if indicators is None:
        indicators = ["sma_20", "ema_20", "rsi_14", "macd", "bbands_20"]

    symbol = symbol.upper().strip()
    for err in [validate_date(start, "start"), validate_date(end, "end")]:
        if err:
            return err

    # ── Fetch OHLCV ──────────────────────────────────────────────────────────
    try:
        try:
            from vnstock_data import Market
            df = Market().equity(symbol).ohlcv(start=start, end=end, interval="1D")
        except ImportError:
            from vnstock import Quote
            df = Quote(symbol=symbol, source="kbs").history(start=start, end=end, interval="1D")
    except Exception as e:
        return handle_vnstock_error(e, "get_technical_indicators (fetch price)", symbol)

    if df is None or df.empty:
        return f"No price data for {symbol} between {start} and {end}."

    # ── Compute indicators ────────────────────────────────────────────────────
    try:
        try:
            from vnstock_ta import Indicator
        except ImportError:
            return (
                "[vnstock_ta not installed. Run: pip install vnstock_ta]\n\n"
                f"Price data for {symbol} ({len(df)} rows) available, "
                "but indicators require vnstock_ta."
            )

        ta = Indicator(data=df)
        result_df = (
            df[["time", "open", "high", "low", "close", "volume"]].copy()
            if "time" in df.columns
            else df.copy()
        )

        errors = []
        for ind_str in indicators:
            name, period = _parse_indicator(ind_str)
            if name not in INDICATOR_DEFAULTS:
                result_df[f"{name}_unknown"] = None
                errors.append(f"'{name}' not recognised")
                continue
            try:
                _compute_indicator(ta, name, period, result_df)
            except Exception as ind_err:
                col = f"{name}_{period}" if period else name
                result_df[f"{col}_error"] = str(ind_err)[:80]
                errors.append(f"{ind_str}: {ind_err}")

        header = (
            f"## Technical Indicators: {symbol} ({start} → {end})\n"
            f"Indicators: {', '.join(indicators)}\n"
        )
        if errors:
            header += f"Errors: {'; '.join(errors)}\n"
        header += "\n"

        display = result_df.tail(20).copy()
        return header + to_claude_text(display, mode="table", max_rows=20)

    except Exception as e:
        return handle_vnstock_error(e, "get_technical_indicators", symbol)
