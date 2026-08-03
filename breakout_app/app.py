"""Panel dashboard for the Breakout Screener (2 pages: Layer 1 + Layer 2).

Serve standalone:   panel serve breakout_app/app.py --show
Or via run.py (also starts the background scheduler):  python breakout_app/run.py
"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import panel as pn

import config
import store
import scheduler
import claude_advisor
from engine import scoring
from data import db

pn.extension("tabulator", notifications=True, sizing_mode="stretch_width")

# ── Column titles ────────────────────────────────────────────────────────────────
L2_COLS = ["rank", "symbol", "exchange", "state_label", "buy_score", "liquidity",
           "momentum", "signal", "rating", "close"]
L2_TITLES = {"rank": "#", "symbol": "Mã", "exchange": "Sàn",
             "state_label": "Trạng thái", "buy_score": "BUY",
             "liquidity": "Thanh khoản", "momentum": "Động lượng",
             "signal": "Tín hiệu", "rating": "Đánh giá", "close": "Giá"}
L1_PASS_COLS = ["symbol", "exchange", "close", "gtgd20_b", "cv", "intraday_ratio"]
L1_PASS_TITLES = {"symbol": "Mã", "exchange": "Sàn", "close": "Giá",
                  "gtgd20_b": "GTGD20 (tỷ)", "cv": "CV %", "intraday_ratio": "Intraday %"}
L1_FAIL_COLS = ["symbol", "exchange", "reason"]
L1_FAIL_TITLES = {"symbol": "Mã", "exchange": "Sàn", "reason": "Lý do không pass"}

REGIME_STYLE = {
    "ok": ("success", "🟢 THỊ TRƯỜNG UPTREND"),
    "caution": ("warning", "🟡 MARKET CAUTION"),
    "blocked": ("danger", "🔴 THỊ TRƯỜNG DOWNTREND — SCREENER TẠM DỪNG (Layer 2)"),
}

_scanning = threading.Event()

# ── Global widgets (sidebar) ──────────────────────────────────────────────────────
# Universe size is NOT a setting: it is determined entirely by the liquidity
# pre-filter (min GTGD20 on Tab 1) applied to the whole HOSE+HNX market.
scan_button = pn.widgets.Button(name="🔍 Quét ngay", button_type="primary", height=42)
regime_pane = pn.pane.Alert("Chưa quét. Bấm **Quét ngay** để bắt đầu.", alert_type="light")
status_pane = pn.pane.Markdown("", styles={"font-size": "0.85em", "color": "#666"})

# ── Layer 1 widgets ──────────────────────────────────────────────────────────────
min_price = pn.widgets.IntInput(name="Giá tối thiểu (VND)", value=config.MIN_PRICE, step=1000, start=0)
min_gtgd20_b = pn.widgets.FloatInput(name="GTGD20 tối thiểu (tỷ VND)",
                                     value=config.MIN_GTGD20 / 1e9, step=5.0, start=0)
ex_hose = pn.widgets.Checkbox(name="HOSE", value="HOSE" in config.DEFAULT_EXCHANGES)
ex_hnx = pn.widgets.Checkbox(name="HNX", value="HNX" in config.DEFAULT_EXCHANGES)


def _selected_exchanges():
    sel = []
    if ex_hose.value:
        sel.append("HOSE")
    if ex_hnx.value:
        sel.append("HNX")
    return sel or list(config.DEFAULT_EXCHANGES)
show_failed = pn.widgets.Checkbox(name="Hiện các mã KHÔNG pass (kèm lý do)", value=False)

l1_summary = pn.pane.Markdown("")
l1_pass_table = pn.widgets.Tabulator(pd.DataFrame(columns=L1_PASS_COLS), disabled=True,
                                     show_index=False, height=460, layout="fit_data_stretch",
                                     titles=L1_PASS_TITLES)
l1_fail_table = pn.widgets.Tabulator(pd.DataFrame(columns=L1_FAIL_COLS), disabled=True,
                                     show_index=False, height=300, layout="fit_data_stretch",
                                     titles=L1_FAIL_TITLES, visible=False)

# ── Layer 2 widgets ──────────────────────────────────────────────────────────────
position_size = pn.widgets.IntInput(name="Position size (VND)", value=config.DEFAULT_POSITION_SIZE,
                                    step=10_000_000, start=1_000_000)
min_score = pn.widgets.FloatSlider(name="BUY score tối thiểu", value=config.DEFAULT_MIN_SCORE,
                                   start=0, end=100, step=5)
manual_l2_button = pn.widgets.Button(name="▶ Chạy Layer 2 thủ công (bỏ qua chặn downtrend)",
                                     button_type="warning", height=38)
l2_table = pn.widgets.Tabulator(
    pd.DataFrame(columns=L2_COLS), disabled=True, selectable=1, show_index=False,
    height=480, layout="fit_data_stretch", titles=L2_TITLES,
    formatters={
        "buy_score": {"type": "progress", "max": 100, "min": 0, "legend": True},
        "liquidity": {"type": "progress", "max": 100, "min": 0},
        "momentum": {"type": "progress", "max": 100, "min": 0},
        "signal": {"type": "progress", "max": 100, "min": 0},
    },
)
detail_pane = pn.pane.Markdown("*Chọn một mã trong bảng để xem chi tiết.*")
claude_button = pn.widgets.Button(name="🤖 Xuất bundle hỏi Claude", button_type="default",
                                  disabled=True, height=36)
claude_out = pn.pane.Markdown("")

# ── Tracking / validation widgets (Phase 3) ───────────────────────────────────────
TRACK_COLS = ["reco_date", "symbol", "state", "reco_close", "buy_score",
              "ret_t1", "ret_t2", "ret_t3", "ret_t4", "ret_t5", "mfe", "mae", "verdict"]
TRACK_TITLES = {"reco_date": "Ngày KN", "symbol": "Mã", "state": "Trạng thái",
                "reco_close": "Giá KN", "buy_score": "BUY", "ret_t1": "T+1 %",
                "ret_t2": "T+2 %", "ret_t3": "T+3 %", "ret_t4": "T+4 %", "ret_t5": "T+5 %",
                "mfe": "Đỉnh %", "mae": "Đáy %", "verdict": "Kết quả"}
MFE_TAKE_PROFIT = 3.0   # MFE ≥ this % within 5 sessions = real chance to take profit
track_summary = pn.pane.Markdown("")
track_table = pn.widgets.Tabulator(pd.DataFrame(columns=TRACK_COLS), disabled=True,
                                   show_index=False, height=460,
                                   layout="fit_data_stretch", titles=TRACK_TITLES)

# ── Missed-winners tab (false negatives: not recommended but won) ──────────────────
MISS_COLS = ["obs_date", "symbol", "reason", "buy_score", "close_ref", "ret_t3", "review_cause"]
MISS_TITLES = {"obs_date": "Ngày", "symbol": "Mã", "reason": "Vì sao bị loại",
               "buy_score": "BUY lúc đó", "close_ref": "Giá EOD", "ret_t3": "T+3 %",
               "review_cause": "Kết luận review"}
pool_summary = pn.pane.Markdown("")
miss_table = pn.widgets.Tabulator(pd.DataFrame(columns=MISS_COLS), disabled=True,
                                  show_index=False, height=420, layout="fit_data_stretch",
                                  titles=MISS_TITLES)


# ── Rendering helpers ────────────────────────────────────────────────────────────
def _fmt_pct(v):
    return "—" if v is None or pd.isna(v) else f"{v:+.2f}%"


def _n(v, nd=2):
    """Format a number, or '—' if missing."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{v:.{nd}f}"


