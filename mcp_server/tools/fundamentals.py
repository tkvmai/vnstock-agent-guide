"""Fundamental analysis tools: company info, financial ratios, statements."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.df_converter import to_claude_text, dict_to_text
from utils.error_handler import handle_vnstock_error


def get_company_info(symbol: str) -> str:
    """
    Get company profile including name, industry, exchange, charter capital,
    number of employees, and listing details.

    Args:
        symbol: Stock ticker (e.g. 'TCB', 'VCB')
    """
    symbol = symbol.upper().strip()
    try:
        try:
            from vnstock_data import Reference
            info = Reference().company(symbol).info()
        except ImportError:
            from vnstock import Company
            info = Company(symbol=symbol, source="kbs").overview()

        if info is None:
            return f"No company info found for {symbol}."

        import pandas as pd
        if isinstance(info, pd.DataFrame):
            if info.empty:
                return f"No company info found for {symbol}."
            row = info.iloc[0].to_dict()
            return f"## Company Profile: {symbol}\n\n" + dict_to_text(row)
        elif isinstance(info, dict):
            return f"## Company Profile: {symbol}\n\n" + dict_to_text(info)
        return f"## Company Profile: {symbol}\n\n{str(info)}"
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
        try:
            from vnstock_data import Fundamental
            df = Fundamental().equity(symbol).ratio(period=period)
        except ImportError:
            from vnstock import Finance
            df = Finance(symbol=symbol, source="kbs").ratio(period=period)

        if df is None or df.empty:
            return f"No financial ratios found for {symbol}."

        return (
            f"## Financial Ratios: {symbol} ({period})\n\n"
            + to_claude_text(df, mode="table", max_rows=8)
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_financial_ratios", symbol)


def get_income_statement(symbol: str, period: str = "year", n_periods: int = 4) -> str:
    """
    Get income statement showing revenue, gross profit, operating profit, net profit.

    Args:
        symbol: Stock ticker (e.g. 'TCB')
        period: 'year' or 'quarter'
        n_periods: Number of periods to show (default 4)
    """
    symbol = symbol.upper().strip()
    n_periods = max(1, min(n_periods, 12))
    if period not in ("year", "quarter"):
        return "[Invalid period. Use 'year' or 'quarter']"
    try:
        try:
            from vnstock_data import Fundamental
            df = Fundamental().equity(symbol).income_statement(period=period)
        except ImportError:
            from vnstock import Finance
            df = Finance(symbol=symbol, source="kbs").income_statement(period=period)

        if df is None or df.empty:
            return f"No income statement data found for {symbol}."

        import pandas as pd
        # Financial statements usually have items as rows and periods as columns
        # Slice to n_periods columns (skip item/label columns)
        if "item" in df.columns or "ticker" in df.columns:
            label_cols = [c for c in df.columns if df[c].dtype == object or c in ("item", "item_id", "ticker")]
            value_cols = [c for c in df.columns if c not in label_cols]
            value_cols = value_cols[-n_periods:] if len(value_cols) > n_periods else value_cols
            df = df[label_cols + value_cols]

        return (
            f"## Income Statement: {symbol} ({period}, last {n_periods} periods)\n\n"
            + to_claude_text(df, mode="table", max_rows=40)
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_income_statement", symbol)


def get_balance_sheet(symbol: str, period: str = "year", n_periods: int = 4) -> str:
    """
    Get balance sheet: total assets, liabilities, equity, and key sub-items.

    Args:
        symbol: Stock ticker (e.g. 'TCB')
        period: 'year' or 'quarter'
        n_periods: Number of periods to show (default 4)
    """
    symbol = symbol.upper().strip()
    n_periods = max(1, min(n_periods, 12))
    if period not in ("year", "quarter"):
        return "[Invalid period. Use 'year' or 'quarter']"
    try:
        try:
            from vnstock_data import Fundamental
            df = Fundamental().equity(symbol).balance_sheet(period=period)
        except ImportError:
            from vnstock import Finance
            df = Finance(symbol=symbol, source="kbs").balance_sheet(period=period)

        if df is None or df.empty:
            return f"No balance sheet data found for {symbol}."

        import pandas as pd
        if "item" in df.columns or "ticker" in df.columns:
            label_cols = [c for c in df.columns if df[c].dtype == object or c in ("item", "item_id", "ticker")]
            value_cols = [c for c in df.columns if c not in label_cols]
            value_cols = value_cols[-n_periods:] if len(value_cols) > n_periods else value_cols
            df = df[label_cols + value_cols]

        return (
            f"## Balance Sheet: {symbol} ({period}, last {n_periods} periods)\n\n"
            + to_claude_text(df, mode="table", max_rows=40)
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_balance_sheet", symbol)
