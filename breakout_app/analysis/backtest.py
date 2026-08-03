"""Phase B — Máy backtest: replay engine RevD trên toàn bộ lịch sử (~2016→nay).

Nguyên tắc:
  - Dùng NGUYÊN BẢN ``engine/`` (scoring.score_stock) — không fork logic, nên mọi
    thay đổi công thức tương lai backtest lại được ngay.
  - Universe point-in-time: mỗi ngày lấy TOP ``--top`` mã thanh khoản nhất (GTGD20
    percentile) trong số mã có ≥65 phiên dữ liệu TÍNH ĐẾN ngày đó — gồm cả mã nay đã
    hủy niêm yết (chống survivorship bias). Không dùng ngưỡng VND cứng (giá điều
    chỉnh + quy mô thị trường đổi theo thời gian).
  - Xấp xỉ EOD (ghi trong DEVELOPMENT #32): entry = close ngày tín hiệu;
    volume_intraday = volume cả phiên (time_ratio=1); filter intraday #6 coi như
    pass (intraday_ratio=100 cho mọi mã — hằng số, không ảnh hưởng xếp hạng);
    không có flow ngoại/tự doanh (score_flow trung tính).
  - Ghi TOÀN BỘ pool đã chấm (kể cả state NONE) + outcome T+1..T+10, MFE/MAE 5 phiên
    → phân tích hậu kỳ (so band, test rule mới) không cần chạy lại.

Kết quả: ``data/backtest/bt_<năm>.parquet`` (resume theo năm — xóa file năm nào thì
chạy lại năm đó).

Run:
    & python breakout_app/analysis/backtest.py                     # toàn bộ 2016→nay
    & python breakout_app/analysis/backtest.py --start 2024-01-01 --end 2024-03-31
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import config
from engine import scoring

HIST_DIR = os.path.join(config.DATA_DIR, "history")
BT_DIR = os.path.join(config.DATA_DIR, "backtest")
MIN_BARS = 65          # đủ cho MA50 + slope(10) + buffer (spec cần ≥60)
CV_CAP = 200.0
FWD_N = 10             # số phiên forward lưu outcome

# Các cột thô/điểm cần giữ lại cho phân tích hậu kỳ (ngoài core).
# Mở rộng 19/07 (kế hoạch calibrate W1): đủ metric thô để shadow-scorer tính lại MỌI
# band/weight offline không cần chạy lại engine, + điểm nhóm để parity-test.
KEEP_COLS = [
    "state", "buy_score", "signal", "setup_score", "liquidity", "momentum", "breakout",
    "overheat_mult", "overhead_mult", "state_mult", "breakout_age", "close",
    "bo_breakout_ratio", "bo_dist_to_high", "bo_volume_ratio", "bo_dry_up_ratio",
    "bo_narrowing_ratio", "bo_closing_strength", "bo_recent_above",
    "bo_score_price_fresh", "bo_age_factor",
    "mom_rsi", "mom_return_1d", "mom_return_5d", "mom_return_20d",
    "mom_price_vs_ma20", "mom_price_vs_ma50", "mom_ma20_vs_ma50",
    "mom_slope_ma20", "mom_slope_ma50", "mom_rs_1m", "mom_rs_3m", "mom_rs_weighted",
    "mom_macd_hist_pct", "mom_consistency_mult", "mom_ad_ratio",
    "mom_composite", "mom_ma", "mom_rs", "mom_flow", "mom_technical",
    "liq_gtgd20", "liq_cv", "liq_safety_ratio",
    "liq_score_gtgd20", "liq_score_intraday", "liq_score_cv",
    "setup_dist_below_pivot", "setup_score_proximity", "setup_score_setup_structure",
]


def load_history():
    """{symbol: DataFrame(time<datetime>, o,h,l,c,v)} + VNINDEX df."""
    data = {}
    for f in os.listdir(HIST_DIR):
        if not f.endswith(".parquet") or f.startswith("_"):
            continue
        sym = f[:-8]
        df = pd.read_parquet(os.path.join(HIST_DIR, f))
        df["time"] = pd.to_datetime(df["time"])
        data[sym] = df.reset_index(drop=True)
    vn = data.pop("VNINDEX", None)
    return data, vn


def run(start: str, end: str, top_n: int):
    os.makedirs(BT_DIR, exist_ok=True)
    print("Nạp lịch sử…")
    data, vn = load_history()
    print(f"  {len(data)} mã · VNINDEX {len(vn)} phiên")

    # Chuẩn bị mảng nhanh theo mã: date-string → index, GTGD series
    idx_of = {}
    for sym, df in data.items():
        idx_of[sym] = {d: i for i, d in enumerate(df["time"].dt.strftime("%Y-%m-%d"))}
    vn_dates = vn["time"].dt.strftime("%Y-%m-%d").tolist()
    days = [d for d in vn_dates if start <= d <= end]
    years = sorted({d[:4] for d in days})

    for year in years:
        out_path = os.path.join(BT_DIR, f"bt_{year}.parquet")
        if os.path.exists(out_path):
            print(f"{year}: đã có, bỏ qua (xóa file để chạy lại)")
            continue
        ydays = [d for d in days if d[:4] == year]
        rows, t0 = [], time.time()
        for k, d in enumerate(ydays, 1):
            vi = vn_dates.index(d)
            if vi < 20:
                continue
            vn_hist = vn.iloc[: vi + 1][["close"]].reset_index(drop=True)

            # Universe point-in-time: GTGD20 của 20 phiên TRƯỚC d, top-N
            cands = []
            for sym, df in data.items():
                i = idx_of[sym].get(d)
                if i is None or i < MIN_BARS:
                    continue
                g = (df["close"].iloc[i - 20:i] * df["volume"].iloc[i - 20:i])
                m = float(g.mean())
                if not m or m != m:
                    continue
                cv = float(g.std(ddof=0) / m * 100)
                if cv >= CV_CAP:
                    continue
                cands.append((sym, i, m, cv))
            cands.sort(key=lambda x: -x[2])
            for sym, i, gtgd20, cv in cands[:top_n]:
                df = data[sym]
                bar = df.iloc[i]
                hist = df.iloc[:i]
                live = {"close": float(bar["close"]), "high": float(bar["high"]),
                        "low": float(bar["low"]), "volume": float(bar["volume"]),
                        "minutes_elapsed": 225}
                metrics = {"gtgd20": gtgd20, "cv": cv, "intraday_ratio": 100.0,
                           "time_ratio": 1.0, "volume_intraday": live["volume"],
                           "gtgd_intraday": live["close"] * live["volume"]}
                try:
                    res = scoring.score_stock(sym, hist, live, vn_hist, flow=None,
                                              metrics=metrics)
                except Exception:
                    continue
                row = {"date": d, "symbol": sym,
                       **{c: res.get(c) for c in KEEP_COLS}}
                # Outcome forward từ chính series (cùng hệ điều chỉnh)
                closes = df["close"].iloc[i + 1: i + 1 + FWD_N].tolist()
                entry = live["close"]
                rets = [(c / entry - 1) * 100 for c in closes]
                for n in (1, 2, 3, 5, 10):
                    row[f"ret_t{n}"] = rets[n - 1] if len(rets) >= n else None
                row["mfe5"] = max(rets[:5]) if len(rets) >= 1 else None
                row["mae5"] = min(rets[:5]) if len(rets) >= 1 else None
                rows.append(row)
            if k % 25 == 0 or k == len(ydays):
                el = time.time() - t0
                print(f"  {year}: {k}/{len(ydays)} phiên · {len(rows):,} dòng · "
                      f"{el:.0f}s (~{el / k:.1f}s/phiên)")
        pd.DataFrame(rows).to_parquet(out_path, index=False)
        print(f"{year}: LƯU {len(rows):,} dòng → {out_path}")

    print("\nHoàn tất Phase B run.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--top", type=int, default=120, help="universe size mỗi ngày (percentile thanh khoản)")
    a = ap.parse_args()
    run(a.start, a.end, a.top)
