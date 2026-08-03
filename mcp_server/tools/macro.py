"""Macro & commodity tools: economic indicators and commodity prices."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.df_converter import to_claude_text
from utils.error_handler import handle_vnstock_error

# Indicator name -> (domain, method, description)
#
# These route through the v3 DOMAIN api — Macro().economy().gdp() — not the flat
# Macro().gdp(). That distinction is the whole reason macro was reported dead:
# the flat attributes still exist on Macro() but are the deprecated pre-v3 path
# and hit an endpoint that now 404s, while the domain path returns clean data.
# Verified live 2026-08-02: all 11 economy methods and 5 of 6 currency methods
# return rows; only interbank_rate 404s upstream.
MACRO_INDICATORS = {
    # ── economy ──────────────────────────────────────────────────────────────
    "gdp":              ("economy",  "gdp",              "GDP theo khu vực (nông nghiệp/công nghiệp/dịch vụ/thuế)"),
    "cpi":              ("economy",  "cpi",              "Chỉ số giá tiêu dùng (tổng + lõi), kèm VNINDEX"),
    "industry_prod":    ("economy",  "industry_prod",    "Chỉ số sản xuất công nghiệp"),
    "import_export":    ("economy",  "import_export",    "Xuất nhập khẩu và cán cân thương mại"),
    "retail":           ("economy",  "retail",           "Doanh thu bán lẻ tiêu dùng"),
    "fdi":              ("economy",  "fdi",              "FDI đăng ký và giải ngân"),
    "money_supply":     ("economy",  "money_supply",     "Cung tiền (tổng/tổ chức/dân cư), kèm VNINDEX"),
    "population_labor": ("economy",  "population_labor", "Dân số và lao động"),
    "credit":           ("economy",  "credit",           "Tăng trưởng tín dụng, kèm VNINDEX"),
    "total_investment": ("economy",  "total_investment", "Tổng vốn đầu tư (công/tư/FDI), kèm VNINDEX"),
    "state_budget":     ("economy",  "state_budget",     "Thu chi ngân sách nhà nước"),
    # ── currency ─────────────────────────────────────────────────────────────
    "exchange_rate":    ("currency", "exchange_rate",    "Tỷ giá (trung tâm/VCB/tự do), kèm VNINDEX"),
    "interest_rate":    ("currency", "interest_rate",    "Lãi suất theo nhóm"),
    "policy_rate":      ("currency", "policy_rate",      "Lãi suất điều hành (tái cấp vốn/chiết khấu)"),
    "omo":              ("currency", "omo",              "Thị trường mở: bơm/hút ròng của NHNN"),
    "deposit_rate":     ("currency", "deposit_rate",     "Lãi suất huy động theo ngân hàng và kỳ hạn"),
    # Present in the library but 404s upstream as of 2026-08-02. Kept so the
    # tool reports a specific reason instead of "unknown indicator".
    "interbank_rate":   ("currency", "interbank_rate",   "Lãi suất liên ngân hàng (nguồn đang lỗi 404)"),
}

# Extra keyword arguments some methods accept beyond the common ones.
MACRO_EXTRA_KWARGS = {
    "credit":       {"breakdown": "total"},
    "money_supply": {"breakdown": "total"},
    "deposit_rate": {"mode": "term", "period": "all"},
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


# The upstream Macro/CommodityPrice methods ACCEPT start/end/length but largely
# ignore them (verified 2026-07-20: exchange_rate(length="1M") still returned all
# 1,629 rows back to 2020). And because the frames are oldest-first, the table
# renderer's head(50) then showed 2020 data for a "last month" request. So trim
# the window here, and always render the NEWEST rows.
_TIME_COLS = ("time", "date", "trading_date", "period", "report_date", "month", "year")

# Units for each commodity series.
#
# These are NOT copied from docs/vnstock-data/05-macro-layer.md — that guide is
# wrong in several places, and a confidently wrong unit is worse than none.
# Each entry below was verified on 2026-08-02 by pulling the live series and
# checking the magnitude against the real-world price of that commodity:
#
#   gold_vn      146,500  doc said "VNĐ/lượng"; a lượng (37.5g ≈ 1.2oz) at the
#                         4,617 USD/oz shown by gold_global is ~146 MILLION VND,
#                         so the series is in THOUSANDS of VND — doc off by 1000x
#   pork_china        11.5  doc said "VNĐ/kg"; 11.5 VND/kg is absurd, 11.5 CNY/kg
#                         is the actual Chinese hog price
#   corn             440.75  doc said "USD/bushel"; corn trades ~$4.40/bu, so the
#   soybean        1,187.5   figures are US CENTS per bushel, not dollars
#
# steel_hrc (1,191) and coke (135.85) could not be pinned to a currency with
# confidence, so they are deliberately omitted and render as "not documented".
COMMODITY_UNITS = {
    # Vietnam domestic
    "gold_vn":       "nghìn VND/lượng (146,500 ≈ 146,5 triệu VND/lượng)",
    "gas_vn":        "nghìn VND/lít (22.38 ≈ 22,380 VND/lít)",
    "steel_d10":     "nghìn VND/kg (14.21 ≈ 14,210 VND/kg)",
    "pork_north_vn": "VND/kg",
    # International
    "gold_global":   "USD/oz (OHLCV)",
    "oil_crude":     "USD/barrel (OHLCV)",
    "gas_natural":   "USD/MMBtu (OHLCV)",
    "iron_ore":      "USD/tấn (OHLCV)",
    "fertilizer_ure": "USD/tấn (OHLCV)",
    "corn":          "US cents/bushel (440.75 = 4,41 USD/bushel)",
    "soybean":       "US cents/bushel (1187.5 = 11,88 USD/bushel)",
    "sugar":         "US cents/lb",
    "pork_china":    "CNY/kg",
}


def _materialize_time_index(df):
    """
    Move a DatetimeIndex into a real column.

    CommodityPrice.* returns its date ONLY as a DatetimeIndex named 'time'
    (verified live 2026-08-02: steel_d10 -> shape (74, 1), index=DatetimeIndex,
    columns=['close']). Because _time_column() scans df.columns only, the window
    filter silently skipped those frames AND the renderer dropped the index —
    so the tool emitted a bare column of numbers with no date at all.
    """
    import pandas as pd

    if isinstance(df.index, pd.DatetimeIndex):
        name = df.index.name or "time"
        out = df.copy()
        out.index.name = name
        return out.reset_index()
    return df


def _time_column(df):
    for col in df.columns:
        if str(col).strip().lower() in _TIME_COLS:
            return col
    return None


def _cutoff_from_length(length: str):
    """'3M' / '1Y' / '30D' / '100b' -> a pandas Timestamp cutoff, or None."""
    import re
    from datetime import datetime
    import pandas as pd

    if not length:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*([dDmMyYbB])\s*", str(length))
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    days = {"d": 1, "b": 1.6, "m": 31, "y": 366}.get(unit)  # 'b' = bars, ~1.6 calendar days each
    if not days:
        return None
    return pd.Timestamp(datetime.now()) - pd.Timedelta(days=int(n * days))


def _trim_window(df, start=None, end=None, length=None, max_rows=50):
    """
    Filter a time series to the requested window and return the newest max_rows,
    still in chronological order. Returns (frame, note).
    """
    import pandas as pd

    df = _materialize_time_index(df)
    col = _time_column(df)
    total = len(df)
    if col is None:
        return df.tail(max_rows), ("" if total <= max_rows else f" (newest {max_rows} of {total} rows)")

    out = df.copy()
    try:
        parsed = pd.to_datetime(out[col], errors="coerce")
    except Exception:
        return out.tail(max_rows), ""

    if parsed.notna().any():
        out = out.assign(_ts=parsed).sort_values("_ts")
        if start:
            out = out[out["_ts"] >= pd.Timestamp(start)]
        if end:
            out = out[out["_ts"] <= pd.Timestamp(end)]
        if not start and not end:
            cutoff = _cutoff_from_length(length)
            if cutoff is not None:
                out = out[out["_ts"] >= cutoff]
        out = out.drop(columns=["_ts"])

    kept = len(out)
    if kept == 0:
        return out, " (no rows in the requested window)"
    note = ""
    if kept > max_rows:
        out = out.tail(max_rows)
        note = f" (newest {max_rows} of {kept} rows in window)"
    elif kept < total:
        note = f" ({kept} of {total} rows in window)"
    return out, note


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
        indicator: Kinh tế —
            gdp              - GDP theo khu vực (nông nghiệp/công nghiệp/dịch vụ/thuế)
            cpi              - CPI tổng và lõi, kèm VNINDEX để đối chiếu
            industry_prod    - Chỉ số sản xuất công nghiệp
            import_export    - Xuất nhập khẩu, cán cân thương mại
            retail           - Doanh thu bán lẻ
            fdi              - FDI đăng ký và giải ngân
            money_supply     - Cung tiền (tổng/tổ chức/dân cư)
            population_labor - Dân số và lao động
            credit           - Tăng trưởng tín dụng
            total_investment - Tổng vốn đầu tư (công/tư/FDI)
            state_budget     - Thu chi ngân sách
                 Tiền tệ —
            exchange_rate    - Tỷ giá trung tâm / VCB / thị trường tự do
            interest_rate    - Lãi suất theo nhóm
            policy_rate      - Lãi suất điều hành (tái cấp vốn, chiết khấu)
            omo              - Thị trường mở: NHNN bơm/hút ròng
            deposit_rate     - Lãi suất huy động theo ngân hàng và kỳ hạn
            interbank_rate   - Liên ngân hàng (nguồn đang lỗi 404)
        start: Start date 'YYYY-MM' for most indicators, 'YYYY-MM-DD' for exchange_rate/interest_rate day period
        end: End date (same format as start)
        period: Granularity — depends on indicator (see above). Defaults to each indicator's natural period.
        length: Relative time window e.g. '1Y', '3M', '30D'. Used when start/end not provided. Default '1Y'.
                Applied by this server (the upstream source ignores it), newest rows first.
    """
    indicator = indicator.lower().strip()
    if indicator not in MACRO_INDICATORS:
        supported = ", ".join(sorted(MACRO_INDICATORS.keys()))
        return f"[Unknown indicator '{indicator}'. Supported: {supported}]"

    domain_name, method_name, description = MACRO_INDICATORS[indicator]

    try:
        from vnstock_data import Macro
        # v3 DOMAIN path. Macro().gdp() (the flat attribute) still exists but is
        # the deprecated pre-v3 route and 404s; Macro().economy().gdp() works.
        domain = getattr(Macro(), domain_name)()
        method = getattr(domain, method_name)

        # These methods take **kwargs and quietly reject some combinations —
        # gdp(period="year") 404s while gdp() succeeds. So pass only what the
        # caller actually asked for, and retry bare on failure rather than
        # letting an optional argument kill an otherwise-working indicator.
        kwargs = dict(MACRO_EXTRA_KWARGS.get(indicator, {}))
        if period:
            kwargs["period"] = period
        if start:
            kwargs["start"] = start
        if end:
            kwargs["end"] = end

        try:
            df = method(**kwargs)
        except Exception:
            base = dict(MACRO_EXTRA_KWARGS.get(indicator, {}))
            df = method(**base)
            note_retry = " (đã bỏ tham số period/start/end vì nguồn không nhận)"
        else:
            note_retry = ""

        if df is None or df.empty:
            return (
                f"No {indicator} data returned (domain={domain_name}). "
                "Thử bỏ tham số period/start/end, hoặc chọn chỉ tiêu khác.\n"
                f"Các chỉ tiêu hỗ trợ: {', '.join(sorted(MACRO_INDICATORS))}"
            )

        full = df
        df, note = _trim_window(df, start=start, end=end, length=length, max_rows=50)

        # A relative `length` default must never turn a populated series into an
        # error. Annual/low-frequency indicators (fdi, credit, state_budget,
        # money_supply, population_labor) have their newest row further back
        # than the 1Y default, so the window emptied them and the tool reported
        # "no data" for series that had 5-126 rows sitting right there.
        if df.empty and not (start or end):
            df = full.tail(50)
            note = (
                f" — không có dòng nào trong cửa sổ {length}; "
                "hiển thị các kỳ mới nhất hiện có (chuỗi tần suất thấp)"
            )
        elif df.empty:
            return (
                f"No {indicator} rows fall inside the requested window "
                f"({start} → {end or 'latest'}). Widen it or drop start/end.\n"
                f"Chuỗi có {len(full)} kỳ, mới nhất: "
                f"{str(full.iloc[-1].get(_time_column(full), 'n/a'))[:10]}."
            )

        header = (
            f"## {description} ({indicator.upper()})\n"
            f"Nguồn: vnstock_data Macro().{domain_name}().{method_name}() — API v3{note_retry}\n"
            f"Range: {start or length} → {end or 'latest'}{note}\n\n"
        )
        return header + to_claude_text(df, mode="table", max_rows=50)

    except ImportError:
        return "[vnstock_data not installed. Macro data requires vnstock_data.]"
    except Exception as e:
        return handle_vnstock_error(e, f"get_macro_indicator({indicator})", "")


