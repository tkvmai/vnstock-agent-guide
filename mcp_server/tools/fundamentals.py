"""Fundamental analysis tools: company info, financial ratios, statements."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.df_converter import to_claude_text, dict_to_text
from utils.error_handler import handle_vnstock_error
from utils.sources import (
    FINANCE_SOURCES, COMPANY_SOURCES,
    resolve, source_note, all_failed_message,
)


# vnstock_data returns financial statements as one ROW PER PERIOD (newest first)
# and one COLUMN PER LINE ITEM — the opposite of the layout the old slicing code
# assumed ("item"/"ticker" columns), so n_periods silently did nothing and every
# call dumped all 14 years x 111 columns (~56KB for a balance sheet).
# 'report_period' is VCI's name for it; without it a VCI frame fell through
# unsliced and dumped 42 periods x 100 columns.
_PERIOD_COLS = ("period", "report_period", "year_period", "yearreport",
                "quarter", "year", "report_date", "time")


def _fetch_statement(symbol: str, statement: str, period: str):
    """
    Pull one financial statement, trying every source that serves Finance.

    `Finance` takes `period` in the CONSTRUCTOR, not on the method — passing it
    to the method silently returns an empty frame instead of raising.
    """
    def attempt(source):
        from vnstock_data import Finance
        fin = Finance(symbol=symbol, source=source, period=period)
        return getattr(fin, statement)()

    return resolve(FINANCE_SOURCES, attempt)


# ---------------------------------------------------------------------------
# VCI ratio scale correction
# ---------------------------------------------------------------------------
# VCI labels a column "(%)" but ships a DECIMAL FRACTION in it: ROE (%) = 0.1673
# means 16.73%, not 0.17%. That is a 100x trap sitting directly on the
# thresholds a scorer uses ("operating margin > 25% = excellent"), and it also
# contradicts vnstock's OWN other endpoints — Market.summary() reports roe as
# 15.85 and the company_profile blurb says "ROE ở mức 16.73%".
#
# Verified 2026-08-02 against two independent oracles, across VCB, TCB, HPG,
# MWG and FPT (banks, steel, retail, IT):
#   company_profile text   NIM 2.63%  <- raw 0.0264   NPL 0.58% <- raw 0.0058
#                          ROE 16.73% <- raw 0.1673   coverage 258.29% <- 2.5829
#   magnitude scan         every "(%)"-named column stayed <= 1.24 on all five
#                          tickers, with x100 landing on sector-plausible values
#
# So: multiply the "(%)"-named columns by 100 to make the value match its own
# label. Scope is deliberately narrow —
#   * only columns whose NAME already claims "%", so the fix removes a
#     contradiction rather than inventing a new convention;
#   * only for VCI, the source actually verified. MAS uses a different layout
#     and was unreachable during verification, so it is left untouched.
# Columns that are decimal but do NOT claim "%" (NIM, ROIC, CIR, CAR, CASA,
# Equity/Total Assets, ...) are NOT converted — their labels are not lying —
# but they are named in a warning so the caller knows to scale them.
_DECIMAL_RATIO_COLS_NO_PCT_LABEL = (
    "Net Interest Margin", "ROIC", "CIR", "CAR", "CASA Ratio",
    "Avg Yield on Earning Assets", "Avg Cost of Financing",
    "Cost/Income Ratio", "Equity/Total Liabilities", "Equity/Loans",
    "Equity/Total Assets", "Loan Loss Reserve/Loans",
    # Coverage ratio — same fractional convention (TCB raw -1.2805 against a
    # stated 128.05%). Converting it keeps the whole table on one scale;
    # leaving it out made the header note contradict the rendered value.
    "Loan Loss Reserves/NPLs",
)


def _scale_pct_columns(df, source):
    """
    Put every fractional ratio on a real percent scale, and make its label say so.

    Two sets are handled:
      1. columns already NAMED "(%)" — value is corrected to match the label;
      2. the fractional ratios in _DECIMAL_RATIO_COLS_NO_PCT_LABEL — value is
         converted AND "(%)" is appended to the name.

    Set 2 matters because leaving ROIC at 0.09 while the scorer tests
    "ROIC > 15%" just moves the same 100x trap to a different column. A header
    note alone does not protect a reader who scans the table.

    Returns (frame, converted_column_names, renamed_pairs).
    """
    import pandas as pd
    if df is None or source != "VCI":
        return df, [], []

    converted, renamed = [], []
    out = df.copy()
    rename_map = {}

    for col in out.columns:
        name = str(col)
        labelled_pct = "%" in name
        known_fraction = name.strip() in _DECIMAL_RATIO_COLS_NO_PCT_LABEL
        if not (labelled_pct or known_fraction):
            continue
        if not pd.api.types.is_numeric_dtype(out[col]):
            continue
        series = out[col].dropna()
        if series.empty:
            continue
        # Guard: only scale a column that really is on a 0-1 scale. If VCI ever
        # starts returning true percentages, the values exceed this bound and
        # the column is left alone rather than inflated a second time. 2.0
        # clears ratios that legitimately pass 100% (LDR, NPL coverage) while
        # staying far below any genuine percent figure.
        if series.abs().max() > 2.0:
            continue
        out[col] = out[col] * 100
        converted.append(name)
        if known_fraction and not labelled_pct:
            rename_map[col] = f"{name} (%)"
            renamed.append(name)

    if rename_map:
        out = out.rename(columns=rename_map)
    return out, converted, renamed


def _scale_note(source, converted, renamed):
    """Explain what was rescaled, so the change is auditable rather than hidden."""
    if source != "VCI" or not converted:
        return ""
    lines = [
        f"*Đã quy đổi {len(converted)} cột tỷ lệ sang phần trăm thật: nguồn VCI "
        f"trả về dạng thập phân (ROE 0.1673) nên giá trị hiển thị đã ×100 "
        f"(16.73%). Đối chiếu khớp với `company_profile` và `summary()`.*"
    ]
    if renamed:
        lines.append(
            "*Đã thêm hậu tố '(%)' vào các cột vốn không ghi đơn vị: "
            + ", ".join(renamed) + ".*"
        )
    lines.append(
        "*Lưu ý dấu: `Loan Loss Reserves/NPLs` và `Cost/Income Ratio` mang dấu ÂM "
        "theo quy ước của nguồn — đọc theo giá trị tuyệt đối "
        "(-128.05% nghĩa là tỷ lệ bao phủ nợ xấu 128.05%).*"
    )
    return "\n".join(lines)


# Layouts differ per source, so callers must not assume line-item names carry
# across a failover (the guide flags this: MAS is Excel-style parent/child,
# VCI is flat with English labels).
_LAYOUT_NOTE = {
    "VCI": "Bố cục VCI: phẳng, tên khoản mục tiếng Anh.",
    "MAS": "Bố cục MAS: Excel-style phân cấp cha-con, tên khoản mục có thể trùng lặp.",
}


def _materialize_period_index(df):
    """VCI frames carry the period in the index; make it a real column."""
    import pandas as pd
    if df is None or not isinstance(df, pd.DataFrame):
        return df
    if not isinstance(df.index, pd.RangeIndex) and df.index.name:
        df = df.reset_index()
        # VCI names the index 'period' AND ships an identical 'report_period'
        # column; keeping both just wastes a column in every statement.
        if "period" in df.columns and "report_period" in df.columns:
            if df["period"].astype(str).equals(df["report_period"].astype(str)):
                df = df.drop(columns=["report_period"])
    return df


def _period_column(df):
    """Name of the column holding the reporting period, or None."""
    for col in df.columns:
        if str(col).strip().lower() in _PERIOD_COLS:
            return col
    return None


def _filter_by_period(df, period_col, period: str):
    """
    Keep only annual or only quarterly rows.

    VCI IGNORES the `period` constructor argument — it returns the same frame
    either way, mixing annual rows (labelled '2025') with quarterly ones
    ('2026-Q2'). Verified 2026-08-02: income_statement had shape (42, 24) for
    both period='year' and period='quarter'. Asking for annual figures and
    silently receiving quarters is exactly the kind of wrong-but-plausible
    answer this server should not produce, so filter on the label shape.
    """
    import re
    if period_col is None or period not in ("year", "quarter"):
        return df, False

    labels = df[period_col].astype(str)
    is_quarter = labels.str.contains(r"-?Q[1-4]\b", case=False, regex=True, na=False)
    mask = is_quarter if period == "quarter" else ~is_quarter

    filtered = df[mask]
    if filtered.empty:
        # The source only carries one granularity for this symbol — better to
        # return what exists (clearly labelled) than an empty statement.
        return df, False
    return filtered, True


def _slice_statement(df, n_periods: int, period: str = None):
    """
    Keep the newest n_periods and pivot to the conventional statement layout:
    line items down the rows, periods across the columns.

    Returns (frame, actual_period_count, period_labels).
    """
    period_col = _period_column(df)
    if period_col is None:
        # Unknown layout — return it untouched rather than mangling it.
        return df, len(df), []

    if period:
        df, _ = _filter_by_period(df, period_col, period)

    trimmed = df.copy()
    try:
        trimmed = trimmed.sort_values(period_col, ascending=False)
    except Exception:
        pass  # already newest-first in practice
    trimmed = trimmed.head(n_periods)

    labels = [str(v).removesuffix(".0") for v in trimmed[period_col].tolist()]

    drop = [c for c in trimmed.columns if str(c).strip().lower() in ("period", "year_period")]
    body = trimmed.drop(columns=drop, errors="ignore")

    pivoted = body.T
    pivoted.columns = labels
    pivoted = pivoted.dropna(how="all")          # drop items with no data in this window
    pivoted.insert(0, "item", pivoted.index)
    pivoted = pivoted.reset_index(drop=True)
    return pivoted, len(trimmed), labels


def get_company_info(symbol: str) -> str:
    """
    Get company profile including name, industry, exchange, charter capital,
    number of employees, and listing details.

    Args:
        symbol: Stock ticker (e.g. 'TCB', 'VCB')
    """
    symbol = symbol.upper().strip()
    try:
        def attempt(source):
            from vnstock_data import Company
            return Company(symbol=symbol, source=source).overview()

        info, used, failures = resolve(COMPANY_SOURCES, attempt)
        if info is None:
            return all_failed_message(f"get_company_info({symbol})", failures, COMPANY_SOURCES)

        header = f"## Company Profile: {symbol}\n{source_note(used, failures, COMPANY_SOURCES)}\n\n"

        import pandas as pd
        if isinstance(info, pd.DataFrame):
            return header + dict_to_text(info.iloc[0].to_dict())
        if isinstance(info, dict):
            return header + dict_to_text(info)
        return header + str(info)
    except Exception as e:
        return handle_vnstock_error(e, "get_company_info", symbol)


def get_financial_ratios(symbol: str, period: str = "year") -> str:
    """
    Get key financial ratios: PE, PB, ROE, ROA, EPS, gross margin, debt ratios.

    Args:
        symbol: Stock ticker (e.g. 'TCB')
        period: 'year' for annual or 'quarter' for quarterly
    """
    symbol = symbol.upper().strip()
    if period not in ("year", "quarter"):
        return "[Invalid period. Use 'year' or 'quarter']"
    try:
        df, used, failures = _fetch_statement(symbol, "ratio", period)
        if df is None:
            return all_failed_message(f"get_financial_ratios({symbol})", failures, FINANCE_SOURCES)

        df = _materialize_period_index(df)
        pcol = _period_column(df)

        # Same VCI quirk as the statements: annual and quarterly rows arrive
        # mixed regardless of the requested period.
        filtered = False
        if pcol is not None:
            df, filtered = _filter_by_period(df, pcol, period)
            try:
                df = df.sort_values(pcol, ascending=False)
            except Exception:
                pass

        gran = ""
        if pcol is not None and not filtered:
            gran = (
                f"\n*Nguồn chỉ có một loại kỳ cho mã này — không lọc được riêng "
                f"'{period}'; xem cột {pcol} để biết kỳ thực tế.*"
            )

        df, converted, renamed = _scale_pct_columns(df, used)
        scale = _scale_note(used, converted, renamed)

        return (
            f"## Financial Ratios: {symbol} ({period})\n"
            f"{source_note(used, failures, FINANCE_SOURCES)}{gran}\n"
            f"{scale}\n\n"
            + to_claude_text(df.head(8), mode="table", max_rows=8)
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_financial_ratios", symbol)


def get_income_statement(symbol: str, period: str = "year", n_periods: int = 4) -> str:
    """
    Get income statement showing revenue, gross profit, operating profit, net profit.

    Args:
        symbol: Stock ticker (e.g. 'TCB')
        period: 'year' or 'quarter'
        n_periods: Number of periods to show, newest first (default 4, max 20)
    """
    symbol = symbol.upper().strip()
    n_periods = max(1, min(int(n_periods), 20))
    if period not in ("year", "quarter"):
        return "[Invalid period. Use 'year' or 'quarter']"
    try:
        df, used, failures = _fetch_statement(symbol, "income_statement", period)
        if df is None:
            return all_failed_message(f"get_income_statement({symbol})", failures, FINANCE_SOURCES)

        df, shown, labels = _slice_statement(_materialize_period_index(df), n_periods, period)
        span = f" — {', '.join(labels)}" if labels else ""

        return (
            f"## Income Statement: {symbol} ({period}, last {shown} periods{span})\n"
            f"{source_note(used, failures, FINANCE_SOURCES)}\n"
            f"{_LAYOUT_NOTE.get(used, '')}\n\n"
            + to_claude_text(df, mode="table", max_rows=300)
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_income_statement", symbol)


def get_cash_flow(symbol: str, period: str = "year", n_periods: int = 4) -> str:
    """
    Get cash flow statement: operating, investing, and financing activities.

    Args:
        symbol: Stock ticker (e.g. 'TCB')
        period: 'year' or 'quarter'
        n_periods: Number of periods to show, newest first (default 4, max 20)
    """
    symbol = symbol.upper().strip()
    n_periods = max(1, min(int(n_periods), 20))
    if period not in ("year", "quarter"):
        return "[Invalid period. Use 'year' or 'quarter']"
    try:
        df, used, failures = _fetch_statement(symbol, "cash_flow", period)
        if df is None:
            return all_failed_message(f"get_cash_flow({symbol})", failures, FINANCE_SOURCES)

        df, shown, labels = _slice_statement(_materialize_period_index(df), n_periods, period)
        span = f" — {', '.join(labels)}" if labels else ""

        return (
            f"## Cash Flow Statement: {symbol} ({period}, last {shown} periods{span})\n"
            f"{source_note(used, failures, FINANCE_SOURCES)}\n"
            f"{_LAYOUT_NOTE.get(used, '')}\n\n"
            + to_claude_text(df, mode="table", max_rows=300)
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_cash_flow", symbol)


def get_shareholders(symbol: str) -> str:
    """
    Get list of major shareholders with ownership percentage.

    Args:
        symbol: Stock ticker (e.g. 'TCB')
    """
    symbol = symbol.upper().strip()
    try:
        def attempt(source):
            from vnstock_data import Company
            return Company(symbol=symbol, source=source).shareholders()

        df, used, failures = resolve(COMPANY_SOURCES, attempt)
        if df is None:
            return all_failed_message(f"get_shareholders({symbol})", failures, COMPANY_SOURCES)

        return (
            f"## Major Shareholders: {symbol}\n"
            f"{source_note(used, failures, COMPANY_SOURCES)}\n\n"
            + to_claude_text(df, mode="table", max_rows=30)
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_shareholders", symbol)


def get_company_officers(symbol: str, filter_by: str = "working") -> str:
    """
    Get list of board of directors and senior management.

    Args:
        symbol: Stock ticker (e.g. 'TCB')
        filter_by: 'working' (current), 'resigned', or 'all'
    """
    symbol = symbol.upper().strip()
    if filter_by not in ("working", "resigned", "all"):
        return "[Invalid filter_by. Use 'working', 'resigned', or 'all']"
    try:
        def attempt(source):
            from vnstock_data import Company
            c = Company(symbol=symbol, source=source)
            try:
                return c.officers(filter_by=filter_by)
            except TypeError:
                # KBS's officers() does not take filter_by.
                return c.officers()

        df, used, failures = resolve(COMPANY_SOURCES, attempt)
        if df is None:
            return all_failed_message(f"get_company_officers({symbol})", failures, COMPANY_SOURCES)

        return (
            f"## Officers ({filter_by}): {symbol}\n"
            f"{source_note(used, failures, COMPANY_SOURCES)}\n\n"
            + to_claude_text(df, mode="table", max_rows=30)
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_company_officers", symbol)


def get_company_news(symbol: str, limit: int = 20, keyword: str = "") -> str:
    """
    Get official company announcements for one ticker, each with its publication
    date — filings, board resolutions, dividend notices, results releases.

    Different from get_news/search_news, which crawl financial newspapers. This
    is the company's own disclosure feed.

    Useful for pinning WHEN a quarterly result was actually released, since the
    financial statements themselves carry only the fiscal period, not the
    announcement date.

    Args:
        symbol: Stock ticker (e.g. 'HPG')
        limit: Number of items (default 20, max 50)
        keyword: Optional filter on the title, e.g. 'kết quả kinh doanh',
                 'báo cáo tài chính', 'cổ tức'. Case-insensitive substring.
    """
    symbol = symbol.upper().strip()
    limit = max(1, min(int(limit), 50))
    try:
        def attempt(source):
            from vnstock_data import Company
            return Company(symbol=symbol, source=source).news()

        df, used, failures = resolve(COMPANY_SOURCES, attempt)
        if df is None:
            return all_failed_message(f"get_company_news({symbol})", failures, COMPANY_SOURCES)

        title_col = "news_title" if "news_title" in df.columns else df.columns[0]
        keep = [c for c in ("public_date", title_col, "news_source", "news_source_link")
                if c in df.columns]
        out = df[keep] if keep else df

        note = ""
        if keyword and keyword.strip():
            mask = out[title_col].astype(str).str.contains(
                keyword.strip(), case=False, na=False, regex=False)
            filtered = out[mask]
            note = (
                f"\nLọc theo '{keyword}': {len(filtered)}/{len(out)} tin khớp."
                if len(filtered)
                else f"\n**Không tin nào khớp '{keyword}'** — hiển thị toàn bộ. "
                     "Lưu ý feed này chỉ phủ khoảng 50 tin gần nhất."
            )
            if len(filtered):
                out = filtered

        rng = ""
        if "public_date" in out.columns and len(out):
            import pandas as pd
            d = pd.to_datetime(out["public_date"], errors="coerce").dropna()
            if len(d):
                rng = f" | Từ {d.min().date()} đến {d.max().date()}"

        return (
            f"## Công bố thông tin: {symbol}\n"
            f"{source_note(used, failures, COMPANY_SOURCES)} | {len(out)} tin{rng}{note}\n\n"
            + to_claude_text(out.head(limit), mode="table", max_rows=limit)
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_company_news", symbol)


def get_company_events(symbol: str) -> str:
    """
    Get corporate events: dividends, rights issues, shareholder meetings.

    Args:
        symbol: Stock ticker (e.g. 'TCB')
    """
    symbol = symbol.upper().strip()
    try:
        def attempt(source):
            from vnstock_data import Company
            return Company(symbol=symbol, source=source).events()

        df, used, failures = resolve(COMPANY_SOURCES, attempt)
        if df is None:
            return all_failed_message(f"get_company_events({symbol})", failures, COMPANY_SOURCES)

        return (
            f"## Corporate Events: {symbol}\n"
            f"{source_note(used, failures, COMPANY_SOURCES)}\n\n"
            + to_claude_text(df, mode="table", max_rows=30)
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_company_events", symbol)


def get_balance_sheet(symbol: str, period: str = "year", n_periods: int = 4) -> str:
    """
    Get balance sheet: total assets, liabilities, equity, and key sub-items.

    Args:
        symbol: Stock ticker (e.g. 'TCB')
        period: 'year' or 'quarter'
        n_periods: Number of periods to show, newest first (default 4, max 20)
    """
    symbol = symbol.upper().strip()
    n_periods = max(1, min(int(n_periods), 20))
    if period not in ("year", "quarter"):
        return "[Invalid period. Use 'year' or 'quarter']"
    try:
        df, used, failures = _fetch_statement(symbol, "balance_sheet", period)
        if df is None:
            return all_failed_message(f"get_balance_sheet({symbol})", failures, FINANCE_SOURCES)

        df, shown, labels = _slice_statement(_materialize_period_index(df), n_periods, period)
        span = f" — {', '.join(labels)}" if labels else ""

        return (
            f"## Balance Sheet: {symbol} ({period}, last {shown} periods{span})\n"
            f"{source_note(used, failures, FINANCE_SOURCES)}\n"
            f"{_LAYOUT_NOTE.get(used, '')}\n\n"
            + to_claude_text(df, mode="table", max_rows=300)
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_balance_sheet", symbol)
