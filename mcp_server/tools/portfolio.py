"""Portfolio and risk tools: compare stocks, compute correlation."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.df_converter import to_claude_text
from utils.error_handler import handle_vnstock_error, validate_date


def _fetch_close(symbol: str, start: str, end: str):
    """Fetch closing prices for a symbol, return Series."""
    try:
        from vnstock_data import Market
        df = Market().equity(symbol).ohlcv(start=start, end=end, interval="1D")
    except ImportError:
        from vnstock import Quote
        df = Quote(symbol=symbol, source="kbs").history(start=start, end=end, interval="1D")

    if df is None or df.empty:
        return None
    time_col = "time" if "time" in df.columns else df.columns[0]
    df = df.set_index(time_col)["close"]
    df.name = symbol
    return df


def compare_stocks(symbols: list, start: str, end: str) -> str:
    """
    Compare price performance of multiple stocks over a time period.
    Returns cumulative return (base 100), total return %, and daily volatility.

    Args:
        symbols: List of stock tickers (e.g. ['TCB', 'VCB', 'MBB'])
        start: Start date YYYY-MM-DD
        end: End date YYYY-MM-DD
    """
    if not symbols or len(symbols) < 1:
        return "[Please provide at least one symbol]"
    if len(symbols) > 10:
        return "[Maximum 10 symbols at a time]"
    for err in [validate_date(start, "start"), validate_date(end, "end")]:
        if err:
            return err

    import pandas as pd

    price_data = {}
    errors = []
    for sym in symbols:
        sym = sym.upper().strip()
        try:
            series = _fetch_close(sym, start, end)
            if series is not None and not series.empty:
                price_data[sym] = series
            else:
                errors.append(f"{sym}: no data")
        except Exception as e:
            errors.append(f"{sym}: {str(e)[:60]}")

    if not price_data:
        return f"No price data fetched. Errors:\n" + "\n".join(errors)

    df = pd.DataFrame(price_data).dropna(how="all")

    # Cumulative return normalized to 100
    norm = (df / df.iloc[0] * 100).round(2)

    # Summary stats
    returns = df.pct_change().dropna()
    summary_rows = []
    for sym in price_data:
        if sym in df.columns:
            total_ret = (df[sym].iloc[-1] / df[sym].iloc[0] - 1) * 100
            vol = returns[sym].std() * (252 ** 0.5) * 100  # annualized volatility
            last_price = df[sym].iloc[-1]
            summary_rows.append({
                "Symbol": sym,
                "Start Price": f"{df[sym].iloc[0]:,.1f}",
                "End Price": f"{last_price:,.1f}",
                "Total Return %": f"{total_ret:+.2f}%",
                "Ann. Volatility %": f"{vol:.2f}%",
            })

    summary_df = pd.DataFrame(summary_rows)

    result = f"## Stock Comparison: {', '.join(price_data.keys())} ({start} → {end})\n\n"
    result += "### Performance Summary\n\n"
    result += to_claude_text(summary_df, mode="table", max_rows=10)
    result += "\n\n### Normalized Price (Base 100)\n\n"
    result += to_claude_text(norm, mode="table", max_rows=30)

    if errors:
        result += f"\n\n*Errors: {'; '.join(errors)}*"
    return result


def get_correlation(symbols: list, start: str, end: str) -> str:
    """
    Compute pairwise return correlation matrix for multiple stocks.
    High correlation (>0.7) means stocks move together; diversification benefit is low.

    Args:
        symbols: List of stock tickers (e.g. ['TCB', 'VCB', 'HPG', 'VNM'])
        start: Start date YYYY-MM-DD
        end: End date YYYY-MM-DD
    """
    if not symbols or len(symbols) < 2:
        return "[Please provide at least 2 symbols for correlation analysis]"
    if len(symbols) > 15:
        return "[Maximum 15 symbols at a time for correlation]"
    for err in [validate_date(start, "start"), validate_date(end, "end")]:
        if err:
            return err

    import pandas as pd

    price_data = {}
    errors = []
    for sym in symbols:
        sym = sym.upper().strip()
        try:
            series = _fetch_close(sym, start, end)
            if series is not None and not series.empty:
                price_data[sym] = series
            else:
                errors.append(f"{sym}: no data")
        except Exception as e:
            errors.append(f"{sym}: {str(e)[:60]}")

    if len(price_data) < 2:
        return f"Need at least 2 symbols with data. Errors:\n" + "\n".join(errors)

    df = pd.DataFrame(price_data).dropna(how="all")
    returns = df.pct_change().dropna()
    corr = returns.corr().round(3)

    result = (
        f"## Return Correlation Matrix: {', '.join(price_data.keys())}\n"
        f"Period: {start} → {end} | Based on {len(returns)} daily returns\n\n"
        + to_claude_text(corr, mode="table", max_rows=20)
        + "\n\n*Interpretation: >0.7 = high correlation, 0.3–0.7 = moderate, <0.3 = low (good diversification)*"
    )
    if errors:
        result += f"\n\n*Errors: {'; '.join(errors)}*"
    return result
