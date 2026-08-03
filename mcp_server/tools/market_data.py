"""Market data tools: stock prices, intraday, market overview."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.df_converter import to_claude_text
from utils.error_handler import handle_vnstock_error, validate_date
from utils.sources import (
    QUOTE_SOURCES, TRADING_SOURCES, SECTOR_INDICES,
    resolve, source_note, all_failed_message, is_index,
)


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
        def attempt(source):
            from vnstock_data import Quote
            return Quote(symbol=symbol, source=source).history(
                start=start, end=end, interval=interval)

        df, used, failures = resolve(QUOTE_SOURCES, attempt)
        if df is None:
            return all_failed_message(f"get_stock_price({symbol})", failures, QUOTE_SOURCES)

        # Keep essential columns
        cols = [c for c in ["time", "open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[cols].copy()

        # State the session the data actually reaches. The upstream feed fills
        # in the newest bar at different times, so an identical call can return
        # one bar fewer moments earlier — without this line that is invisible
        # and the caller silently reads a stale "latest close".
        as_of = str(df["time"].iloc[-1])[:10] if "time" in df.columns else "n/a"

        # An index quote is in POINTS. Labelling VNFIN's 2,099.43 as "nghìn VND"
        # — the label a stock gets — would read as 2.1 million VND per unit.
        if is_index(symbol):
            kind = SECTOR_INDICES.get(symbol.upper(), "chỉ số")
            unit_line = (
                f"Đây là CHỈ SỐ ({kind}) — giá trị là ĐIỂM chỉ số, KHÔNG phải VND. "
                "Volume: tổng khối lượng toàn rổ.\n"
            )
        else:
            unit_line = "Đơn vị giá: NGHÌN VND (59.30 = 59,300 VND). Volume: số cổ phiếu.\n"

        summary = (
            f"**{symbol} Price History** ({start} to {end}, interval={interval})\n"
            f"{source_note(used, failures, QUOTE_SOURCES)}\n"
            f"Rows: {len(df)} | Dữ liệu tính đến phiên: {as_of}\n"
            f"Latest close: {df['close'].iloc[-1]:,.2f} | "
            f"Period return: {((df['close'].iloc[-1] / df['close'].iloc[0]) - 1) * 100:.2f}%\n"
            f"{unit_line}\n"
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
        # MAS does not serve intraday (guide § Quote), so it is excluded here
        # rather than being tried and failing on every call.
        intraday_sources = tuple(s for s in QUOTE_SOURCES if s != "MAS")

        def attempt(source):
            from vnstock_data import Quote
            return Quote(symbol=symbol, source=source).intraday(limit=limit)

        df, used, failures = resolve(intraday_sources, attempt)
        if df is None:
            return all_failed_message(f"get_intraday({symbol})", failures, intraday_sources)

        return (
            f"**{symbol} Intraday Ticks** (latest {min(limit, len(df))})\n"
            f"{source_note(used, failures, intraday_sources)}\n"
            "Đơn vị giá: NGHÌN VND (59.20 = 59,200 VND).\n\n"
            + to_claude_text(df, mode="table", max_rows=50)
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_intraday", symbol)


def get_price_board(symbols: list) -> str:
    """
    Get real-time price board snapshot for multiple stock symbols at once.
    Returns bid/ask, match price, ceiling/floor, foreign buy/sell volume.

    Args:
        symbols: List of stock tickers (e.g. ['TCB', 'VCB', 'HPG'])
    """
    if not symbols:
        return "[symbols list cannot be empty]"
    symbols = [s.upper().strip() for s in symbols[:20]]  # cap at 20
    try:
        def attempt(source):
            from vnstock_data import Trading
            return Trading(symbol=symbols[0], source=source).price_board(symbols_list=symbols)

        df, used, failures = resolve(TRADING_SOURCES, attempt)
        if df is None:
            return all_failed_message("get_price_board", failures, TRADING_SOURCES)

        # price_board reports FULL VND integers (59300), whereas ohlcv/intraday
        # report thousands (59.30). Both are the source's documented conventions
        # (docs/vnstock-data/schema/02-market.md); labelling which applies here
        # is what stops the two being read as the same scale.
        return (
            f"## Price Board: {', '.join(symbols)}\n"
            f"{source_note(used, failures, TRADING_SOURCES)}\n"
            "Đơn vị giá: VND ĐẦY ĐỦ (59300 = 59,300 VND) — KHÁC get_stock_price "
            "(nghìn VND). Volume: số cổ phiếu.\n\n"
            + to_claude_text(df, mode="table", max_rows=25)
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_price_board", str(symbols))


def get_foreign_trade(symbol: str, start: str, end: str) -> str:
    """
    Get foreign investor buy/sell flow history for a stock.
    Shows daily foreign buy/sell volume, net value, and remaining room.

    Args:
        symbol: Stock ticker (e.g. 'TCB')
        start: Start date YYYY-MM-DD
        end: End date YYYY-MM-DD
    """
    symbol = symbol.upper().strip()
    for err in [validate_date(start, "start"), validate_date(end, "end")]:
        if err:
            return err
    try:
        def attempt(source):
            from vnstock_data import Trading
            return Trading(symbol=symbol, source=source).foreign_trade(start=start, end=end)

        df, used, failures = resolve(TRADING_SOURCES, attempt)
        if df is None:
            return all_failed_message(f"get_foreign_trade({symbol})", failures, TRADING_SOURCES)

        return (
            f"## Foreign Trade: {symbol} ({start} → {end})\n"
            f"{source_note(used, failures, TRADING_SOURCES)}\n"
            "Giá trị (value) ở đơn vị VND; khối lượng (volume) là số cổ phiếu.\n\n"
            + to_claude_text(df, mode="table", max_rows=50)
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_foreign_trade", symbol)


def get_proprietary_flow(symbol: str) -> str:
    """
    Get proprietary (tự doanh) trading flow HISTORY for one stock — the daily
    buy/sell/net volume and value of securities firms' own trading desks.

    This is the per-symbol time series. Use get_money_flow('proprietary') for
    the opposite view: a single-day snapshot ranking the whole market.

    Args:
        symbol: Stock ticker (e.g. 'VCB', 'TCB')
    """
    symbol = symbol.upper().strip()
    try:
        # Foreign flow already has a per-symbol history tool (get_foreign_trade)
        # while proprietary flow only had the market-wide snapshot in
        # get_money_flow('proprietary'), which carries no history at all —
        # so "how has the prop desk been accumulating this stock?" could not
        # be answered. This closes that asymmetry.
        from vnstock_data import Market
        df = Market().equity(symbol).proprietary_flow()

        if df is None or df.empty:
            return (
                f"No proprietary trading data for {symbol}.\n"
                "Lưu ý: tự doanh chỉ phát sinh ở các mã có CTCK giao dịch — "
                "mã thanh khoản thấp có thể không có dữ liệu."
            )

        rng = ""
        if "time" in df.columns and len(df):
            newest, oldest = str(df["time"].iloc[0])[:10], str(df["time"].iloc[-1])[:10]
            rng = f" | Từ {oldest} đến {newest}"

        # Net over the window answers the question the caller actually has:
        # accumulating or distributing?
        verdict = ""
        if "net_val" in df.columns:
            net = df["net_val"].sum()
            side = "MUA RÒNG" if net > 0 else ("BÁN RÒNG" if net < 0 else "cân bằng")
            verdict = f"\nLũy kế cả kỳ: {side} {abs(net):,.0f} VND"

        return (
            f"## Dòng Tiền Tự Doanh (Proprietary Flow): {symbol}\n"
            f"Nguồn dữ liệu: Market layer (KBS/VCI) | {len(df)} phiên{rng}\n"
            "Giá trị ở đơn vị VND; khối lượng là số cổ phiếu. Sắp xếp mới nhất trước."
            f"{verdict}\n\n"
            + to_claude_text(df, mode="table", max_rows=50)
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_proprietary_flow", symbol)


def get_insider_deals(symbol: str, limit: int = 20) -> str:
    """
    Get insider trading records: buy/sell transactions by directors and major shareholders.

    Args:
        symbol: Stock ticker (e.g. 'TCB')
        limit: Number of records to return (default 20)
    """
    symbol = symbol.upper().strip()
    limit = max(1, min(limit, 100))
    try:
        def attempt(source):
            from vnstock_data import Trading
            return Trading(symbol=symbol, source=source).insider_deal(limit=limit)

        df, used, failures = resolve(TRADING_SOURCES, attempt)
        if df is None:
            return all_failed_message(f"get_insider_deals({symbol})", failures, TRADING_SOURCES)

        return (
            f"## Insider Deals: {symbol} (latest {limit})\n"
            f"{source_note(used, failures, TRADING_SOURCES)}\n\n"
            + to_claude_text(df, mode="table", max_rows=limit)
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_insider_deals", symbol)


def _freshest_index_ohlcv(ticker: str, start: str, end: str):
    """
    Fetch an index series from whichever SOURCE currently has the newest bar.

    Sources do not update in lockstep — verified live 2026-08-02: VCI and VND
    both carried the 2026-07-31 session while KBS still ended at 2026-07-30.
    Polling any single source therefore risks silently returning a stale
    session, which is what made two identical get_market_overview calls minutes
    apart report different HNX-Index levels. Ask every source that serves Quote
    and keep the one with the latest bar.

    Returns (df, source_label) or (None, None).
    """
    candidates = []
    for source in QUOTE_SOURCES:
        try:
            from vnstock_data import Quote
            d = Quote(symbol=ticker, source=source).history(
                start=start, end=end, interval="1D")
            if d is not None and not d.empty and "time" in d.columns:
                candidates.append((d["time"].iloc[-1], d, source))
        except Exception:
            continue

    if not candidates:
        return None, None
    candidates.sort(key=lambda t: t[0])
    _, df, source = candidates[-1]
    return df, source


def get_market_overview() -> str:
    """
    Get current snapshot of major Vietnamese market indices:
    VNINDEX, HNX-Index, and UPCOM-Index.
    Returns latest price, change, volume, and the session the data is as of.
    """
    from datetime import date, timedelta
    today = date.today().strftime("%Y-%m-%d")
    week_ago = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")

    results = []
    as_of_seen = set()
    indices = [
        ("VNINDEX", "VN-Index"),
        ("HNXINDEX", "HNX-Index"),
        ("UPCOMINDEX", "UPCOM-Index"),
    ]

    for ticker, label in indices:
        try:
            df, domain = _freshest_index_ohlcv(ticker, week_ago, today)
            if df is not None and not df.empty:
                last = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else last
                chg = last["close"] - prev["close"]
                pct = (chg / prev["close"]) * 100 if prev["close"] else 0
                sign = "+" if chg >= 0 else ""
                as_of = str(last.get("time", ""))[:10]
                as_of_seen.add(as_of)
                results.append(
                    f"**{label}**: {last['close']:,.2f}  {sign}{chg:,.2f} ({sign}{pct:.2f}%)  "
                    f"Vol: {last.get('volume', 'N/A'):,.0f}  "
                    f"_(phiên {as_of}, nguồn {domain})_"
                )
            else:
                results.append(f"**{label}**: No data available")
        except Exception as e:
            results.append(f"**{label}**: Error — {str(e)[:100]}")

    header = "## Vietnam Market Overview\n"
    if len(as_of_seen) > 1:
        header += (
            f"\n> ⚠ Các chỉ số đang ở phiên khác nhau ({', '.join(sorted(as_of_seen))}) — "
            "nguồn upstream cập nhật lệch nhau. Xem nhãn phiên ở từng dòng.\n"
        )
    elif as_of_seen:
        header += f"Dữ liệu tính đến phiên {as_of_seen.pop()}.\n"
    return header + "\n" + "\n\n".join(results)


def get_stock_summary(symbol: str) -> str:
    """
    Get key per-stock reference metrics in one call: 52-week high/low, beta,
    EPS, BVPS, market cap, P/E, P/B, ROE, dividend and dividend yield, foreign
    ownership, and 1-month / 1-year price change.

    Useful when the financial-statement endpoints are unavailable — this comes
    from a different upstream source and carries the headline valuation ratios.

    Args:
        symbol: Stock ticker (e.g. 'TCB', 'VCB', 'HPG')
    """
    symbol = symbol.upper().strip()
    try:
        from vnstock_data import Market
        df = Market().equity(symbol).summary()

        if df is None or df.empty:
            return f"No summary data available for {symbol}."

        row = df.iloc[0]

        # summary() types several ratio fields as `object`, i.e. strings —
        # pe, pb, change_1m and change_1y all arrive as text (confirmed by
        # docs/vnstock-data/schema/02-market.md). Formatting those with a
        # numeric format code raises, so coerce first and fall back to the
        # raw value rather than dropping it.
        def g(key):
            v = row.get(key)
            if v is None or v != v:
                return None
            if isinstance(v, str):
                try:
                    return float(v.replace(",", "").replace("%", "").strip())
                except ValueError:
                    return v
            # numpy scalars (int64/float64) are NOT instances of Python int/float,
            # so they fell through the numeric branches below and rendered as a
            # bare str() — losing thousands separators and the VND suffix.
            if hasattr(v, "item"):
                try:
                    return v.item()
                except Exception:
                    pass
            return v

        def money(v):
            if v is None:
                return "n/a"
            return f"{v:,.0f} VND" if isinstance(v, (int, float)) else str(v)

        def num(v, suffix=""):
            if v is None:
                return "n/a"
            return f"{v:,.2f}{suffix}" if isinstance(v, (int, float)) else f"{v}{suffix}"

        lines = [
            "| Chỉ tiêu | Giá trị |",
            "|---|---|",
            f"| Đỉnh 52 tuần | {money(g('high_52w'))} |",
            f"| Đáy 52 tuần | {money(g('low_52w'))} |",
            f"| Vốn hóa | {money(g('market_cap'))} |",
            f"| Beta | {num(g('beta'))} |",
            f"| EPS | {money(g('eps'))} |",
            f"| BVPS | {money(g('bvps'))} |",
            f"| P/E | {num(g('pe'))} |",
            f"| P/B | {num(g('pb'))} |",
            f"| ROE | {num(g('roe'), '%')} |",
            f"| Cổ tức | {money(g('dividend'))} |",
            f"| Tỷ suất cổ tức | {num((g('dividend_yield') or 0) * 100, '%') if g('dividend_yield') is not None else 'n/a'} |",
            f"| Sở hữu nước ngoài | {num(g('foreign_ownership_pct'), '%')} |",
            f"| Biến động 1 tháng | {num(g('change_1m'), '%')} |",
            f"| Biến động 1 năm | {num(g('change_1y'), '%')} |",
        ]

        return (
            f"## Stock Summary: {symbol}\n"
            "Giá và EPS/BVPS ở đơn vị VND đầy đủ (khác get_stock_price — đơn vị nghìn VND).\n\n"
            + "\n".join(lines)
        )
    except ImportError:
        return "[vnstock_data not installed. get_stock_summary requires vnstock_data.]"
    except Exception as e:
        return handle_vnstock_error(e, "get_stock_summary", symbol)