def _vol(v):
    """Format a share volume with thousands separators, or '—'."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{v:,.0f}"


def _render_detail(row: pd.Series) -> str:
    g = row.get

    # ── 0. Trạng thái & thời điểm (RevD) ──────────────────────────────────────────
    state = f"""### 🎯 Trạng thái & thời điểm — {g('state_label')}
*RevD đưa THỜI ĐIỂM vào lệnh thành yếu tố hạng nhất: thưởng breakout MỚI, phạt breakout đã chạy xa/quá cũ, và phát hiện mã SẮP breakout để vào sớm (T+2.5).*

| Chỉ số | Giá trị | Ý nghĩa |
|---|---|---|
| Trạng thái | {g('state_label')} | 🟢 Mua ngay = breakout mới (age≤1, chưa chạy xa ≤4%); 🔵 Sắp breakout = co chặt sát đỉnh, vào sớm; 🟠 Muộn = đã chạy xa/cũ → hạ mạnh điểm |
| Breakout ratio | {_n(g('bo_breakout_ratio'),3)} | Giá / đỉnh đóng cửa 20 phiên. ≥1.0 = đã vượt; ≤1.04 = còn mới; >1.07 = đuổi giá |
| Breakout age | {(str(g('breakout_age')) + ' phiên') if (g('breakout_age') is not None and g('breakout_age') >= 0) else '— (chưa vượt đỉnh)'} | Số phiên kể từ khi vượt đỉnh. 0 = hôm nay (lý tưởng); ≥2 = muộn → age_factor giảm điểm |
| Điểm tín hiệu (Signal) | {_n(g('signal'),1)} | = Trigger (breakout) hoặc Setup (sắp breakout), trọng số **0.40** trong BUY |
| Hệ số quá nóng | ×{_n(g('overheat_mult'),2)} | RSI + độ xa MA20. RSI>70 hoặc giá >6% trên MA20 bắt đầu phạt; RSI>80 → ×0.55 |
| Kháng cự dài hạn | {_fmt_pct(g('dist_to_high'))} so đỉnh ~4 tháng (×{_n(g('overhead_mult'),2)}) | Breakout 20 phiên nhưng còn SÂU dưới đỉnh dài hạn = phía trên đầy người kẹp hàng chờ bán (overhead supply). <−10% → ×0.70; <−5% → ×0.90 |
| Hệ số trạng thái | ×{_n(g('state_mult'),2)} | Fresh ×1.0 · Sắp breakout ×0.95 · Muộn ×0.6 (điều chỉnh ưu tiên theo thời điểm) |"""
    if g("state") == "PRE_BREAKOUT":
        state += (f"\n| Khoảng cách dưới đỉnh | {_n(g('setup_dist_below_pivot'),2)}% "
                  f"(đ {g('setup_score_proximity')}) | Càng sát dưới đỉnh càng sắp bật (≤3% mới tính Sắp breakout). |"
                  f"\n| Điểm Setup | {_n(g('setup_score'),1)} | Điểm chất lượng nền pre-breakout (proximity + nền chặt + dry-up + cấu trúc + RS). |")

    # ── 1. Liquidity ────────────────────────────────────────────────────────────
    liq = f"""### 💧 1. Thanh khoản — {g('liquidity'):.1f}/100  *(trọng số 0.35)*
