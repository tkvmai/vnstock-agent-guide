"""Sức khỏe thị trường (Market Health) — lớp quan sát, GIAI ĐOẠN 1 (chưa can thiệp alert).

Không dự đoán *thời điểm* điều chỉnh (bất khả thi về nguyên lý) — chỉ đo *độ mong manh*
của thị trường qua 4 thành phần có bằng chứng thực nghiệm:

  1. dist_days   — số "phiên phân phối" O'Neil trong 25 phiên: index giảm >0.2% với
                   volume CAO hơn phiên trước (tổ chức đang xả). 4-6 phiên → rủi ro cao.
  2. breadth_pct — % mã trong pool đang đóng cửa TRÊN MA20 của chính nó. Index cao nhưng
                   breadth co lại = phân phối dưới bề mặt (phân kỳ độ rộng).
  3. canary_pct  — "chim hoàng yến" leadership: % tín hiệu đã KN trong 1-2 phiên gần nhất
                   hiện còn ≥ giá đóng cửa ngày KN. Breakout chết hàng loạt ngay T+1 là
                   tín hiệu sớm nhất của dòng tiền rút (đã thấy rõ tối 08/07/2026: lứa 7/7
                   đỏ toàn bộ trong khi index +0.29%).
  4. index_ratio — VN-Index / MA20 (thành phần regime gate hiện có).

    health = 0.30·dist + 0.30·breadth + 0.20·canary + 0.20·index   (0..100)

Module này THUẦN (không I/O): nhận các đại lượng đã tính, trả điểm. Việc gom dữ liệu
nằm ở scheduler; backtest ở analysis/market_health_backtest.py.
"""

import numpy as np
import pandas as pd

W_HEALTH = {"dist": 0.30, "breadth": 0.30, "canary": 0.20, "index": 0.20}

HEALTH_BANDS = [(30, "🔴 Rất yếu"), (50, "🟠 Yếu"), (70, "🟡 Trung tính"), (101, "🟢 Khỏe")]


def count_distribution_days(index_df: pd.DataFrame, lookback: int = 25,
                            drop_pct: float = 0.2, rally_expire_pct: float = 5.0) -> int:
    """Số phiên phân phối O'Neil CÒN HIỆU LỰC trong ``lookback`` phiên gần nhất.

    Phiên phân phối = index giảm > drop_pct% với volume CAO hơn phiên trước.
    **Luật hết hạn O'Neil (Phase 2, 19/07):** một phiên phân phối bị XÓA khỏi sổ khi
    index đã có lúc đóng cửa cao hơn close của phiên đó ≥ rally_expire_pct% (áp lực
    bán coi như được hấp thụ). Thiếu luật này bộ đếm bão hòa 6-7 ngay trong uptrend
    (lỗi phát hiện ở backtest phase 1, tháng 6/2026)."""
    d = index_df.tail(lookback + 1).reset_index(drop=True)
    if len(d) < 2 or "volume" not in d.columns:
        return 0
    close = d["close"].to_numpy(dtype=float)
    vol = d["volume"].to_numpy(dtype=float)
    n = 0
    for i in range(1, len(d)):
        drop = (close[i] / close[i - 1] - 1) * 100 < -drop_pct
        vol_up = vol[i] > vol[i - 1]
        if not (drop and vol_up):
            continue
        # hết hạn nếu SAU phiên i index từng đóng cửa cao hơn close[i] ≥ ngưỡng rally
        future_max = close[i + 1:].max() if i + 1 < len(close) else -np.inf
        if future_max >= close[i] * (1 + rally_expire_pct / 100):
            continue
        n += 1
    return int(n)


def breadth_above_ma20(closes_by_symbol: dict) -> float:
    """% mã có close hiện tại > MA20 của chính nó. ``closes_by_symbol`` =
    {symbol: pd.Series close, phần tử cuối là giá hiện tại}."""
    above = total = 0
    for s, series in closes_by_symbol.items():
        if series is None or len(series) < 21:
            continue
        ma20 = float(series.iloc[-21:-1].mean())     # MA20 của 20 phiên TRƯỚC
        if not ma20:
            continue
        total += 1
        if float(series.iloc[-1]) > ma20:
            above += 1
    return (above / total * 100) if total else float("nan")


def _score_dist(n: int) -> float:
    if n <= 1:
        return 100
    return {2: 80, 3: 60, 4: 40, 5: 20}.get(n, 0)


def _score_breadth(pct: float) -> float:
    if pct != pct:                      # NaN — thiếu dữ liệu
        return 70
    if pct >= 70:
        return 100
    if pct >= 55:
        return 80
    if pct >= 40:
        return 60
    if pct >= 25:
        return 40
    return 20


def _score_canary(pct: float) -> float:
    """pct = % tín hiệu KN 1-2 phiên gần nhất còn ≥ giá đóng ngày KN (None = thiếu)."""
    if pct is None or pct != pct:
        return 70                       # trung tính khi không có lứa gần đây
    if pct >= 60:
        return 100
    if pct >= 40:
        return 70
    if pct >= 20:
        return 40
    return 10


def _score_index(ratio: float) -> float:
    if ratio != ratio:
        return 70
    if ratio >= 1.0:
        return 100
    if ratio >= 0.99:
        return 80
    if ratio >= 0.97:
        return 55
    return 20


def score_market_health(dist_days: int, breadth_pct: float, canary_pct,
                        index_ratio: float) -> dict:
    s = {"score_dist": _score_dist(dist_days),
         "score_breadth": _score_breadth(breadth_pct),
         "score_canary": _score_canary(canary_pct),
         "score_index": _score_index(index_ratio)}
    health = (W_HEALTH["dist"] * s["score_dist"] + W_HEALTH["breadth"] * s["score_breadth"]
              + W_HEALTH["canary"] * s["score_canary"] + W_HEALTH["index"] * s["score_index"])
    label = next(lbl for thr, lbl in HEALTH_BANDS if health < thr)
    return {"health": round(health, 1), "label": label,
            "dist_days": dist_days,
            "breadth_pct": round(breadth_pct, 1) if breadth_pct == breadth_pct else None,
            "canary_pct": (round(canary_pct, 1)
                           if canary_pct is not None and canary_pct == canary_pct else None),
            "index_ratio": round(index_ratio, 4) if index_ratio == index_ratio else None,
            **s}
