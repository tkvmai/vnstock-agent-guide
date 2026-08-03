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

    # Symbols rarely share an identical trading calendar — one feed can lag a
    # session behind another (verified: index/equity endpoints disagree on
    # whether the newest bar exists yet). Aligning them on a union index leaves
    # NaN holes, and .iloc[0] / .iloc[-1] then read a hole instead of a price,
    # so a symbol with perfectly good data reported "End Price: nan" and
    # "Total Return: +nan%". Use each column's own first/last VALID observation.
    def _first_last_valid(series):
        s = series.dropna()
        if s.empty:
            return None, None, None, None
        return s.iloc[0], s.iloc[-1], s.index[0], s.index[-1]

    # Normalize each column against its own first valid price, not row 0 —
    # a NaN at row 0 previously blanked the entire column.
    norm = df.apply(lambda c: c / c.dropna().iloc[0] * 100 if c.notna().any() else c).round(2)

    # fill_method=None is deliberate. pct_change() defaults to forward-filling
    # NaNs first, which turns a session a symbol did not trade into a fabricated
    # 0% return — that understates volatility. Leave the gap as NaN and drop it
    # per-symbol when computing the statistic.
    returns = df.pct_change(fill_method=None)
    summary_rows = []
    incomplete = []
    for sym in price_data:
        if sym not in df.columns:
            continue
        first, last, first_ts, last_ts = _first_last_valid(df[sym])
        if first is None or not first:
            incomplete.append(sym)
            continue
        total_ret = (last / first - 1) * 100
        vol = returns[sym].dropna().std() * (252 ** 0.5) * 100  # annualized
        summary_rows.append({
            "Symbol": sym,
            "Start Price": f"{first:,.2f}",
            "End Price": f"{last:,.2f}",
            "Total Return %": f"{total_ret:+.2f}%",
            "Ann. Volatility %": f"{vol:.2f}%" if vol == vol else "n/a",
            "First Bar": str(first_ts)[:10],
            "Last Bar": str(last_ts)[:10],
        })

    summary_df = pd.DataFrame(summary_rows)

    result = f"## Stock Comparison: {', '.join(price_data.keys())} ({start} → {end})\n\n"
    result += "Prices in thousands of VND (e.g. 59.30 = 59,300 VND), as returned by the source.\n"
    result += (
        "Per-symbol First/Last Bar are shown because feeds can differ by a "
        "session; returns are computed from each symbol's own first and last "
        "traded bar.\n\n"
    )
    result += "### Performance Summary\n\n"
    result += to_claude_text(summary_df, mode="table", max_rows=10)
    result += "\n\n### Normalized Price (Base 100)\n\n"
    result += to_claude_text(norm, mode="table", max_rows=30, index_label="time")

    if incomplete:
        result += f"\n\n*No usable price series for: {', '.join(incomplete)}*"
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
    # fill_method=None: forward-filling would inject fabricated 0% returns on
    # sessions a symbol did not trade, which biases the correlation toward the
    # symbols whose calendars happen to align.
    returns = df.pct_change(fill_method=None).dropna()
    corr = returns.corr().round(3)

    # The symbol labels live in the correlation matrix's INDEX. Rendering it
    # with index=False dropped them, leaving an unlabelled grid of numbers in
    # which no pair could be identified — index_label materializes them.
    result = (
        f"## Return Correlation Matrix: {', '.join(price_data.keys())}\n"
        f"Period: {start} → {end} | Based on {len(returns)} daily returns\n\n"
        + to_claude_text(corr, mode="table", max_rows=20, index_label="symbol")
        + "\n\n*Interpretation: >0.7 = high correlation, 0.3–0.7 = moderate, <0.3 = low (good diversification)*"
    )
    if errors:
        result += f"\n\n*Errors: {'; '.join(errors)}*"
    return result