*Câu hỏi: mã này có đủ dòng tiền THẬT, đủ ỔN ĐỊNH, và HÔM NAY có đang hoạt động để lướt sóng vào/ra trơn tru không?*

| Sub-component | Giá trị (điểm) | Ý nghĩa |
|---|---|---|
| GTGD20 / position size | {_n(g('liq_safety_ratio'),1)}× (đ {g('liq_score_gtgd20')}) | Với quy mô lệnh của bạn, mã có đủ thanh khoản để vào/ra mà KHÔNG trượt giá? GTGD20 (giá trị khớp TB 20 phiên) gấp bao nhiêu lần số tiền vào lệnh. ≥200×: lệnh chìm trong dòng tiền thị trường, vào/ra tự do. <10×: lệnh quá lớn so với mã, không nên vào. |
| Hoạt động intraday | {_n(g('liq_intraday_ratio'),0)}% (đ {g('liq_score_intraday')}) | Hôm nay có dòng tiền vào THẬT không — không chỉ thanh khoản nền mà cụ thể hôm nay? So GTGD lũy kế hôm nay với mức kỳ vọng (đã điều chỉnh theo số phút đã trôi của phiên). <30%: gần như không ai giao dịch → không có setup thật, loại. |
| ├ GTGD intraday | {_n((g('liq_gtgd_intraday') or 0)/1e9,2)} tỷ | Tổng giá trị khớp lũy kế từ đầu phiên đến hiện tại (giá hiện tại × khối lượng khớp). |
| └ Volume intraday | {_vol(g('liq_volume_intraday'))} CP | Tổng khối lượng cổ phiếu khớp lũy kế từ đầu phiên đến hiện tại. |
| Ổn định CV (20 phiên) | {_n(g('liq_cv'),0)}% (đ {g('liq_score_cv')}) | Thanh khoản đều đặn hay chỉ bùng lên vài phiên? CV = độ lệch chuẩn/trung bình của GTGD 20 phiên. CV cao = bẫy thanh khoản: kiểu pump-and-dump tạo 1–2 phiên volume cực lớn kéo GTGD20 lên giả rồi chết. <30%: rất đều (thật); ≥150%: thất thường (nghi ngờ). |"""

    # ── 2. Momentum ─────────────────────────────────────────────────────────────
    mom = f"""### 🚀 2. Động lượng — {g('momentum'):.1f}/100  *(trọng số 0.25)*
*Câu hỏi: phân biệt mã thanh khoản tốt nhưng ĐI NGANG với mã đang TĂNG THẬT. Lướt sóng chỉ cần bắt đà đang mạnh, không cần dự báo dài hạn.*

