"""Technical analysis tools: compute indicators on price data."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.df_converter import to_claude_text
from utils.error_handler import handle_vnstock_error, validate_date

# Supported indicators and their default periods
INDICATOR_DEFAULTS = {
    "sma": 20, "ema": 20, "wma": 20,
    "rsi": 14, "macd": None,
    "bbands": 20, "kc": 20, "atr": 14,
    "stoch": 14, "obv": None, "adx": 14,
    "aroon": 14, "psar": None, "supertrend": 7,
    "willr": 14, "cmo": 14, "roc": 10, "mom": 10,
}


def _parse_indicator(indicator_str: str):
    """Parse 'rsi_14' → ('rsi', 14) or 'macd' → ('macd', None)."""
    parts = indicator_str.lower().strip().split("_")
    name = parts[0]
    period = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else INDICATOR_DEFAULTS.get(name)
    return name, period


def get_technical_indicators(
    symbol: str,
    start: str,
    end: str,
    indicators: list = None,
) -> str:
    """
    Calculate technical indicators for a stock symbol.

    Args:
        symbol: Stock ticker (e.g. 'TCB')
        start: Start date YYYY-MM-DD
        end: End date YYYY-MM-DD
        indicators: List of indicator strings, each formatted as 'name' or 'name_period'.
                    Examples: ['sma_20', 'ema_50', 'rsi_14', 'macd', 'bbands_20', 'atr_14']
                    Supported: sma, ema, wma, rsi, macd, bbands, kc, atr, stoch, obv, adx,
                               aroon, psar, supertrend, willr, cmo, roc, mom
    """
    if indicators is None:
        indicators = ["sma_20", "ema_20", "rsi_14", "macd", "bbands_20"]

    symbol = symbol.upper().strip()
    for err in [validate_date(start, "start"), validate_date(end, "end")]:
        if err:
            return err

    # Step 1: Fetch OHLCV
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

    # Step 2: Compute indicators via vnstock_ta
    try:
        import pandas as pd
        result_df = df[["time", "open", "high", "low", "close", "volume"]].copy() if "time" in df.columns else df.copy()

        try:
            from vnstock_ta import Indicator
            ta = Indicator(data=df)

            for ind_str in indicators:
                name, period = _parse_indicator(ind_str)
                col_name = f"{name}_{period}" if period else name
                try:
                    if name == "sma":
                        result_df[col_name] = ta.trend.sma(length=period)
                    elif name == "ema":
                        result_df[col_name] = ta.trend.ema(length=period)
                    elif name == "wma":
                        result_df[col_name] = ta.trend.wma(length=period)
                    elif name == "rsi":
                        result_df[col_name] = ta.momentum.rsi(length=period)
                    elif name == "macd":
                        macd_df = ta.momentum.macd()
                        if isinstance(macd_df, pd.DataFrame):
                            for c in macd_df.columns:
                                result_df[c] = macd_df[c].values
                        else:
                            result_df["macd"] = macd_df
                    elif name == "bbands":
                        bb_df = ta.volatility.bbands(length=period)
                        if isinstance(bb_df, pd.DataFrame):
                            for c in bb_df.columns:
                                result_df[c] = bb_df[c].values
                        else:
                            result_df["bbands"] = bb_df
                    elif name == "atr":
                        result_df[col_name] = ta.volatility.atr(length=period)
                    elif name == "obv":
                        result_df["obv"] = ta.volume.obv()
                    elif name == "adx":
                        result_df[col_name] = ta.trend.adx(length=period)
                    elif name == "stoch":
                        stoch_df = ta.momentum.stoch(length=period)
                        if isinstance(stoch_df, pd.DataFrame):
                            for c in stoch_df.columns:
                                result_df[c] = stoch_df[c].values
                    elif name == "willr":
                        result_df[col_name] = ta.momentum.willr(length=period)
                    else:
                        result_df[f"{col_name}_unsupported"] = None
                except Exception as ind_err:
                    result_df[f"{col_name}_error"] = str(ind_err)[:50]

        except ImportError:
            return (
                f"[vnstock_ta not installed. Run: pip install vnstock_ta]\n\n"
                f"Price data for {symbol} ({len(df)} rows) available, but indicators require vnstock_ta."
            )

        # Show last 20 rows
        display = result_df.tail(20).copy()
        return (
            f"## Technical Indicators: {symbol} ({start} → {end})\n"
            f"Indicators: {', '.join(indicators)}\n\n"
            + to_claude_text(display, mode="table", max_rows=20)
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_technical_indicators", symbol)
