"""Builds the per-stock swing-trade analysis prompt for the manual "Xuất bundle
hỏi Claude" button.

Produces only RAW technical values (not the app's 0–100 scores — those have no
meaning to a model with no context for the scoring scale). The user pastes the
bundle into Claude Desktop / Claude Code, where the vnstock MCP can fetch fresh
data. No Claude API call is made from the app itself (that automated path was
removed: an LLM rephrasing a static indicator snapshot into buy/sell signals over
Telegram is not a reliable, verifiable source).
"""

import pandas as pd

_SYSTEM = (
    "Bạn là chuyên gia phân tích kỹ thuật chứng khoán, chuyên LƯỚT SÓNG ngắn hạn "
    "(swing trade) trên thị trường Việt Nam (sàn HOSE/HNX). Bối cảnh giao dịch: "
    "cơ chế thanh toán T+2.5 — mua hôm nay phải ~2.5 phiên sau mới bán được, nên "
    "rủi ro bị 'khóa' khi giá đảo chiều là yếu tố quan trọng. Khung thời gian nắm "
    "giữ mục tiêu là vài phiên đến ~1-2 tuần.\n\n"
    "Bạn sẽ nhận các CHỈ SỐ KỸ THUẬT THÔ của một cổ phiếu (giá, returns đa khung, "
    "vị trí so với MA20/MA50, RS so với VN-Index, RSI, MACD, dòng tiền ngoại/tự "
    "doanh, các chỉ số breakout...). Hãy phân tích và đưa ra:\n"
    "1) Nhận định xu hướng & trạng thái hiện tại (1-2 câu).\n"
    "2) Tín hiệu mua/chờ/tránh cho khung lướt sóng, kèm lý do dựa trên số liệu.\n"
    "3) Vùng giá vào gợi ý + vùng dừng lỗ gợi ý (dựa trên MA/ATR nếu hợp lý).\n"
    "4) Mức độ rủi ro (thấp/trung bình/cao) và lưu ý T+2.5 nếu có.\n\n"
    "Viết TIẾNG VIỆT, ngắn gọn, đi thẳng vào trọng tâm (tối đa ~150 từ). "
    "Đây là phân tích kỹ thuật tham khảo, KHÔNG phải lời khuyên đầu tư."
)


def _pct(v):
    return "—" if v is None or (isinstance(v, float) and pd.isna(v)) else f"{v:+.2f}%"


def _num(v, nd=2):
    return "—" if v is None or (isinstance(v, float) and pd.isna(v)) else f"{v:.{nd}f}"


def _build_user_text(row) -> str:
    g = row.get
    foreign_b = (g("mom_foreign_net_5d") or 0) / 1e9
    prop_b = (g("mom_prop_net_5d") or 0) / 1e9
    return "\n".join([
        f"Mã: {g('symbol')} | Sàn: {g('exchange')} | Giá hiện tại: {g('close'):,.0f} VND",
        "",
        "— ĐỘNG LƯỢNG GIÁ —",
        f"return 1 phiên: {_pct(g('mom_return_1d'))}",
        f"return 5 phiên (1 tuần): {_pct(g('mom_return_5d'))}",
        f"return 20 phiên (1 tháng): {_pct(g('mom_return_20d'))}",
        "",
        "— CẤU TRÚC MA —",
        f"giá so với MA20: {_pct(g('mom_price_vs_ma20'))}",
        f"giá so với MA50: {_pct(g('mom_price_vs_ma50'))}",
        f"MA20 so với MA50: {_pct(g('mom_ma20_vs_ma50'))} (>0 = Stage 2/golden cross)",
        f"độ dốc MA20 (10 phiên): {_pct(g('mom_slope_ma20'))} | độ dốc MA50: {_pct(g('mom_slope_ma50'))}",
        "",
        "— SỨC MẠNH TƯƠNG ĐỐI vs VN-INDEX —",
        f"RS 1 tháng: {_pct(g('mom_rs_1m'))} | RS 3 tháng: {_pct(g('mom_rs_3m'))}",
        "",
        "— DÒNG TIỀN (5 phiên) —",
        f"A/D ratio (vol ngày tăng/ngày giảm, 20 phiên): {_num(g('mom_ad_ratio'))}",
        f"khối ngoại mua ròng: {_num(foreign_b,1)} tỷ ({_pct(g('mom_foreign_net_pct'))} GTGD)",
        f"tự doanh mua ròng: {_num(prop_b,1)} tỷ ({_pct(g('mom_prop_net_pct'))} GTGD)",
        "",
        "— XÁC NHẬN KỸ THUẬT —",
        f"RSI(14): {_num(g('mom_rsi'),1)}",
        f"MACD histogram (% so giá): {_num(g('mom_macd_hist_pct'),3)}%",
        "",
        "— BREAKOUT —",
        f"breakout ratio (giá/đỉnh20 phiên): {_num(g('bo_breakout_ratio'),3)} (>1.0 = đã vượt cản)",
        f"volume ratio (so kỳ vọng): {_num(g('bo_volume_ratio'))}",
        f"dry-up ratio (vol trước breakout/nền): {_num(g('bo_dry_up_ratio'))} (<0.7 = sellers cạn)",
        f"chất lượng nền ATR5/ATR20: {_num(g('bo_narrowing_ratio'))} (<0.7 = nền chặt)",
        f"sức mạnh đóng cửa: {_num(g('bo_closing_strength'),0)}%",
        "",
        "— THANH KHOẢN —",
        f"GTGD20 (giá trị GD TB 20 phiên): {(g('liq_gtgd20') or 0)/1e9:.1f} tỷ VND",
        f"CV thanh khoản 20 phiên: {_num(g('liq_cv'),0)}%",
        f"hoạt động intraday hôm nay: {_num(g('liq_intraday_ratio'),0)}% so kỳ vọng",
    ])


_MCP_HINT = (
    "Ngoài các chỉ số trên, bạn có sẵn **vnstock MCP** — hãy gọi thêm "
    "`get_company_info`, `get_news`, `get_foreign_trade` cho mã này để bổ sung "
    "dữ liệu nền tảng & tin tức trước khi kết luận."
)


def build_manual_bundle(row) -> str:
    """Full prompt to copy-paste into Claude Desktop / Claude Code.

    Same system framing + raw technicals as the automated API call (single source
    of truth, NO 0–100 scores), plus a hint to enrich via the vnstock MCP — which
    only the manual/desktop path can actually use.
    """
    return "\n\n".join([
        "# Yêu cầu phân tích (vai trò & bối cảnh)",
        _SYSTEM,
        "# Chỉ số kỹ thuật",
        _build_user_text(row),
        _MCP_HINT,
    ])