| Nhóm (điểm) | Chỉ số | Giá trị | Ý nghĩa |
|---|---|---|---|
| **Composite** ({_n(g('mom_composite'),0)})<br>*đa khung — đang chạy mạnh hơn bình thường?* | return 1D | {_fmt_pct(g('mom_return_1d'))} | Hôm nay có tăng? Khung 1D nhiễu nhất nên trọng số thấp (0.15) — tránh "đuổi giá". |
| | return 5D | {_fmt_pct(g('mom_return_5d'))} | Tuần qua có momentum rõ không? **Tín hiệu CHÍNH (0.50)** — 5 phiên ≈ 1 tuần, khớp đúng holding period của swing T+2.5. |
| | return 20D | {_fmt_pct(g('mom_return_20d'))} | Tháng qua xu hướng có ủng hộ (0.35)? Tránh mua "bounce" hồi phục trong downtrend — phân biệt Stage 2 thật với hồi kỹ thuật. |
| | consistency | ×{_n(g('mom_consistency_mult'),2)} | 3 khung cùng chiều → xác suất momentum tiếp diễn cao (×1.10). Nếu 20D tốt nhưng 1D/5D âm = dấu hiệu đảo chiều → phạt (×0.85–0.70). |
| **MA** ({_n(g('mom_ma'),0)})<br>*sức khỏe cấu trúc xu hướng* | giá vs MA20 | {_fmt_pct(g('mom_price_vs_ma20'))} | Entry có sát MA20 (stop chặt) không? Vùng 0–3.5% là đẹp; quá xa → stop phải đặt xa → R:R xấu, và dễ lỗ lớn nếu bị khóa T+2.5 khi giá pullback về MA20. |
| | giá vs MA50 | {_fmt_pct(g('mom_price_vs_ma50'))} | Uptrend TRUNG HẠN có được xác nhận? Khác MA20, ở xa MA50 vẫn tốt (Stage 2 mạnh); dưới MA50 = Stage 3/4 (phân phối/giảm). |
| | MA20 vs MA50 | {_fmt_pct(g('mom_ma20_vs_ma50'))} | MA20 đã vượt MA50 chưa = Stage 2 đã bắt đầu chưa? Đây là chốt chặn phân biệt UPTREND THẬT với DEAD-CAT BOUNCE (giá > MA20 nhưng MA20 < MA50). |
| | slope MA20/50 | {_fmt_pct(g('mom_slope_ma20'))} / {_fmt_pct(g('mom_slope_ma50'))} | Cả ngắn hạn (MA20) lẫn trung hạn (MA50) có đang TĂNG TỐC không (độ dốc 10 phiên)? MA50 dốc lên = tổ chức đang tích lũy trung hạn. |
| **RS vs Index** ({_n(g('mom_rs'),0)})<br>*có phải leader đang dẫn dắt?* | RS 1 tháng | {_fmt_pct(g('mom_rs_1m'))} | Leader luôn OUTPERFORM index TRƯỚC khi breakout. Vượt VN-Index bao nhiêu % trong 1 tháng — ưu tiên cao (leader đang nổi ngay bây giờ). |
| | RS 3 tháng | {_fmt_pct(g('mom_rs_3m'))} | Vượt/kém VN-Index trong 3 tháng — xác nhận vị thế dẫn dắt trung hạn. |
| | RS tổng hợp | {_fmt_pct(g('mom_rs_weighted'))} | Mã breakout nhưng tăng ÍT hơn index = không phải leader, xác suất thắng thấp. >+8%: leader rõ; <−5%: underperform, bỏ qua. |
| **Dòng tiền** ({_n(g('mom_flow'),0)})<br>*smart money đang tích lũy?* | A/D ratio | {_n(g('mom_ad_ratio'),2)} | Volume ngày TĂNG / volume ngày GIẢM (20 phiên). ≥1.5 = tổ chức đang tích lũy. Đây là tín hiệu SỚM hơn breakout, cho biết breakout có dòng tiền thật hay không. |
| | Khối ngoại (5 phiên) | {_n((g('mom_foreign_net_5d') or 0)/1e9,1)} tỷ → {_fmt_pct(g('mom_foreign_net_pct'))} (đ {_n(g('mom_score_foreign'),0)}) | Mua ròng khối ngoại 5 phiên, **chuẩn hóa theo %GTGD** (không dùng tuyệt đối vì 90 tỷ với HPG là nhỏ, với midcap là lớn). >+5%: rất mạnh; <−2%: bán mạnh (cảnh báo). |
| | Tự doanh (5 phiên) | {_n((g('mom_prop_net_5d') or 0)/1e9,1)} tỷ → {_fmt_pct(g('mom_prop_net_pct'))} (đ {_n(g('mom_score_prop'),0)}) | Mua ròng tự doanh CTCK, cũng chuẩn hóa %GTGD. Ngưỡng THẤP hơn ngoại (>+3% đã mạnh) vì tự doanh giao dịch khối lượng nhỏ hơn quỹ ngoại. |
| | SMF tổng hợp | {_n(g('mom_smf'),0)} | Smart Money Flow = 0.6×điểm ngoại + 0.4×điểm tự doanh. |
| | convergence | ×{_n(g('mom_convergence_mult'),2)} | A/D và SMF có cùng xác nhận không? Cả hai mạnh → ×1.20 (đáng tin nhất). A/D đẹp nhưng ngoại bán ròng → ×0.85 (retail mua khi tổ chức phân phối — bẫy kinh điển). |
| **Kỹ thuật** ({_n(g('mom_technical'),0)})<br>*momentum đã xác nhận nhưng chưa quá nóng?* | RSI(14) | {_n(g('mom_rsi'),1)} | Với T+2.5, điểm tối ưu là momentum đang tăng nhưng CHƯA quá nóng. RSI 60–70: sweet spot, xác suất tiếp diễn 3–5 phiên tới tốt nhất. >80: quá mua, dễ đảo chiều. <40: quá yếu. |
| | MACD histogram | {_n(g('mom_macd_hist_pct'),3)}% | Histogram (= MACD − signal) chuẩn hóa theo giá. >0: vừa cắt lên, động lượng dương; >0.20%: dương mạnh, xác nhận rõ. |"""

    # ── 3. Breakout ─────────────────────────────────────────────────────────────
    bo_head = (f"### 📈 3. Breakout / Trigger — {g('breakout'):.1f}/100\n"
               "*Câu hỏi: giá có đang VƯỢT kháng cự với xác nhận đủ mạnh — và còn MỚI không? RevD thưởng breakout mới, phạt breakout đã chạy xa/cũ.*\n")
    if g("bo_gated"):
        bo = (bo_head + f"\n> ℹ️ **Chưa breakout** (close/đỉnh20 = {_n(g('bo_breakout_ratio'),3)} < 1.0) → "
              "điểm Trigger = 0. Nếu là 🔵 **Sắp breakout**, điểm tín hiệu lấy từ **Setup** (xem mục Trạng thái ở trên).")
    else:
        bo = bo_head + f"""
