"""Macro & commodity tools: economic indicators and commodity prices."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.df_converter import to_claude_text
from utils.error_handler import handle_vnstock_error

# Map indicator name → (method_name, default_period, description)
MACRO_INDICATORS = {
    "gdp":              ("gdp",              "quarter", "GDP growth by sector"),
    "cpi":              ("cpi",              "month",   "Consumer Price Index"),
    "industry_prod":    ("industry_prod",    "month",   "Industrial Production Index"),
    "import_export":    ("import_export",    "month",   "Import/Export trade balance"),
    "retail":           ("retail",           "month",   "Retail consumption revenue"),
    "fdi":              ("fdi",              "month",   "Foreign Direct Investment"),
    "money_supply":     ("money_supply",     "month",   "Money supply M0/M1/M2"),
    "exchange_rate":    ("exchange_rate",    "day",     "USD/VND and other exchange rates"),
    "interest_rate":    ("interest_rate",    "day",     "Interbank interest rates"),
    "population_labor": ("population_labor", "year",    "Population and labor statistics"),
}

# Map commodity name → method name
COMMODITY_MAP = {
    "gold_vn":        "gold_vn",
    "gold_global":    "gold_global",
    "gas_vn":         "gas_vn",
    "oil_crude":      "oil_crude",
    "gas_natural":    "gas_natural",
    "coke":           "coke",
    "steel_d10":      "steel_d10",
    "iron_ore":       "iron_ore",
    "steel_hrc":      "steel_hrc",
    "fertilizer_ure": "fertilizer_ure",
    "soybean":        "soybean",
    "corn":           "corn",
    "sugar":          "sugar",
    "pork_north_vn":  "pork_north_vn",
    "pork_china":     "pork_china",
}


def get_macro_indicator(
    indicator: str,
    start: str = None,
    end: str = None,
    period: str = None,
    length: str = "1Y",
) -> str:
    """
    Get Vietnam macroeconomic indicators from MayBank (MBK) data source.

    Args:
        indicator: One of:
            gdp              - GDP growth by sector (period: quarter/year)
            cpi              - Consumer Price Index (period: month/year)
            industry_prod    - Industrial Production Index (period: month/year)
            import_export    - Import/Export trade balance (period: month/year)
            retail           - Retail consumption revenue (period: month/year)
            fdi              - Foreign Direct Investment (period: month/year)
            money_supply     - Money supply M0/M1/M2 (period: month/year)
            exchange_rate    - USD/VND exchange rates (period: day/month/year)
            interest_rate    - Interbank interest rates (period: day/year)
            population_labor - Population and labor statistics (period: year)
        start: Start date 'YYYY-MM' for most indicators, 'YYYY-MM-DD' for exchange_rate/interest_rate day period
        end: End date (same format as start)
        period: Granularity — depends on indicator (see above). Defaults to each indicator's natural period.
        length: Relative time window e.g. '1Y', '3M', '30D', '100b'. Used when start/end not provided. Default '1Y'.
    """
    indicator = indicator.lower().strip()
    if indicator not in MACRO_INDICATORS:
        supported = ", ".join(sorted(MACRO_INDICATORS.keys()))
        return f"[Unknown indicator '{indicator}'. Supported: {supported}]"

    method_name, default_period, description = MACRO_INDICATORS[indicator]
    if period is None:
        period = default_period

    try:
        from vnstock_data import Macro
        macro = Macro()
        method = getattr(macro, method_name)

        kwargs = {"period": period}
        if start:
            kwargs["start"] = start
        if end:
            kwargs["end"] = end
        if not start and not end:
            kwargs["length"] = length

        df = method(**kwargs)

        if df is None or df.empty:
            return f"No {indicator} data available for the requested period."

        header = (
            f"## {description} ({indicator.upper()})\n"
            f"Period: {period} | Range: {start or length} → {end or 'latest'}\n\n"
        )
        return header + to_claude_text(df, mode="table", max_rows=50)

    except ImportError:
        return "[vnstock_data not installed. Macro data requires vnstock_data.]"
    except Exception as e:
        return handle_vnstock_error(e, f"get_macro_indicator({indicator})", "")


def get_commodity_price(
    commodity: str,
    length: str = "3M",
    start: str = None,
    end: str = None,
) -> str:
    """
    Get commodity price history from SPL data source.

    Args:
        commodity: One of:
            GOLD:    gold_vn (VN buy/sell price), gold_global (OHLCV USD/oz)
            ENERGY:  gas_vn (RON95/RON92/DO), oil_crude (WTI OHLCV), gas_natural (OHLCV)
            METALS:  coke, steel_d10 (VN rebar), iron_ore, steel_hrc (global HRC)
            AGRI:    fertilizer_ure, soybean, corn, sugar, pork_north_vn, pork_china
        length: Relative window e.g. '3M', '1Y', '30D', '100b'. Used when start/end not set. Default '3M'.
        start: Start date YYYY-MM-DD (optional, overrides length)
        end: End date YYYY-MM-DD (optional)
    """
    commodity = commodity.lower().strip()
    if commodity not in COMMODITY_MAP:
        supported = ", ".join(sorted(COMMODITY_MAP.keys()))
        return f"[Unknown commodity '{commodity}'. Supported: {supported}]"

    try:
        from vnstock_data import CommodityPrice
        cp = CommodityPrice()
        method = getattr(cp, COMMODITY_MAP[commodity])

        kwargs = {}
        if start:
            kwargs["start"] = start
        if end:
            kwargs["end"] = end
        if not start and not end:
            kwargs["length"] = length

        df = method(**kwargs)

        if df is None or df.empty:
            return f"No price data available for {commodity}."

        # Brief summary line for numeric columns
        summary = ""
        try:
            numeric = df.select_dtypes("number")
            if not numeric.empty:
                last = numeric.iloc[-1]
                parts = [f"{col}: {val:,.2f}" for col, val in last.items() if val == val]
                summary = "Latest: " + " | ".join(parts) + "\n\n"
        except Exception:
            pass

        header = (
            f"## Commodity: {commodity.replace('_', ' ').title()}\n"
            f"Range: {start or length} → {end or 'latest'} | Rows: {len(df)}\n"
            f"{summary}"
        )
        return header + to_claude_text(df, mode="table", max_rows=50)

    except ImportError:
        return "[vnstock_data not installed. Commodity data requires vnstock_data.]"
    except Exception as e:
        return handle_vnstock_error(e, f"get_commodity_price({commodity})", "")
