"""Stock screening tools: top movers, index members, industry filter, money flow."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.df_converter import to_claude_text
from utils.error_handler import handle_vnstock_error

VALID_CRITERIA = ["gainer", "loser", "volume", "foreign_buy", "foreign_sell", "value", "deal"]
VALID_INDICES = ["VN30", "VN100", "VNMID", "VNSML", "HNX30", "HOSE", "HNX", "UPCOM"]


def screen_stocks(criteria: str = "gainer") -> str:
    """
    Screen top 10 stocks by market criteria.

    Args:
        criteria: 'gainer' (tăng giá mạnh nhất), 'loser' (giảm mạnh nhất),
                  'volume' (đột biến khối lượng), 'value' (giá trị GD cao nhất),
                  'foreign_buy' (khối ngoại mua ròng), 'foreign_sell' (khối ngoại bán ròng),
                  'deal' (giao dịch thỏa thuận lớn nhất)
    """
    criteria = criteria.lower().strip()
    if criteria not in VALID_CRITERIA:
        return f"[Invalid criteria '{criteria}'. Choose from: {', '.join(VALID_CRITERIA)}]"
    try:
        from vnstock_data import Insights
        r = Insights().ranking
        method_map = {
            "gainer":       r.gainer,
            "loser":        r.loser,
            "volume":       r.volume,
            "value":        r.value,
            "foreign_buy":  r.foreign_buy,
            "foreign_sell": r.foreign_sell,
            "deal":         r.deal,
        }
        df = method_map[criteria]()

        if df is None or df.empty:
            return f"No results for criteria='{criteria}'."

        label = {
            "gainer": "Top Tăng Giá",
            "loser": "Top Giảm Giá",
            "volume": "Top Đột Biến Khối Lượng",
            "value": "Top Giá Trị Giao Dịch",
            "foreign_buy": "Top Khối Ngoại Mua Ròng",
            "foreign_sell": "Top Khối Ngoại Bán Ròng",
            "deal": "Top Giao Dịch Thỏa Thuận",
        }[criteria]

        return f"## {label}\n\n" + to_claude_text(df, mode="table", max_rows=20)
    except Exception as e:
        return handle_vnstock_error(e, "screen_stocks")


def get_money_flow(flow_type: str = "foreign") -> str:
    """
    Get market money flow data — dòng tiền vào/ra thị trường.

    Args:
        flow_type: 'foreign' (dòng tiền khối ngoại),
                   'active' (dòng tiền chủ động mua/bán),
                   'proprietary' (dòng tiền tự doanh)
    """
    flow_type = flow_type.lower().strip()
    valid = ["foreign", "active", "proprietary"]
    if flow_type not in valid:
        return f"[Invalid flow_type '{flow_type}'. Choose from: {', '.join(valid)}]"
    try:
        from vnstock_data import Insights
        f = Insights().flow
        method_map = {
            "foreign":     f.foreign,
            "active":      f.active,
            "proprietary": f.proprietary,
        }
        df = method_map[flow_type]()

        if df is None or df.empty:
            return f"No money flow data for type='{flow_type}'."

        label = {
            "foreign": "Dòng Tiền Khối Ngoại (Foreign Flow)",
            "active": "Dòng Tiền Chủ Động (Active Buy/Sell Flow)",
            "proprietary": "Dòng Tiền Tự Doanh (Proprietary Flow)",
        }[flow_type]

        return f"## {label}\n\n" + to_claude_text(df, mode="table", max_rows=30)
    except Exception as e:
        return handle_vnstock_error(e, "get_money_flow")


def get_market_sentiment() -> str:
    """
    Get market sentiment: breadth (advancing vs declining) and top index contributors.
    Cho biết thị trường đang rộng hay hẹp, cổ phiếu nào kéo index lên/xuống.
    """
    results = []
    try:
        from vnstock_data import Insights
        s = Insights().sentiment

        try:
            df_contrib = s.contribution()
            if df_contrib is not None and not df_contrib.empty:
                results.append("### Top Cổ Phiếu Ảnh Hưởng Index\n\n" + to_claude_text(df_contrib, mode="table", max_rows=15))
        except Exception as e:
            results.append(f"### Contribution: {str(e)[:100]}")

        try:
            df_breadth = s.breadth()
            if df_breadth is not None and not df_breadth.empty:
                results.append("### Độ Rộng Thị Trường (Market Breadth)\n\n" + to_claude_text(df_breadth, mode="table", max_rows=10))
        except Exception as e:
            results.append(f"### Breadth: {str(e)[:100]}")

        if not results:
            return "No sentiment data available."

        return "## Market Sentiment\n\n" + "\n\n".join(results)
    except Exception as e:
        return handle_vnstock_error(e, "get_market_sentiment")


def get_index_members(index_name: str = "VN30") -> str:
    """
    Get list of stocks in a Vietnamese market index.

    Args:
        index_name: Index name — 'VN30', 'VN100', 'VNMID', 'VNSML', 'HNX30',
                    'HOSE', 'HNX', 'UPCOM'
    """
    index_name = index_name.upper().strip()
    if index_name not in VALID_INDICES:
        return f"[Invalid index '{index_name}'. Choose from: {', '.join(VALID_INDICES)}]"
    try:
        from vnstock import Listing
        df = Listing(source="kbs").symbols_by_group(group=index_name)

        if df is None or (hasattr(df, "empty") and df.empty):
            return f"No members found for index '{index_name}'."

        import pandas as pd
        if isinstance(df, list):
            symbols = [str(s) for s in df]
            return f"**{index_name} members** ({len(symbols)} stocks):\n\n" + ", ".join(symbols)
        if isinstance(df, pd.Series):
            symbols = df.dropna().tolist()
            return f"**{index_name} members** ({len(symbols)} stocks):\n\n" + ", ".join(str(s) for s in symbols)
        if isinstance(df, pd.DataFrame):
            return (
                f"**{index_name} members** ({len(df)} stocks)\n\n"
                + to_claude_text(df, mode="table", max_rows=100)
            )
        return f"**{index_name}**: {str(df)}"
    except Exception as e:
        return handle_vnstock_error(e, "get_index_members")


def get_stocks_by_industry(industry: str) -> str:
    """
    Get all stocks in a specific industry sector (ICB classification).

    Args:
        industry: Industry name in Vietnamese or English
                  (e.g. 'Ngân hàng', 'Bất động sản', 'Banks', 'Real Estate')
                  Use get_industry_list() to see all available industries.
    """
    try:
        from vnstock import Listing
        listing = Listing(source="kbs")
        df = listing.all_symbols()

        if df is None or df.empty:
            return "Could not fetch stock listing."

        # Search in industry/sector columns (case-insensitive partial match)
        industry_lower = industry.lower()
        industry_cols = [c for c in df.columns if any(k in c.lower() for k in ["industry", "sector", "nganh", "icb"])]

        if not industry_cols:
            return f"No industry column found. Available columns: {list(df.columns)}"

        mask = df[industry_cols].apply(
            lambda col: col.astype(str).str.lower().str.contains(industry_lower, na=False)
        ).any(axis=1)
        filtered = df[mask]

        if filtered.empty:
            # Show available industries
            all_industries = df[industry_cols[0]].dropna().unique().tolist()
            sample = ", ".join(str(x) for x in all_industries[:20])
            return (
                f"No stocks found for industry '{industry}'.\n"
                f"Available industries (sample): {sample}"
            )

        return (
            f"**Stocks in '{industry}'** ({len(filtered)} stocks)\n\n"
            + to_claude_text(filtered, mode="table", max_rows=50)
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_stocks_by_industry")


def get_industry_list() -> str:
    """List all available industry/sector classifications."""
    try:
        from vnstock import Listing
        df = Listing(source="kbs").industries_icb()
        if df is None or df.empty:
            return "Could not fetch industry list."
        return "## Available Industries (ICB)\n\n" + to_claude_text(df, mode="table", max_rows=100)
    except Exception as e:
        return handle_vnstock_error(e, "get_industry_list")