| Sub-component | Giá trị | Ý nghĩa |
|---|---|---|
| Độ mới (price_fresh) | {_n(g('bo_breakout_ratio'),3)} (đ {g('bo_score_price_fresh')}) | **RevD thưởng độ MỚI, không thưởng chạy xa.** 1.00–1.02: vừa vượt (100đ, lý tưởng); 1.04–1.07: đang chạy xa (50đ); >1.07: đuổi giá (20đ). |
| Age factor | age {g('breakout_age')} → ×{_n(g('bo_age_factor'),2)} | Số phiên kể từ khi vượt đỉnh nhân vào điểm Trigger. 0→×1.0; 2→×0.6; ≥3→×0.3 → breakout cũ bị hạ mạnh. |
| Volume breakout | {_n(g('bo_volume_ratio'),2)}× | Breakout phải có volume xác nhận. Volume lũy kế hôm nay so mức kỳ vọng (điều chỉnh theo giờ). >1.3×: có lực mua thật. |
| Volume dry-up trước đó | {_n(g('bo_dry_up_ratio'),2)} | NGHỊCH trực giác (VCP): volume phải GIẢM TRƯỚC breakout. Cạn = sellers đã rút → breakout bền. <0.7: dry-up tốt. |
| Chất lượng nền (ATR5/20) | {_n(g('bo_narrowing_ratio'),2)} | Nền phải THU HẸP dần trước breakout. <0.7: nền chặt (VCP), stop gần, R:R tốt. >1.1: nền loạn, dễ fake. |
| Sức mạnh đóng cửa | {_n(g('bo_closing_strength'),0)}% | (close − low)/(high − low). >80%: buyers kiểm soát cuối phiên → tốt cho phiên sau. |"""

    header = (f"## {g('symbol')} — BUY {g('buy_score'):.1f} ({g('rating')}) · {g('state_label')}\n"
              f"**Sàn** {g('exchange')} · **Giá** {g('close'):,.0f} VND · "
              f"BUY = (0.35×Thanh khoản + 0.25×Động lượng + 0.40×Tín hiệu) × quá_nóng × trạng_thái\n")
    return "\n\n".join([header, state, liq, mom, bo])


def _claude_bundle(row: pd.Series) -> str:
    # Single source of truth: identical system framing + raw technicals as the
    # automated Telegram API call (NO 0–100 scores), so a manual paste reproduces
    # the same analysis. claude_advisor appends the vnstock-MCP enrichment hint.
    return claude_advisor.build_manual_bundle(row)


def _refresh_view():
    """Read the shared store and update all widgets."""
    s = store.get()
    atype, label = REGIME_STYLE.get(s["regime"], ("light", s["regime"]))
    ratio = f" · VNINDEX/MA20 = {s['regime_ratio']:.3f}" if s.get("regime_ratio") else ""
    regime_pane.alert_type = atype
    mh = s.get("market_health")
    mh_line = ""
    if mh:
        mode = mh.get("mode", "normal")
        mode_txt = {"normal": "✅ bình thường",
                    "selective": f"⚕️ CHỌN LỌC — chỉ khuyến nghị BUY ≥ {config.MH_GATE_STRONG_SCORE:.0f}",
                    "halt": "⛔ TẠM NGỪNG khuyến nghị mới"}.get(mode, mode)
        mh_line = (f"\n\n**Sức khỏe thị trường: {mh['health']}/100 {mh['label']}** — {mode_txt} "
                   f"<small>(phiên phân phối: {mh['dist_days']} · breadth>MA20: "
                   f"{mh['breadth_pct'] if mh['breadth_pct'] is not None else '—'}% · "
                   f"canary lứa KN gần nhất: "
                   f"{mh['canary_pct'] if mh['canary_pct'] is not None else '—'}%)</small>")
    regime_pane.object = f"### {label}{ratio}\n{s.get('regime_msg','')}{mh_line}"

    last = s["last_scan"].strftime("%H:%M:%S") if s["last_scan"] else "—"
    status_pane.object = (f"Cập nhật: **{last}** · Universe {s['universe_total']} · "
                          f"Qua Layer-1 {s['universe_passed']} · Trạng thái: {s['status']}")

    # Layer 1 tables
    l1 = s["layer1"]
    if l1 is not None and not l1.empty:
        passed = l1[l1["passed"]]
        failed = l1[~l1["passed"]]
        l1_summary.object = (f"**{len(passed)}** mã qua Layer-1 · **{len(failed)}** mã bị loại "
                             f"(trên tổng {len(l1)} mã universe)")
        pc = [c for c in L1_PASS_COLS if c in passed.columns]
        l1_pass_table.value = passed[pc].round(2).reset_index(drop=True)
        fc = [c for c in L1_FAIL_COLS if c in failed.columns]
        l1_fail_table.value = failed[fc].reset_index(drop=True)
    else:
        l1_summary.object = ""
        l1_pass_table.value = pd.DataFrame(columns=L1_PASS_COLS)
        l1_fail_table.value = pd.DataFrame(columns=L1_FAIL_COLS)

    # Layer 2 ranking
    ranked = s["ranked"]
    if ranked is not None and not ranked.empty:
        view = ranked[ranked["buy_score"] >= s["settings"]["min_score"]]
        cols = [c for c in L2_COLS if c in view.columns]
        l2_table.value = view[cols].round(1).reset_index(drop=True)
    else:
        l2_table.value = pd.DataFrame(columns=L2_COLS)

    _refresh_tracking()


def _miss_reason(r) -> str:
    """Why the screener did NOT recommend this stock that day (coarse, from the snapshot)."""
    st = r.get("state")
    if st == "NONE":
        return "Không có setup (NONE)"
    if st == "BREAKOUT_LATE":
        return "🟠 Muộn — bị hạ điểm"
    return f"Dưới ngưỡng điểm ({r.get('buy_score', 0):.0f} < {config.ALERT_MIN_SCORE:.0f})"


def _refresh_pool():
    """Refresh the missed-winners tab (whole-pool false negatives)."""
    try:
        stats = db.pool_quality_stats()
        misses = db.load_missed_winners(config.MISS_MIN_RET_T3)
    except Exception:
        return
    if stats is None or stats.empty:
        pool_summary.object = ("*Đang tích lũy dữ liệu toàn pool: snapshot chạy ở phiên EOD "
                               "15:30 mỗi ngày (app phải đang chạy), kết quả T+3 có sau ~3 phiên.*")
        miss_table.value = pd.DataFrame(columns=MISS_COLS)
        return
    parts = []
    for flag, lbl in ((1, "✅ Được khuyến nghị"), (0, "⬜ Không khuyến nghị")):
        g = stats[stats["is_reco"] == flag]
        if len(g):
            n = int(g["n"].sum())
            win = (g["win_pct"] * g["n"]).sum() / n
            ret = (g["avg_ret"] * g["n"]).sum() / n
            line = f"{lbl}: **{n}** mã · win T+3 **{win:.0f}%** · return TB **{ret:+.2f}%**"
            n5 = int(pd.to_numeric(g["n5"], errors="coerce").fillna(0).sum())
            if n5:
                g5 = g[pd.to_numeric(g["n5"], errors="coerce") > 0]
                win5 = (g5["win5_pct"] * g5["n5"]).sum() / n5
                ret5 = (g5["avg_ret5"] * g5["n5"]).sum() / n5
                line += f" — T+5 ({n5} mã): win **{win5:.0f}%** · TB **{ret5:+.2f}%**"
            parts.append(line)
    pool_summary.object = ("**Chất lượng khuyến nghị (toàn pool Layer-1, đã đủ T+3):**  \n"
                           + "  \n".join(parts)
                           + f"  \n<small>Bảng dưới: mã KHÔNG khuyến nghị nhưng thắng ≥ "
                             f"{config.MISS_MIN_RET_T3:.0f}% tại T+3 — ứng viên review bỏ sót.</small>")
    if misses is None or misses.empty:
        miss_table.value = pd.DataFrame(columns=MISS_COLS)
        return
    misses["reason"] = misses.apply(_miss_reason, axis=1)
    misses["ret_t3"] = pd.to_numeric(misses["ret_t3"], errors="coerce").round(2)
    misses["close_ref"] = pd.to_numeric(misses["close_ref"], errors="coerce").round(0)
    misses["buy_score"] = pd.to_numeric(misses["buy_score"], errors="coerce").round(1)
    misses["review_cause"] = misses["review_cause"].fillna("⏳ chưa review")
    miss_table.value = misses[MISS_COLS].reset_index(drop=True)


def _refresh_tracking():
    """Load tracked signals + outcomes into the validation tab (Phase 3)."""
    _refresh_pool()
    try:
        df = db.load_tracking(300)
    except Exception:
        return
    if df is None or df.empty:
        track_summary.object = "*Chưa có tín hiệu nào được theo dõi. Sau mỗi phiên có mã được khuyến nghị, chúng sẽ xuất hiện ở đây và được đo return T+1..T+5.*"
        track_table.value = pd.DataFrame(columns=TRACK_COLS)
        return

    def _verdict(r):
        n = r["n_forward"]
        if n is None or pd.isna(n) or n < 3:
            return "⏳ chờ"
        out = "✅ Thắng" if r["win_t3"] == 1 else "❌ Thua"
        # Swing-window view (spec: vài phiên → 1-2 tuần): T+5 verdict when resolved
        r5 = r.get("ret_t5")
        if n >= 5 and r5 is not None and not pd.isna(r5):
            out += " · T+5 " + ("✅" if r5 > 0 else "❌")
        # Chance-to-take-profit: peak gain within the window cleared the bar
        mfe = r.get("mfe")
        if mfe is not None and not pd.isna(mfe) and mfe >= MFE_TAKE_PROFIT:
            out += " · 💰"
        return out

    df["verdict"] = df.apply(_verdict, axis=1)
    df["state"] = df["state"].map(config.STATE_LABELS).fillna(df["state"])
    for c in ("ret_t1", "ret_t2", "ret_t3", "ret_t4", "ret_t5", "mfe", "mae"):
        df[c] = pd.to_numeric(df[c], errors="coerce").round(2)
    df["reco_close"] = pd.to_numeric(df["reco_close"], errors="coerce").round(0)
    df["buy_score"] = pd.to_numeric(df["buy_score"], errors="coerce").round(1)

    resolved = df[pd.to_numeric(df["n_forward"], errors="coerce") >= 3]
    if len(resolved):
        wr = resolved["win_t3"].mean() * 100
        avg = pd.to_numeric(resolved["ret_t3"], errors="coerce").mean()
        parts = [f"**Tỷ lệ thắng T+3: {wr:.0f}%** trên {len(resolved)} tín hiệu đã đủ 3 phiên",
                 f"return T+3 trung bình **{avg:+.2f}%**",
                 f"tổng {len(df)} tín hiệu đang theo dõi"]
        track_summary.object = " · ".join(parts)
    else:
        track_summary.object = (f"{len(df)} tín hiệu đang theo dõi — chưa tín hiệu nào đủ "
                                "3 phiên (T+3) để chấm thắng/thua.")
    track_table.value = df[TRACK_COLS].reset_index(drop=True)


# ── Event handlers ───────────────────────────────────────────────────────────────
def _start_scan(override_regime=False):
    """Run a scan in a worker thread. override_regime forces Layer-2 scoring even
    when the market regime gate is blocked (manual run button on Tab 2)."""
    if _scanning.is_set():
        return
    store.update_settings(
        position_size=position_size.value,
        min_score=min_score.value,
        min_price=min_price.value,
        min_gtgd20=min_gtgd20_b.value * 1e9,
        exchanges=_selected_exchanges(),
    )
    _scanning.set()
    btn = manual_l2_button if override_regime else scan_button
    btn.loading = True
    status_pane.object = ("⏳ Đang chạy Layer 2 thủ công (bỏ qua chặn downtrend)..."
                          if override_regime else
                          "⏳ Đang quét... (lần đầu sau khi khởi động có thể mất ~2 phút)")

    # Capture the session document so the worker thread can push the UI update
    # reliably via add_next_tick_callback (canonical Bokeh cross-thread pattern),
    # independent of whether periodic callbacks fire on a shared served object.
    doc = pn.state.curdoc

    def _work():
        try:
            scheduler.run_full_scan(position_size=position_size.value,
                                    override_regime=override_regime)
        finally:
            _scanning.clear()
            scan_button.loading = False
            manual_l2_button.loading = False
            if doc is not None:
                doc.add_next_tick_callback(_refresh_view)
            else:
                _refresh_view()

    threading.Thread(target=_work, daemon=True).start()


def _do_scan(event=None):
    _start_scan(override_regime=False)


def _do_manual_l2(event=None):
    _start_scan(override_regime=True)


def _on_select(event):
    ranked = store.get()["ranked"]
    if not event.new or ranked is None or ranked.empty:
        return
    view = ranked[ranked["buy_score"] >= store.get_settings()["min_score"]].reset_index(drop=True)
    idx = event.new[0]
    if idx >= len(view):
        return
    row = view.iloc[idx]
    detail_pane.object = _render_detail(row)
    claude_button.disabled = False
    claude_button._row = row


def _on_claude(event):
    row = getattr(claude_button, "_row", None)
    if row is None:
        return
    bundle = _claude_bundle(row)
    os.makedirs(os.path.join(config.DATA_DIR, "claude_bundles"), exist_ok=True)
    path = os.path.join(config.DATA_DIR, "claude_bundles", f"{row['symbol']}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(bundle)
    claude_out.object = (f"✅ Đã lưu `{path}`. Mở Claude Desktop và dán nội dung dưới đây:\n\n"
                         f"```markdown\n{bundle}\n```")


scan_button.on_click(_do_scan)
manual_l2_button.on_click(_do_manual_l2)
l2_table.param.watch(_on_select, "selection")
claude_button.on_click(_on_claude)
min_score.param.watch(lambda e: _refresh_view(), "value")
show_failed.param.watch(lambda e: setattr(l1_fail_table, "visible", e.new), "value")


# ── Layout ────────────────────────────────────────────────────────────────────────
sidebar = pn.Column(
    pn.pane.Markdown("## ⚙️ Điều khiển"),
    scan_button,
    pn.pane.Markdown("*Universe = toàn bộ HOSE+HNX đạt ngưỡng thanh khoản "
                     "(chỉnh **GTGD20 tối thiểu** ở tab Layer 1).*",
                     styles={"font-size": "0.8em", "color": "#888"}),
    pn.layout.Divider(),
    pn.pane.Markdown(
        "**Thang điểm BUY**\n\n🟢 85–100 Rất mạnh\n\n🟢 75–84 Mạnh\n\n🟡 65–74 Khá\n\n"
        "🟠 50–64 Trung bình\n\n⚪ <50 Yếu", styles={"font-size": "0.85em"}),
    width=300,
)

layer1_tab = pn.Column(
    pn.pane.Markdown("### Tham số lọc thô (Layer 1)"),
    pn.Row(min_price, min_gtgd20_b),
    pn.Row(pn.pane.Markdown("**Sàn:**", width=60), ex_hose, ex_hnx),
    pn.pane.Markdown("*Đổi tham số rồi bấm **Quét ngay** ở thanh bên để áp dụng.*",
                     styles={"font-size": "0.8em", "color": "#888"}),
    pn.layout.Divider(),
    l1_summary,
    pn.pane.Markdown("#### ✅ Mã qua Layer 1"),
    l1_pass_table,
    show_failed,
    l1_fail_table,
)

layer2_tab = pn.Column(
    pn.pane.Markdown("### Chấm điểm BUY (Layer 2)"),
    pn.Row(position_size, min_score),
    manual_l2_button,
    pn.pane.Markdown("*Khi thị trường downtrend, job tự động chỉ chạy Layer 1. "
                     "Dùng nút trên để chấm điểm Layer 2 thủ công (chấp nhận rủi ro).*",
                     styles={"font-size": "0.8em", "color": "#888"}),
    pn.layout.Divider(),
    l2_table,
    pn.layout.Divider(),
    pn.pane.Markdown("### 🔎 Chi tiết"),
    detail_pane,
    pn.Row(claude_button),
    claude_out,
)

tracking_tab = pn.Column(
    pn.pane.Markdown("### 🎯 Theo dõi & Kiểm chứng khuyến nghị (T+2.5)"),
    pn.pane.Markdown("*Mỗi mã được app khuyến nghị (🟢 Mua ngay / 🔵 Sắp breakout) được ghi lại "
                     "với giá lúc khuyến nghị, rồi đo return các phiên sau. **T+3** = phiên bán "
                     "được đầu tiên theo T+2.5 → dùng làm thước đo Thắng/Thua.*",
                     styles={"font-size": "0.85em", "color": "#666"}),
    track_summary,
    pn.pane.Markdown(f"<small>⚖️ Trọng số BUY cố định: Thanh khoản {config.W_BUY['liquidity']:.2f} · "
                     f"Động lượng {config.W_BUY['momentum']:.2f} · Tín hiệu {config.W_BUY['signal']:.2f} "
                     f"— đã kiểm chứng trên backtest 10 năm (07/2026); cơ chế tự học đã gỡ bỏ, "
                     f"thay bằng Drift Alarm khi đủ dữ liệu live.</small>",
                     styles={"color": "#888"}),
    pn.layout.Divider(),
    track_table,
)

missed_tab = pn.Column(
    pn.pane.Markdown("### 📊 Bỏ sót — mã KHÔNG khuyến nghị nhưng thắng (toàn pool Layer-1)"),
    pn.pane.Markdown("*Mỗi phiên EOD app snapshot TOÀN BỘ mã qua Layer-1 (kể cả mã không được "
                     "khuyến nghị) và đo return T+3 từ giá đóng cửa. Tab này lộ ra các mã app "
                     "bỏ sót nhưng vẫn thắng lớn — nói **'review các mã bỏ sót'** để phân tích "
                     "vì sao bị loại (chart TradingView + dữ liệu vnstock) và cân nhắc nới công "
                     "thức nếu pattern lặp lại.*",
                     styles={"font-size": "0.85em", "color": "#666"}),
    pool_summary,
    pn.layout.Divider(),
    miss_table,
)

tabs = pn.Tabs(("📋 Layer 1 — Lọc thô", layer1_tab),
               ("🏆 Layer 2 — Chấm điểm", layer2_tab),
               ("🎯 Theo dõi — Kiểm chứng", tracking_tab),
               ("📊 Bỏ sót — Toàn pool", missed_tab))

main = pn.Column(regime_pane, status_pane, tabs)

template = pn.template.MaterialTemplate(
    title="🚀 Breakout Screener — Vietnam (Spec RevD)",
    sidebar=[sidebar], main=[main], header_background="#1b5e20",
)

# Register the auto-refresh PER SESSION. Doing this at module top-level would not
# bind to a session's document when serving a shared template object (the callback
# would never fire). pn.state.onload runs when each browser session connects.
def _on_session_load():
    _refresh_view()
    doc = pn.state.curdoc

    # Push a refresh whenever any scan updates the store (incl. background scheduler
    # scans, which have no session context). add_next_tick_callback is thread-safe.
    def _push():
        if doc is not None:
            try:
                doc.add_next_tick_callback(_refresh_view)
            except Exception:
                pass
        else:
            _refresh_view()

    store.add_listener(_push)
    # Belt-and-suspenders: also poll every 5s in case a push is missed.
    pn.state.add_periodic_callback(_refresh_view, period=5_000)


pn.state.onload(_on_session_load)
_refresh_view()  # initial render of the shared object

template.servable()
