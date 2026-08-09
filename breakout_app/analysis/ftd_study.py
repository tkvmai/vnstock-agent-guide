"""Nghiên cứu khả thi Follow-Through Day (FTD) trên kho backtest 10 năm — 05/08/2026.

Câu hỏi (thiết kế CỐ ĐỊNH trước khi nhìn kết quả): sau nhịp điều chỉnh, quy tắc FTD
của O'Neil có cho phép MỞ CỬA SỚM cho tín hiệu FRESH/PRE (trước khi regime gate chuyển
'ok') mà không phá kỳ vọng không?

- FTD: sau khi index rơi >= CORR_PCT từ đỉnh chạy (trailing 250 phiên), đếm rally
  attempt từ đáy điều chỉnh (ngày 1 = phiên tăng đầu tiên); thủng đáy rally -> reset.
  Ngày thứ FTD_D0..FTD_D1 của rally có phiên tăng >= thr% trên volume CAO HƠN phiên
  trước -> FTD. FTD fail = close thủng đáy rally trong FAIL_N phiên sau đó.
- Cửa sổ FTD = [ngày FTD .. min(ngày regime 'ok' đầu tiên, ngày FTD fail)] — đây là
  các phiên mà hệ hiện tại ĐỨNG NGOÀI còn quy tắc FTD cho vào.
- Tiêu chí chấp nhận (khai báo trước): objective = mean(ret_t5) − 0.3·mean(|mae5|)
  của tín hiệu trong cửa sổ FTD >= objective của baseline tín-hiệu-trong-regime-ok,
  trên CẢ TRAIN 2016-21 lẫn VALIDATION 2022-23. HOLDOUT 2024-26 giữ khóa.

Chạy:  python analysis/ftd_study.py
"""

import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CORR_PCT = 8.0          # điều chỉnh: rơi >=8% từ đỉnh trailing
FTD_D0, FTD_D1 = 4, 10  # cửa sổ ngày rally hợp lệ cho FTD (O'Neil 4-7, nới tới 10)
FAIL_N = 20             # FTD fail nếu thủng đáy rally trong 20 phiên
TRAIN = ("2016-01-01", "2021-12-31")
VALID = ("2022-01-01", "2023-12-31")


def load_vnindex():
    vn = pd.read_parquet("data/history/VNINDEX.parquet").reset_index(drop=True)
    vn["date"] = pd.to_datetime(vn["time"]).dt.strftime("%Y-%m-%d")
    vn["ma20"] = vn["close"].rolling(20).mean()
    vn["ma5"] = vn["close"].rolling(5).mean()
    vn["ratio"] = vn["close"] / vn["ma20"]
    vn["regime"] = np.where((vn["ratio"] < 0.97) & (vn["ma5"] < vn["ma20"]), "blocked",
                            np.where(vn["ratio"] < 1.0, "caution", "ok"))
    vn["peak"] = vn["close"].rolling(250, min_periods=50).max()
    vn["dd"] = (vn["close"] / vn["peak"] - 1) * 100
    return vn