def get_commodity_impact(commodity: str = "") -> str:
    """
    Map an INTERNATIONAL commodity to the Vietnamese industries and stocks it
    affects, with its current price.

    Call with no argument to browse all 24 commodities and see how many
    Vietnamese stocks each one touches. Call with a name or code to get that
    commodity's price plus the affected ICB industries and ticker list.

    Different dataset from get_commodity_price: this one covers global
    benchmarks (Brent, Copper, London Robusta Coffee, Rough Rice, Baltic Dry
    Index, HRC steel...) and carries the impact mapping. get_commodity_price
    covers the domestic SPL series (gold_vn, gas_vn, steel_d10, pork_north_vn).

    Args:
        commodity: Name (English or Vietnamese, partial match ok) or item_code,
                   e.g. 'Brent', 'Robusta', 'Rice', 'Steel', 'COM_CMICEB'.
                   Leave empty to list everything.
    """
    try:
        from vnstock_data import Macro
        cm = Macro().commodity()
        listing = cm.listing()

        if listing is None or listing.empty:
            return "Không lấy được danh mục hàng hóa quốc tế."

        query = (commodity or "").strip()

        # ── Browse mode ──────────────────────────────────────────────────────
        if not query:
            rows = []
            for _, r in listing.iterrows():
                try:
                    n = len(cm.related_stock(item_id=str(r["item_id"])))
                except Exception:
                    n = 0
                rows.append({
                    "Hàng hóa": str(r.get("commo_name_en", ""))[:40],
                    "Đơn vị": r.get("commo_unit", ""),
                    "Giá": r.get("close_value"),
                    "Thay đổi": r.get("diff_value"),
                    "Số mã VN": n,
                })
            import pandas as pd
            df = pd.DataFrame(rows).sort_values("Số mã VN", ascending=False)
            return (
                "## Hàng Hóa Quốc Tế — Ảnh Hưởng Tới Cổ Phiếu Việt Nam\n"
                "`Thay đổi` là mức thay đổi TUYỆT ĐỐI theo đơn vị của mặt hàng, không phải %.\n"
                "Đây là ảnh chụp tại thời điểm gọi — nguồn KHÔNG cấp chuỗi thời gian cho bộ này.\n"
                "Gọi lại với tên mặt hàng để xem danh sách ngành và mã cụ thể.\n\n"
                + to_claude_text(df, mode="table", max_rows=30)
            )

        # ── Detail mode ──────────────────────────────────────────────────────
        q = query.lower()
        cols = [c for c in ("commo_name_en", "commo_name_vn", "item_code") if c in listing.columns]
        mask = None
        for c in cols:
            m = listing[c].astype(str).str.lower().str.contains(q, na=False, regex=False)
            mask = m if mask is None else (mask | m)
        hit = listing[mask] if mask is not None else listing.iloc[0:0]

        if hit.empty:
            names = ", ".join(listing["commo_name_en"].astype(str).head(24))
            return f"[Không tìm thấy '{query}'. Có sẵn: {names}]"

        row = hit.iloc[0]
        item_id = str(row["item_id"])
        multi = (
            f"\n*Khớp {len(hit)} mặt hàng, đang hiển thị mặt hàng đầu tiên. "
            f"Các mặt hàng khác: {', '.join(hit['commo_name_en'].astype(str)[1:6])}.*"
            if len(hit) > 1 else ""
        )

        try:
            inds = cm.related_industry(item_id=item_id)
        except Exception:
            inds = None
        try:
            stocks = cm.related_stock(item_id=item_id)
        except Exception:
            stocks = None

        # The feed returns float32-widened values (90.120002746582), so round for
        # display — the spurious digits are precision noise, not precision.
        def _num(v, nd=2):
            try:
                return f"{float(v):,.{nd}f}"
            except (TypeError, ValueError):
                return str(v)

        close_v, diff_v = row.get("close_value"), row.get("diff_value")
        pct = ""
        try:
            if float(close_v) and float(diff_v):
                prev = float(close_v) - float(diff_v)
                if prev:
                    pct = f" ({float(diff_v) / prev * 100:+.2f}%)"
        except (TypeError, ValueError, ZeroDivisionError):
            pass

        out = [
            f"## {row.get('commo_name_en')} ({row.get('commo_name_vn')})",
            f"Giá hiện tại: **{_num(close_v)}** {row.get('commo_unit')} "
            f"| Thay đổi: {_num(diff_v)}{pct} (tuyệt đối, không phải %)",
            f"Mã nguồn: `{row.get('item_code')}` | item_id: {item_id}{multi}",
            "",
        ]
        desc = row.get("commo_desc_vn") or row.get("commo_desc_en")
        if desc and str(desc) != "nan":
            out += [f"> {str(desc)[:400]}", ""]

        if inds is not None and len(inds):
            name_col = next((c for c in inds.columns if "name_vn" in c), inds.columns[-1])
            out += [f"### Ngành chịu ảnh hưởng ({len(inds)})", ""]
            out += [f"- {v}" for v in inds[name_col].astype(str).tolist()]
            out += [""]
        else:
            out += ["### Ngành chịu ảnh hưởng", "", "*Nguồn không map ngành nào cho mặt hàng này.*", ""]

        if stocks is not None and len(stocks):
            syms = stocks.iloc[:, 0].astype(str).tolist()
            out += [
                f"### Cổ phiếu Việt Nam chịu ảnh hưởng ({len(syms)})", "",
                ", ".join(syms), "",
            ]
        else:
            out += [
                "### Cổ phiếu Việt Nam chịu ảnh hưởng", "",
                "*Nguồn không map mã nào cho mặt hàng này — KHÔNG có nghĩa là không có "
                "doanh nghiệp VN liên quan, chỉ là bản đồ này chưa phủ.*", "",
            ]

        out += [
            "---",
            "*Độ phủ của bản đồ này không đều — đã kiểm chứng 2026-08-02: thép HRC map 89 mã, "
            "dầu Brent/WTI 36 mã, nhưng Đồng 0 mã và cà phê Robusta chỉ 1 mã (BKG) dù Việt Nam "
            "là nước xuất khẩu cà phê lớn. Coi đây là gợi ý sàng lọc, không phải danh sách đầy đủ.*",
            "*Bộ dữ liệu này chỉ có giá tại thời điểm gọi, không có lịch sử.*",
        ]
        return "\n".join(out)

    except ImportError:
        return "[vnstock_data not installed.]"
    except Exception as e:
        return handle_vnstock_error(e, "get_commodity_impact", commodity)


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
            return (
                f"No price data available for {commodity}.\n"
                "Lưu ý: SPL là nguồn DUY NHẤT cấp dữ liệu hàng hóa — không có nguồn dự phòng."
            )

        df, note = _trim_window(df, start=start, end=end, length=length, max_rows=50)
        if df.empty:
            return (
                f"No {commodity} rows fall inside the requested window "
                f"({start or length} → {end or 'latest'}). Widen it or drop start/end."
            )

        # Brief summary line for numeric columns, stamped with the date it
        # belongs to — a "latest" figure with no date is not verifiable.
        summary = ""
        as_of = ""
        try:
            tcol = _time_column(df)
            if tcol is not None and len(df):
                as_of = f" | As of: {str(df[tcol].iloc[-1])[:10]}"
            numeric = df.select_dtypes("number")
            if not numeric.empty:
                last = numeric.iloc[-1]
                parts = [f"{c}: {val:,.2f}" for c, val in last.items() if val == val]
                summary = "Latest: " + " | ".join(parts) + "\n\n"
        except Exception:
            pass

        unit = COMMODITY_UNITS.get(commodity)
        unit_line = (
            f"Unit: {unit}\n" if unit
            else "Unit: not documented by the SPL source — verify before comparing across series.\n"
        )

        header = (
            f"## Commodity: {commodity.replace('_', ' ').title()}\n"
            f"Range: {start or length} → {end or 'latest'} | Rows: {len(df)}{note}{as_of}\n"
            f"{unit_line}"
            f"{summary}"
        )
        return header + to_claude_text(df, mode="table", max_rows=50)

    except ImportError:
        return "[vnstock_data not installed. Commodity data requires vnstock_data.]"
    except Exception as e:
        return handle_vnstock_error(e, f"get_commodity_price({commodity})", "")
