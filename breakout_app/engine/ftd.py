"""Follow-Through Day (O'Neil) — detector OBSERVE-ONLY (05/08/2026).

Nghiên cứu `analysis/ftd_study.py` trên kho backtest 10 năm: tín hiệu FRESH/PRE
trong cửa sổ [FTD → regime ok] tốt hơn baseline trên CẢ train (obj +0.57 vs −0.07)
lẫn validation (+2.49 vs −0.74, n=3) — NHƯNG tổng giá trị nhỏ: regime gate tự mở
lại chỉ 1-5 phiên sau FTD (MA20 sập theo giá nên close/MA20 hồi nhanh), ~vài tín
hiệu/năm, mẫu validation quá mỏng. Kết luận: CHƯA nối vào gate. Module này chỉ
QUAN SÁT — banner + log + Telegram 1 lần/sự kiện — chờ 3-5 FTD live rồi quyết.

Máy trạng thái (đúng bản study, kể cả bug-fix "thoát corr khi regime ok"):
  - Vào chế độ điều chỉnh khi index rơi >= corr_pct từ đỉnh chạy VÀ regime chưa ok.
  - Rally attempt: ngày 1 = phiên tăng đầu tiên sau đáy điều chỉnh; thủng đáy → reset.
  - FTD = ngày rally thứ d0..d1 tăng >= gain_min% trên volume CAO hơn phiên trước.
  - Cửa sổ FTD kết thúc khi regime chuyển ok (van hết vai trò) hoặc thủng đáy rally
    trong fail_n phiên (FTD fail).

Thuần (không I/O): nhận DataFrame VN-Index, trả trạng thái tại bar cuối.
Lưu ý live: lịch sử fetch ~200-300 phiên → đỉnh chạy trailing ngắn hơn bản study
(250 phiên) một chút; chấp nhận được cho mục đích quan sát.
"""

import numpy as np
import pandas as pd

CORR_PCT = 8.0
GAIN_MIN = 1.5
FTD_D0, FTD_D1 = 4, 10
FAIL_N = 20


def ftd_state(vn: pd.DataFrame) -> dict:
    """Trạng thái FTD tại bar cuối của ``vn`` (cần cột close/volume, >= 60 bars).

    Trả dict: phase ('none'|'correction'|'rally'|'ftd_window'), rally_day, dd,
    ftd {date, day_no, gain} của FTD đang trong cửa sổ (None nếu không có),
    last_ftd (FTD gần nhất kể cả đã đóng cửa sổ, để hiển thị lịch sử)."""
    if vn is None or len(vn) < 60 or "volume" not in vn.columns:
        return {"phase": "none", "rally_day": 0, "dd": None, "ftd": None, "last_ftd": None}
    d = vn.reset_index(drop=True)
    dates = (d["time"].dt.strftime("%Y-%m-%d") if "time" in d.columns
             else pd.Series([str(i) for i in range(len(d))]))
    close = d["close"].astype(float)
    ma20 = close.rolling(20).mean()
    ma5 = close.rolling(5).mean()
    ratio = close / ma20
    regime_ok = (ratio >= 1.0)
    peak = close.rolling(250, min_periods=50).max()
    dd = (close / peak - 1) * 100

    in_corr, rally_day, rally_low = False, 0, None
    ftd_open = None          # FTD đang trong cửa sổ
    last_ftd = None
    vol = d["volume"].astype(float)
    for i in range(1, len(d)):
        if not in_corr:
            if ftd_open is not None:
                # cửa sổ FTD đang mở: đóng khi regime ok hoặc fail (thủng đáy rally)
                if regime_ok.iloc[i]:
                    ftd_open = None
                elif close.iloc[i] < ftd_open["rally_low"]:
                    if i - ftd_open["i"] <= FAIL_N:
                        last_ftd = {**last_ftd, "failed": True}
                    ftd_open = None
                    in_corr, rally_day, rally_low = True, 0, close.iloc[i]
                continue
            if dd.iloc[i] is not None and dd.iloc[i] <= -CORR_PCT and not regime_ok.iloc[i]:
                in_corr, rally_day, rally_low = True, 0, close.iloc[i]
        else:
            if regime_ok.iloc[i]:                    # hồi phục không cần FTD
                in_corr = False
                continue
            if close.iloc[i] < rally_low:            # đáy mới → reset rally
                rally_day, rally_low = 0, close.iloc[i]
            elif rally_day == 0 and close.iloc[i] > close.iloc[i - 1]:
                rally_day = 1
            elif rally_day >= 1:
                rally_day += 1
                gain = (close.iloc[i] / close.iloc[i - 1] - 1) * 100
                if (FTD_D0 <= rally_day <= FTD_D1 and gain >= GAIN_MIN
                        and vol.iloc[i] > vol.iloc[i - 1]):
                    last_ftd = {"date": dates.iloc[i], "day_no": rally_day,
                                "gain": round(gain, 2), "failed": False}
                    ftd_open = {"i": i, "rally_low": rally_low, **last_ftd}
                    in_corr = False

    if ftd_open is not None:
        phase = "ftd_window"
    elif in_corr:
        phase = "rally" if rally_day >= 1 else "correction"
    else:
        phase = "none"
    return {"phase": phase, "rally_day": int(rally_day) if in_corr else 0,
            "dd": round(float(dd.iloc[-1]), 1) if dd.iloc[-1] == dd.iloc[-1] else None,
            "ftd": ({k: ftd_open[k] for k in ("date", "day_no", "gain")}
                    if ftd_open is not None else None),
            "last_ftd": last_ftd}
