"""Market data tools: stock prices, intraday, market overview."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.df_converter import to_claude_text
from utils.error_handler import handle_vnstock_error, validate_date


def get_stock_price(symbol: str, start: str, end: str, interval: str = "1D") -> str:
    """
    Get OHLCV historical price data for a Vietnamese stock symbol.

    Args:
        symbol: Stock ticker (e.g. 'TCB', 'VCB', 'VNINDEX')
        start: Start date in YYYY-MM-DD format
        end: End date in YYYY-MM-DD format
        interval: Time interval - '1D' (daily), '1W' (weekly), '1M' (monthly),
                  '1m','5m','15m','30m','1H' (intraday, limited to recent data)
    """
    symbol = symbol.upper().strip()
    for err in [validate_date(start, "start"), validate_date(end, "end")]:
        if err:
            return err
    try:
        try:
            from vnstock_data import Market
            df = Market().equity(symbol).ohlcv(start=start, end=end, interval=interval)
        except ImportError:
            from vnstock import Quote
            q = Quote(symbol=symbol, source="kbs")
            df = q.history(start=start, end=end, interval=interval)

        if df is None or df.empty:
            return f"No price data found for {symbol} between {start} and {end}."

        # Keep essential columns
        cols = [c for c in ["time", "open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[cols].copy()

        summary = (
            f"**{symbol} Price History** ({start} to {end}, interval={interval})\n"
            f"Rows: {len(df)} | "
            f"Latest close: {df['close'].iloc[-1]:,.1f} | "
            f"Period return: {((df['close'].iloc[-1] / df['close'].iloc[0]) - 1) * 100:.2f}%\n\n"
        )
        return summary + to_claude_text(df, mode="table", max_rows=50)
    except Exception as e:
        return handle_vnstock_error(e, "get_stock_price", symbol)


def get_intraday(symbol: str, limit: int = 100) -> str:
    """
    Get latest intraday tick data for a stock symbol.

    Args:
        symbol: Stock ticker (e.g. 'TCB')
        limit: Number of latest ticks to return (max 500)
    """
    symbol = symbol.upper().strip()
    limit = min(limit, 500)
    try:
        try:
            from vnstock_data import Market
            df = Market().equity(symbol).intraday(limit=limit)
        except ImportError:
            from vnstock import Quote
            q = Quote(symbol=symbol, source="kbs")
            df = q.intraday(symbol=symbol, source="kbs")

        if df is None or df.empty:
            return f"No intraday data for {symbol}. Market may be closed or data unavailable."

        return f"**{symbol} Intraday Ticks** (latest {min(limit, len(df))})\n\n" + to_claude_text(df, mode="table", max_rows=50)
    except Exception as e:
        return handle_vnstock_error(e, "get_intraday", symbol)


def get_market_overview() -> str:
    """
    Get current snapshot of major Vietnamese market indices:
    VNINDEX, HNX-Index, and UPCOM-Index.
    Returns latest price, change, and volume for each index.
    """
    from datetime import date, timedelta
    today = date.today().strftime("%Y-%m-%d")
    week_ago = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")

    results = []
    indices = [
        ("VNINDEX", "VN-Index"),
        ("HNXINDEX", "HNX-Index"),
        ("UPCOMINDEX", "UPCOM-Index"),
    ]

    for ticker, label in indices:
        try:
            try:
                from vnstock_data import Market
                df = Market().equity(ticker).ohlcv(start=week_ago, end=today, interval="1D")
            except ImportError:
                from vnstock import Quote
                df = Quote(symbol=ticker, source="kbs").history(start=week_ago, end=today, interval="1D")

            if df is not None and not df.empty:
                last = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else last
                chg = last["close"] - prev["close"]
                pct = (chg / prev["close"]) * 100 if prev["close"] else 0
                sign = "+" if chg >= 0 else ""
                results.append(
                    f"**{label}**: {last['close']:,.2f}  {sign}{chg:,.2f} ({sign}{pct:.2f}%)  "
                    f"Vol: {last.get('volume', 'N/A'):,.0f}"
                )
            else:
                results.append(f"**{label}**: No data available")
        except Exception as e:
            results.append(f"**{label}**: Error — {str(e)[:100]}")

    return "## Vietnam Market Overview\n\n" + "\n\n".join(results)