def detect_ftds(vn: pd.DataFrame, thr: float):
    """Trả list dict {ftd, rally_low, day_no, end, end_reason} — máy trạng thái O'Neil."""
    ftds = []
    in_corr = False
    rally_day = 0
    rally_low = None
    i = 0
    n = len(vn)
    while i < n:
        r = vn.iloc[i]
        if not in_corr:
            # vào chế độ điều chỉnh chỉ khi regime CHƯA ok (đang bị gate chặn)
            if r["dd"] <= -CORR_PCT and r["regime"] != "ok":
                in_corr, rally_day, rally_low = True, 0, r["close"]
        else:
            prev = vn.iloc[i - 1]
            if r["regime"] == "ok":                         # hồi phục không cần FTD
                in_corr = False                             # (fix: thiếu lối thoát này
                i += 1                                      #  → rally đếm vô hạn, 0 FTD)
                continue
            if r["close"] < rally_low:                      # đáy mới -> reset rally
                rally_day, rally_low = 0, r["close"]
            elif rally_day == 0 and r["close"] > prev["close"]:
                rally_day = 1                               # rally attempt ngày 1
            elif rally_day >= 1:
                rally_day += 1
                gain = (r["close"] / prev["close"] - 1) * 100
                if (FTD_D0 <= rally_day <= FTD_D1 and gain >= thr
                        and r["volume"] > prev["volume"]):
                    # FTD! Xác định cửa sổ: tới regime ok đầu tiên hoặc fail
                    end_i, reason = None, None
                    for j in range(i + 1, min(i + 1 + FAIL_N * 3, n)):
                        if vn.iloc[j]["close"] < rally_low and j <= i + FAIL_N:
                            end_i, reason = j, "fail"
                            break
                        if vn.iloc[j]["regime"] == "ok":
                            end_i, reason = j, "regime_ok"
                            break
                    if end_i is None:
                        end_i, reason = min(i + FAIL_N, n - 1), "timeout"
                    ftds.append({"ftd": r["date"], "day_no": rally_day,
                                 "gain": round(gain, 2),
                                 "end": vn.iloc[end_i]["date"], "end_reason": reason,
                                 "lag_sessions": end_i - i})
                    # thoát chế độ điều chỉnh; nếu fail thì quay lại corr từ ngày fail
                    if reason == "fail":
                        in_corr, rally_day, rally_low = True, 0, vn.iloc[end_i]["close"]
                    else:
                        in_corr = False
                    i = end_i
        i += 1
    return ftds


def load_signals():
    files = sorted(glob.glob("data/backtest/bt_*.parquet"))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    from analysis.manipulation_blacklist import tag
    df = tag(df)
    df = df[~df["manip"]]
    sig = df[(df["state"].isin(["BREAKOUT_FRESH", "PRE_BREAKOUT"]))
             & (df["buy_score"] >= 50)].dropna(subset=["ret_t5", "mae5"]).copy()
    return sig


def objective(g):
    return g["ret_t5"].mean() - 0.3 * g["mae5"].abs().mean()


def stats(g):
    if g.empty:
        return "n=0"
    return (f"n={len(g):4d} · win_t3 {100*(g['ret_t3']>0).mean():4.1f}% · "
            f"ret_t3 {g['ret_t3'].mean():+5.2f} · ret_t5 {g['ret_t5'].mean():+5.2f} · "
            f"MAE {g['mae5'].mean():+5.2f} · OBJ {objective(g):+6.3f}")


def main():
    vn = load_vnindex()
    regime_of = dict(zip(vn["date"], vn["regime"]))
    sig = load_signals()
    sig["regime"] = sig["date"].map(regime_of)

    for thr in (1.5, 2.0):
        print(f"\n{'='*78}\n### Ngưỡng FTD gain >= {thr}% (volume phiên sau > phiên trước)")
        ftds = detect_ftds(vn, thr)
        for lo, hi, name in (TRAIN + ("TRAIN 2016-21",),) and [
                (*TRAIN, "TRAIN 2016-21"), (*VALID, "VALID 2022-23")]:
            f_in = [f for f in ftds if lo <= f["ftd"] <= hi]
            n_fail = sum(1 for f in f_in if f["end_reason"] == "fail")
            print(f"\n-- {name}: {len(f_in)} FTD · fail {n_fail} "
                  f"({100*n_fail/len(f_in):.0f}%)" if f_in else f"\n-- {name}: 0 FTD")
            window_dates = set()
            for f in f_in:
                days = vn[(vn["date"] >= f["ftd"]) & (vn["date"] < f["end"])]["date"]
                window_dates.update(days)
                print(f"   FTD {f['ftd']} (ngày {f['day_no']}, +{f['gain']}%) → "
                      f"{f['end']} [{f['end_reason']}, {f['lag_sessions']} phiên]")
            s = sig[(sig["date"] >= lo) & (sig["date"] <= hi)]
            in_win = s[s["date"].isin(window_dates)]
            base_ok = s[s["regime"] == "ok"]
            blocked_no_ftd = s[(s["regime"].isin(["caution", "blocked"]))
                               & (~s["date"].isin(window_dates))]
            print(f"   Tín hiệu TRONG cửa sổ FTD : {stats(in_win)}")
            print(f"   Baseline regime ok        : {stats(base_ok)}")
            print(f"   Caution/blocked ngoài FTD : {stats(blocked_no_ftd)}")


if __name__ == "__main__":
    main()
