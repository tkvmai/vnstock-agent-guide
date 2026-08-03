"""Stock screening tools: top movers, index members, industry filter, money flow."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.df_converter import to_claude_text, drop_all_nan_columns
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
            return (
                f"No results for criteria='{criteria}'.\n"
                "Lưu ý: VND là nguồn DUY NHẤT cấp dữ liệu Insights/TopStock — "
                "không có nguồn dự phòng."
            )

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

        # f.active() has a bug: missing 'symbol' column. All three DataFrames
        # share the same 658 rows in the same order, so borrow symbols from foreign().
        if flow_type == "active" and "symbol" not in df.columns:
            try:
                df_ref = f.foreign()
                if df_ref is not None and "symbol" in df_ref.columns and len(df_ref) == len(df):
                    df.insert(0, "symbol", df_ref["symbol"].values)
            except Exception:
                pass  # fallback: display without symbol column

        # Sort by value_1d descending so top movers appear first (not last-30 alphabetically)
        if "value_1d" in df.columns:
            df = df.sort_values("value_1d", ascending=False)

        # The 1m/3m/6m columns are shipped by the endpoint but never populated
        # (verified live 2026-08-02: value_1m/volume_1m/value_3m/volume_3m/
        # value_6m/volume_6m are all-NaN for every one of the 738 rows). Showing
        # them reads as "this stock has no 3-month flow" rather than "the source
        # does not publish it", so drop them and say so once.
        df, dropped = drop_all_nan_columns(df)

        label = {
            "foreign": "Dòng Tiền Khối Ngoại (Foreign Flow)",
            "active": "Dòng Tiền Chủ Động (Active Buy/Sell Flow)",
            "proprietary": "Dòng Tiền Tự Doanh (Proprietary Flow)",
        }[flow_type]

        note = ""
        if dropped:
            note = (
                f"\n\n*Not published by this endpoint (all-empty, omitted): "
                f"{', '.join(dropped)}. Only the 1-day and 10-day windows carry data.*"
            )

        return (
            f"## {label}\n"
            "Values in VND; volumes in shares.\n\n"
            + to_claude_text(df, mode="table", max_rows=30)
            + note
        )
    except Exception as e:
        return handle_vnstock_error(e, "get_money_flow")


def _real_breadth(index_name: str = "VNINDEX"):
    """
    True advance/decline breadth for an index.

    Insights().sentiment.breadth() is misnamed: it returns a
    time/pe/pb/position_line valuation series, NOT advancing-vs-declining counts
    (verified live 2026-08-02: shape (745, 4), columns
    ['time','pe','pb','position_line']). The genuine counts live on
    Market().index(X).trade_history() as total_stock_up_price /
    total_stock_down_price / total_stock_no_change_price /
    total_stock_ceiling / total_stock_floor.

    Returns (markdown, None) or (None, error_string).
    """
    try:
        from vnstock_data import Market
        df = Market().index(index_name).trade_history()
        if df is None or df.empty:
            return None, f"{index_name}: no trade_history rows"

        # trade_history() is NOT returned in chronological order — taking
        # .iloc[-1] blindly produced a breadth reading stamped 2026-03-11 while
        # the rest of the tool showed 2026-07-31. Sort explicitly and take the
        # genuinely newest session.
        import pandas as pd
        if "trading_date" in df.columns:
            df = df.assign(_ts=pd.to_datetime(df["trading_date"], errors="coerce"))
            df = df.dropna(subset=["_ts"]).sort_values("_ts")
            if df.empty:
                return None, f"{index_name}: no parseable trading_date"

        row = df.iloc[-1]
        up = row.get("total_stock_up_price")
        down = row.get("total_stock_down_price")
        flat = row.get("total_stock_no_change_price")
        ceil_ = row.get("total_stock_ceiling")
        floor_ = row.get("total_stock_floor")
        when = row.get("trading_date")

        if up is None or down is None or up != up or down != down:
            return None, f"{index_name}: breadth columns absent from trade_history"

        total = sum(v for v in (up, down, flat) if v == v)
        pct_up = (up / total * 100) if total else float("nan")
        if pct_up == pct_up:
            if pct_up >= 60:
                verdict = "rộng — phần lớn cổ phiếu tăng"
            elif pct_up <= 40:
                verdict = "hẹp — phần lớn cổ phiếu giảm"
            else:
                verdict = "phân hóa"
        else:
            verdict = "n/a"

        ad_ratio = (up / down) if (down and down == down and down != 0) else float("nan")

        lines = [
            f"| Chỉ tiêu | Giá trị |",
            f"|---|---|",
            f"| Số mã tăng | {up:,.0f} |",
            f"| Số mã giảm | {down:,.0f} |",
            f"| Số mã đứng giá | {flat:,.0f} |" if flat == flat else "| Số mã đứng giá | n/a |",
            f"| Số mã trần | {ceil_:,.0f} |" if ceil_ == ceil_ else "| Số mã trần | n/a |",
            f"| Số mã sàn | {floor_:,.0f} |" if floor_ == floor_ else "| Số mã sàn | n/a |",
            f"| Tỷ lệ A/D | {ad_ratio:.2f} |" if ad_ratio == ad_ratio else "| Tỷ lệ A/D | n/a |",
            f"| % mã tăng | {pct_up:.1f}% |" if pct_up == pct_up else "| % mã tăng | n/a |",
        ]
        header = (
            f"### Độ Rộng Thị Trường — {index_name} (advance/decline thực)\n"
            f"Phiên: {str(when)[:10]} | Đánh giá: {verdict}\n\n"
        )
        return header + "\n".join(lines), None
    except Exception as e:
        return None, f"{index_name}: {type(e).__name__}: {str(e)[:120]}"


def get_market_sentiment(index_name: str = "VNINDEX") -> str:
    """
    Get market sentiment: TRUE advance/decline breadth plus top index contributors
    and the index valuation series.
    Cho biết thị trường đang rộng hay hẹp, cổ phiếu nào kéo index lên/xuống.

    Args:
        index_name: Index for the breadth counts — 'VNINDEX' (default), 'HNXINDEX',
                    'UPCOMINDEX'.
    """
    index_name = (index_name or "VNINDEX").upper().strip()
    results = []
    try:
        from vnstock_data import Insights
        s = Insights().sentiment

        # 1. Real advance/decline breadth (the headline number).
        breadth_md, breadth_err = _real_breadth(index_name)
        if breadth_md:
            results.append(breadth_md)
        else:
            results.append(f"### Độ Rộng Thị Trường\n\nKhông lấy được ({breadth_err}).")

        # 2. Which stocks moved the index.
        try:
            df_contrib = s.contribution()
            if df_contrib is not None and not df_contrib.empty:
                results.append(
                    "### Top Cổ Phiếu Ảnh Hưởng Index\n"
                    "`point` = số điểm đóng góp vào index; `value` = tỷ lệ ảnh hưởng.\n\n"
                    + to_claude_text(df_contrib, mode="table", max_rows=15)
                )
        except Exception as e:
            results.append(f"### Top Cổ Phiếu Ảnh Hưởng Index\n\nKhông lấy được ({str(e)[:100]}).")

        # 3. The valuation series that used to be mislabelled as "breadth".
        try:
            df_val = s.breadth()
            if df_val is not None and not df_val.empty:
                results.append(
                    "### Định Giá Thị Trường Theo Thời Gian\n"
                    "Chuỗi P/E, P/B và position_line của toàn thị trường. "
                    "**Đây KHÔNG phải độ rộng advance/decline** — bảng ở trên mới là.\n\n"
                    + to_claude_text(df_val.tail(10), mode="table", max_rows=10)
                )
        except Exception as e:
            results.append(f"### Định Giá Thị Trường\n\nKhông lấy được ({str(e)[:100]}).")

        if not results:
            return "No sentiment data available."

        return "## Market Sentiment\n\n" + "\n\n".join(results)
    except Exception as e:
        return handle_vnstock_error(e, "get_market_sentiment")


VALID_VALUATION_INDICES = ("VNINDEX", "HNX", "VN30")


def get_market_valuation(index_name: str = "VNINDEX", duration: str = "5Y") -> str:
    """
    Get the historical P/E and P/B of a market index, with today's reading
    placed against its own history — i.e. is the market cheap or expensive
    relative to where it has traded?

    Args:
        index_name: 'VNINDEX' (default), 'HNX', or 'VN30'. UPCOM is not served.
        duration: Lookback window — '6M', '1Y', '3Y', or '5Y' (default).
    """
    index_name = (index_name or "VNINDEX").upper().strip()
    duration = (duration or "5Y").upper().strip()
    if index_name not in VALID_VALUATION_INDICES:
        return (
            f"[Invalid index '{index_name}'. Chỉ hỗ trợ: "
            f"{', '.join(VALID_VALUATION_INDICES)} — nguồn không phục vụ UPCOM.]"
        )
    if duration not in ("6M", "1Y", "3Y", "5Y"):
        return "[Invalid duration. Use '6M', '1Y', '3Y' or '5Y']"

    try:
        # Analytics().valuation() is the supported path. Market().pe()/.pb()
        # still work but emit a deprecation warning stating they are removed
        # on 2026-08-31, so they are deliberately not used here.
        from vnstock_data import Analytics
        df = Analytics().valuation(index_name).evaluation(duration=duration)

        if df is None or df.empty:
            return (
                f"No valuation data for {index_name} ({duration}).\n"
                "Lưu ý: VND là nguồn DUY NHẤT cấp định giá thị trường — "
                "không có nguồn dự phòng."
            )

        out = df.reset_index() if df.index.name else df.copy()
        tcol = out.columns[0]

        # A raw P/E series is far less useful than knowing where today sits in
        # it — that percentile is the actual question trade-sector asks.
        lines = []
        for metric in ("pe", "pb"):
            if metric not in out.columns:
                continue
            series = out[metric].dropna()
            if series.empty:
                continue
            latest = series.iloc[-1]
            pct = (series < latest).mean() * 100
            if pct >= 80:
                verdict = "ĐẮT so với lịch sử"
            elif pct <= 20:
                verdict = "RẺ so với lịch sử"
            else:
                verdict = "trong vùng trung bình"
            lines.append(
                f"| {metric.upper()} | {latest:.2f} | {series.min():.2f} | "
                f"{series.median():.2f} | {series.max():.2f} | "
                f"{pct:.0f}% | {verdict} |"
            )

        summary = ""
        if lines:
            summary = (
                "\n| Chỉ tiêu | Hiện tại | Thấp nhất | Trung vị | Cao nhất | "
                "Phân vị | Đánh giá |\n|---|---|---|---|---|---|---|\n"
                + "\n".join(lines) + "\n"
            )

        as_of = str(out[tcol].iloc[-1])[:10] if len(out) else "n/a"
        return (
            f"## Định Giá Thị Trường: {index_name} ({duration})\n"
            f"Nguồn dữ liệu: VND (nguồn duy nhất cho định giá thị trường) | "
            f"{len(out)} phiên | Dữ liệu tính đến {as_of}\n"
            f"*Phân vị = tỷ lệ số phiên trong kỳ có giá trị THẤP HƠN hiện tại.*\n"
            f"{summary}\n"
            "### Chuỗi gần nhất\n\n"
            + to_claude_text(out.tail(15), mode="table", max_rows=15)
        )
    except Exception as e:
        return handle_vnstock_error(e, f"get_market_valuation({index_name})")


def get_screener_criteria(lang: str = "vi") -> str:
    """
    List all available stock screener filter criteria (field names and categories).
    Use this first to discover valid field names for filter_stocks().

    Args:
        lang: Language for criteria descriptions — 'vi' or 'en'
    """
    lang = lang.lower().strip()
    if lang not in ("vi", "en"):
        return "[Invalid lang. Choose 'vi' or 'en']"
    try:
        from vnstock_data import Insights
        df = Insights().screener.criteria(lang=lang)
        if df is None or df.empty:
            return "No screener criteria available."
        return "## Screener Filter Criteria\n\n" + to_claude_text(df, mode="table", max_rows=100)
    except Exception as e:
        return handle_vnstock_error(e, "get_screener_criteria")


def filter_stocks(filters_json: str = "", limit: int = 100) -> str:
    """
    Screen the full Vietnamese stock market with custom filter conditions
    (P/E, ROE, RSI, market cap, sector, technical signals...).

    Args:
        filters_json: JSON array of filter conditions. Each condition:
            {"name": "<field_name>", "conditionOptions": [...]}
            where conditionOptions is either a value pick
            [{"type": "value", "value": "hsx"}] or a range [{"from": 0, "to": 10}].
            Some fields need "extraName" (e.g. {"name": "avgVolume", "extraName": "30Days", ...}).
            Common fields: exchange (hsx/hnx/upcom), marketCap, marketPrice,
            ttmPe, ttmPb, ttmRoe, netMargin, grossMargin,
            revenueGrowth/npatmiGrowth (+extraName 'Yoy'),
            rsi, macd, adx, stockStrength, rs (+extraName '3Month'),
            avgVolume/adtv (+extraName '30Days'), dailyPriceChangePercent,
            sectorLv1, stockTrend (e.g. 'STRONG_UPTREND').
            Use get_screener_criteria() for the full list.
            Empty string = no filter (full market, may return many rows).
        limit: Maximum number of records (default 100, max 2000).

        Example — HOSE stocks with P/E < 10 and ROE > 15%:
        [{"name": "exchange", "conditionOptions": [{"type": "value", "value": "hsx"}]},
         {"name": "ttmPe", "conditionOptions": [{"from": 0, "to": 10}]},
         {"name": "ttmRoe", "conditionOptions": [{"from": 15, "to": 100}]}]
    """
    import json
    limit = max(1, min(int(limit), 2000))
    filters = None
    if filters_json and filters_json.strip():
        try:
            filters = json.loads(filters_json)
        except json.JSONDecodeError as e:
            return f"[Invalid filters_json: {e}. Expected a JSON array of filter conditions.]"
        if isinstance(filters, dict) and "filter" in filters:
            filters = filters["filter"]
        if not isinstance(filters, list):
            return "[Invalid filters_json: expected a JSON array (or object with 'filter' key).]"
    try:
        from vnstock_data import Insights
        s = Insights().screener
        df = s.filter(filters=filters, limit=limit) if filters else s.filter(limit=limit)

        if df is None or df.empty:
            return "No stocks matched the given filter conditions."

        # Drop verbose/internal columns to keep output compact
        drop_cols = [
            "match_price_time", "ema_time", "last_modified_date",
            "company_name_en", "short_name_en", "company_name",
            "icb_code2", "icb_code4", "industry_en",
            "reference_price", "ceiling_price", "floor_price", "est_volume",
        ]
        df = df.drop(columns=[c for c in drop_cols if c in df.columns])

        # Unit note: the screener endpoint reports prices in FULL VND (e.g.
        # 21900), unlike ohlcv/get_stock_price which report thousands (21.90).
        # Both are the source's own documented conventions; stating which one
        # applies here stops the two being compared as if they were the same.
        return (
            f"## Screener Results ({len(df)} stocks matched)\n"
            "Prices in FULL VND (e.g. 21900 = 21,900 VND) — note this differs from "
            "get_stock_price/get_technical_indicators, which report thousands of VND.\n"
            "market_cap in VND. Growth/margin/ROE figures in percent.\n\n"
            + to_claude_text(df, mode="table", max_rows=min(limit, 100))
        )
    except Exception as e:
        return handle_vnstock_error(e, "filter_stocks")


def get_index_members(index_name: str = "VN30") -> str:
    """
    Get list of stocks in a Vietnamese market index.

    Args:
        index_name: Index name — 'VN30', 'VN100', 'VNMID', 'VNSML', 'HNX30',
                    'HOSE', 'HNX', 'UPCOM'
    """
    index_name = index_name.upper().strip()
    if index_name not in VALID_INDICES:
        # Sector indices have OHLCV but NO constituent list — verified
        # 2026-08-02: symbols_by_group('VNFIN'/'VNIT'/'VNREAL'/'VNENE'/
        # 'VNDIAMOND'/'VNFINLEAD') raised on both VCI and KBS, while 'VN30'
        # returned its 30 members. Point the caller at the route that does work
        # instead of a bare rejection.
        from utils.sources import SECTOR_INDICES
        if index_name in SECTOR_INDICES:
            return (
                f"[Chỉ số ngành '{index_name}' ({SECTOR_INDICES[index_name]}) "
                "KHÔNG có danh sách thành phần từ nguồn — đã kiểm chứng "
                "symbols_by_group() lỗi trên cả VCI và KBS.\n"
                f"Thay thế: get_stocks_by_industry('{SECTOR_INDICES[index_name]}') "
                "để lấy cổ phiếu theo ngành ICB, hoặc get_stock_price"
                f"('{index_name}') để lấy chính chỉ số đó.]"
            )
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


# Industry data source notes (verified 2026-07-20):
#   kbs.industries_icb()        -> NotImplementedError (never worked)
#   kbs.all_symbols()           -> only ['symbol', 'organ_name'], no industry column
#   kbs.symbols_by_industries() -> 694 listed symbols x ['symbol','industry_code','industry_name'],
#                                  25 clean Vietnamese industry names — best for "who is in X"
#   vci.industries_icb()        -> 177 rows x ['icb_name','en_icb_name','icb_code','level'],
#                                  has English names — best for browsing and vi/en lookup
# So: browse via VCI, resolve names via VCI, list members via KBS (VCI as fallback).

def _load_industry_taxonomy():
    """ICB taxonomy from VCI: icb_name / en_icb_name / icb_code / level. None on failure."""
    try:
        from vnstock import Listing
        df = Listing(source="vci").industries_icb()
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None


def _load_industry_members():
    """Listed symbols tagged with an industry name (KBS). None on failure."""
    try:
        from vnstock import Listing
        df = Listing(source="kbs").symbols_by_industries()
        if df is None or df.empty or "industry_name" not in df.columns:
            return None
        return df
    except Exception:
        return None


def _format_industry_hit(industry, hit, limit, source, name_col="industry_name"):
    """Render a matched industry-membership table."""
    matched = sorted(hit[name_col].dropna().astype(str).unique()) if name_col in hit.columns else []
    label = f" -> {', '.join(matched)}" if matched and matched != [industry] else ""
    return (
        f"**Stocks in '{industry}'**{label} ({len(hit)} stocks)\n"
        f"Source: {source}\n\n"
        + to_claude_text(hit, mode="table", max_rows=limit)
    )


def get_stocks_by_industry(industry: str, limit: int = 100) -> str:
    """
    Get all stocks in a specific industry sector (ICB classification).

    Args:
        industry: Industry name in Vietnamese or English
                  (e.g. 'Ngân hàng', 'Bất động sản', 'Banks', 'Real Estate')
                  Use get_industry_list() to see all available industries.
        limit: Maximum number of stocks to list (default 100, max 500)
    """
    import re

    industry = (industry or "").strip()
    if not industry:
        return "[industry cannot be empty. Use get_industry_list() to browse available industries.]"
    limit = max(1, min(int(limit), 500))
    query = industry.lower()

    try:
        members = _load_industry_members()

        # Pass 1 — direct match against the listed-symbol industry names (Vietnamese).
        if members is not None:
            hit = members[members["industry_name"].astype(str).str.lower().str.contains(query, na=False)]
            if not hit.empty:
                return _format_industry_hit(industry, hit, limit, source="KBS (listed symbols)")

        # Pass 2 — the query may be English ('Banks') or an ICB label that differs from
        # the KBS wording. Resolve it through the VCI taxonomy, then retry pass 1 with
        # every Vietnamese name it maps to.
        taxonomy = _load_industry_taxonomy()
        vi_names = []
        if taxonomy is not None:
            name_cols = [c for c in ("icb_name", "en_icb_name") if c in taxonomy.columns]
            if name_cols:
                mask = taxonomy[name_cols].apply(
                    lambda col: col.astype(str).str.lower().str.contains(query, na=False)
                ).any(axis=1)
                vi_names = taxonomy.loc[mask, "icb_name"].dropna().astype(str).tolist()

        if members is not None and vi_names:
            pattern = "|".join(re.escape(n.lower()) for n in vi_names)
            hit = members[members["industry_name"].astype(str).str.lower().str.contains(pattern, na=False, regex=True)]
            if not hit.empty:
                return _format_industry_hit(industry, hit, limit, source="KBS (listed symbols)")

        # Pass 3 — fall back to the full VCI symbol-to-industry map. Wider coverage
        # (includes unlisted/OTC companies), so only used when KBS has nothing.
        try:
            from vnstock import Listing
            vs = Listing(source="vci").symbols_by_industries()
        except Exception:
            vs = None

        if vs is not None and not vs.empty and "icb_name" in vs.columns:
            pattern = "|".join(re.escape(n.lower()) for n in (vi_names or [industry]))
            hit = vs[vs["icb_name"].astype(str).str.lower().str.contains(pattern, na=False, regex=True)]
            if not hit.empty:
                keep = [c for c in ("symbol", "organ_name", "icb_name", "icb_level") if c in hit.columns]
                hit = hit[keep].drop_duplicates(subset=["symbol"])
                return _format_industry_hit(
                    industry, hit, limit,
                    source="VCI (includes unlisted companies)",
                    name_col="icb_name",
                )

        # Nothing matched — show what is actually selectable.
        if members is not None:
            available = sorted(members["industry_name"].dropna().astype(str).unique())
            return (
                f"No stocks found for industry '{industry}'.\n\n"
                f"Available industries ({len(available)}):\n"
                + ", ".join(available)
                + "\n\nEnglish names also work (e.g. 'Banks', 'Real Estate'); "
                "run get_industry_list() for the full ICB taxonomy."
            )
        return f"No stocks found for '{industry}', and the industry listing could not be fetched."

    except Exception as e:
        return handle_vnstock_error(e, "get_stocks_by_industry", industry)


def get_industry_list(level: str = "top") -> str:
    """
    List all available industry/sector classifications (ICB taxonomy).

    Args:
        level: ICB depth to show — 'top' (levels 1-2, the useful summary, default),
               'all' (every tier), or a single level '1' / '2' / '3' / '4'.
    """
    level = str(level).lower().strip()
    if level not in ("top", "all", "1", "2", "3", "4"):
        return "[Invalid level. Use 'top' (levels 1-2), 'all', or '1'/'2'/'3'/'4']"

    try:
        df = _load_industry_taxonomy()

        if df is not None and "level" in df.columns:
            total = len(df)
            if level == "top":
                df = df[df["level"].isin([1, 2])]
                scope = "levels 1-2"
            elif level == "all":
                scope = "all levels"
            else:
                df = df[df["level"] == int(level)]
                scope = f"level {level}"

            if df.empty:
                return f"No industries found at {scope}."

            sort_cols = [c for c in ("level", "icb_code") if c in df.columns]
            if sort_cols:
                df = df.sort_values(sort_cols)

            return (
                f"## Industries — ICB taxonomy ({scope}, {len(df)} of {total} rows)\n\n"
                + to_claude_text(df, mode="table", max_rows=200)
                + "\n\nPass level='all' (or '3'/'4') for the finer tiers. "
                "Use get_stocks_by_industry('<name>') to list members."
            )

        # Fallback: derive industry names from the listed-symbol map.
        members = _load_industry_members()
        if members is not None:
            counts = (
                members.groupby("industry_name", dropna=True)["symbol"]
                .count()
                .reset_index()
                .rename(columns={"symbol": "stock_count"})
                .sort_values("stock_count", ascending=False)
            )
            return (
                f"## Industries ({len(counts)}, from the listed-symbol map — ICB taxonomy unavailable)\n\n"
                + to_claude_text(counts, mode="table", max_rows=100)
            )

        return "Could not fetch the industry list from any source (tried VCI ICB taxonomy and KBS symbol map)."
    except Exception as e:
        return handle_vnstock_error(e, "get_industry_list")
