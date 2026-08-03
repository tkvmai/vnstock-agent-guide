"""
Multi-source resolution for vnstock data groups.

Why this exists
---------------
Every tool used to bind to exactly one upstream source. When Mirae Asset (MAS)
went down on 2026-08-02, all four financial-statement tools died with it even
though VCI serves the same reports and was healthy the whole time. Binding one
source per data group turns any single provider outage into a total loss of that
group.

This module walks an ordered chain of sources instead, returning the first that
yields data and reporting which one answered.

Source matrix (docs/vnstock-data/09-data-sources.md, corrected against the
library's own validation and live probes on 2026-08-02):

  Finance    VCI, MAS          NOT VND — the quick-reference table in the guide
                               lists VND, but Finance(source=...) raises
                               "chỉ nhận 'VCI' hoặc 'MAS'". The detailed
                               section 4 of the guide is the correct one.
  Quote      VCI, VND, MAS, KBS
  Company    VCI, KBS
  Trading    VCI, CAFEF
  Listing    VCI, VND

  Macro      ASEAN, MBK        via the v3 domain API — see MACRO_DOMAINS below

  Single-source groups with NO fallback available:
    Market valuation (P/E, P/B history) .. VND only
    Insights / TopStock .................. VND only
    Commodity ............................ SPL only
    Fund ................................. Fmarket only

Ordering is evidence-based, not alphabetical (all verified live 2026-08-02):
  * Quote   -> VCI/VND before KBS: KBS lagged a session behind (2026-07-30 vs
              2026-07-31) which is what made get_market_overview report
              different index levels on repeated calls.
  * Company -> VCI before KBS: VCI returned 29 shareholders and 50 events for
              VCB where KBS returned 3 and 0.
  * Trading -> VCI before CAFEF: CAFEF raised on every method probed.
  * Finance -> VCI before MAS: VCI was healthy and returns a flat, easier
              layout; MAS is Excel-style parent/child and was unreachable.

Structural caveat
-----------------
Sources are NOT byte-identical. The guide warns (§ "Fallback Strategy") that
fallback is safe for groups with a unified structure such as OHLCV, while
Finance differs: MAS ships an Excel-style parent/child hierarchy, VCI a flat
frame with different column names. So every tool MUST surface which source
answered — callers comparing figures across calls need to know when the shape
or vintage changed underneath them. Never silently mix.
"""

# Ordered chains per data group. First entry is the preferred source.
FINANCE_SOURCES = ("VCI", "MAS")
QUOTE_SOURCES = ("VCI", "VND", "KBS", "MAS")
COMPANY_SOURCES = ("VCI", "KBS")
TRADING_SOURCES = ("VCI", "CAFEF")
LISTING_SOURCES = ("VCI", "VND")

# Every index symbol served by the Vietnamese market, from
# Listing(source="VCI").all_indices() (21 rows, verified live 2026-08-02) plus
# the exchange-level codes that listing omits.
#
# Needed because an index quote is in POINTS, not thousands of VND — labelling
# VNFIN's 2,099.43 as "nghìn VND" the way a stock price is labelled would read
# as 2.1 million VND per unit. The price tools use this to pick the right unit.
INDEX_SYMBOLS = frozenset({
    # Exchange indices
    "VNINDEX", "HNXINDEX", "UPCOMINDEX", "HNX30",
    # HOSE indices
    "VN30", "VNMID", "VNSML", "VN100", "VNALL", "VNSI",
    # Sector indices (GICS-VN level 1)
    "VNIT", "VNIND", "VNCONS", "VNCOND", "VNHEAL",
    "VNENE", "VNUTI", "VNREAL", "VNFIN", "VNMAT",
    # Investment / thematic indices
    "VNDIAMOND", "VNFINLEAD", "VNFINSELECT",
    # VNX indices
    "VNX50", "VNXALL",
})

SECTOR_INDICES = {
    "VNIT": "Công nghệ Thông tin",
    "VNIND": "Công nghiệp",
    "VNCONS": "Hàng tiêu dùng thiết yếu",
    "VNCOND": "Hàng tiêu dùng không thiết yếu",
    "VNHEAL": "Y tế / Dược phẩm",
    "VNENE": "Năng lượng",
    "VNUTI": "Tiện ích cộng đồng",
    "VNREAL": "Bất động sản",
    "VNFIN": "Tài chính",
    "VNMAT": "Nguyên vật liệu",
}


def is_index(symbol: str) -> bool:
    """True when the symbol is a market/sector index rather than a stock."""
    return (symbol or "").upper().strip() in INDEX_SYMBOLS


# Groups only one provider serves — documented so tools can say "no fallback
# exists" rather than implying a retry might help.
SINGLE_SOURCE_GROUPS = {
    "market_valuation": "VND",
    "insights": "VND",
    "commodity": "SPL",
    "fund": "Fmarket",
}

# Macro is NOT single-source and is NOT down. It is served by ASEAN (economy,
# currency) alongside MBK. The earlier "macro is dead, no fallback" reading came
# from calling the deprecated flat path Macro().gdp() — which 404s — instead of
# the v3 domain path Macro().economy().gdp(), which returns clean data.
# Verified 2026-08-02: 16 of 17 indicators return rows; only interbank_rate 404s.
MACRO_DOMAINS = ("economy", "currency", "commodity")


def _is_empty(value) -> bool:
    if value is None:
        return True
    empty_attr = getattr(value, "empty", None)
    if empty_attr is not None:
        return bool(empty_attr)
    if isinstance(value, (list, tuple, dict, str)):
        return len(value) == 0
    return False


def resolve(sources, fetch, allow_empty: bool = False):
    """
    Call ``fetch(source)`` for each source in order; return the first real result.

    Returns ``(value, source_used, failures)`` where ``failures`` is a list of
    ``(source, reason)`` for everything tried before it succeeded. On total
    failure ``value`` is None and ``source_used`` is None, with ``failures``
    describing every attempt so the caller can report the whole picture rather
    than just the last error.

    ``allow_empty=True`` accepts an empty result as a valid answer (some
    endpoints legitimately return nothing, e.g. a company with no events).
    """
    failures = []
    for source in sources:
        try:
            value = fetch(source)
        except Exception as e:  # noqa: BLE001 - any upstream failure moves on
            failures.append((source, f"{type(e).__name__}: {str(e)[:120]}"))
            continue
        if _is_empty(value) and not allow_empty:
            failures.append((source, "returned no rows"))
            continue
        return value, source, failures
    return None, None, failures


def source_note(source_used, failures, group_sources=None) -> str:
    """One-line provenance for a tool's output header."""
    if source_used is None:
        return ""
    note = f"Nguồn dữ liệu: {source_used}"
    if failures:
        tried = ", ".join(f"{s} ({why})" for s, why in failures)
        note += f" — đã thử và bỏ qua: {tried}"
    elif group_sources and len(group_sources) > 1:
        note += f" (ưu tiên đầu trong {', '.join(group_sources)})"
    return note


def all_failed_message(tool_name: str, failures, group_sources) -> str:
    """Error text when every source in the chain failed."""
    detail = "; ".join(f"{s}: {why}" for s, why in failures) or "no sources attempted"
    return (
        f"[{tool_name}: tất cả {len(group_sources)} nguồn đều thất bại "
        f"({', '.join(group_sources)}). Chi tiết — {detail}. "
        "Đây là sự cố phía nhà cung cấp, không phải lỗi tham số.]"
    )
