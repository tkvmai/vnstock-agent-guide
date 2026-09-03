"""Backtest screen "Dòng tiền thông minh" trên lịch sử flow 2019→nay (21/08/2026).

Giao thức CỐ ĐỊNH trước khi nhìn kết quả:
- Luật BASE (đúng bản live): GTGD20(trước) >= 15 tỷ · vol >= 1.5×MA20(trước) ·
  (NN_net_ngày >= 2% nền GTGD5(trước) HOẶC TD_net >= 3%).
- Hai mốc vào lệnh: close ngày tín hiệu (D0, lý thuyết — tin EOD 15:30) và close
  D+1 (thực tế theo được). Đo T+3/T+5/T+10 close-to-close cùng chuỗi (đã điều
  chỉnh), MFE/MAE 5 phiên. KHÔNG hiệu chỉnh cổ tức tiền mặt (thiếu lịch cho toàn
  universe lịch sử — méo ~+0.1-0.2đ đều tay cả pick lẫn baseline, ghi rõ).
- Baseline: mọi mã-ngày qua sàn thanh khoản (cùng vũ trụ, cùng kỳ).
- Loại blacklist thao túng (analysis/manipulation_blacklist.py).
- Biến thể đánh giá trên 2019-23, XÁC NHẬN 2024-26 (không tune trên 2024-26):
  chỉ-NN, chỉ-TD, NN>=5% (very strong), override "NN>=20% thì vol chỉ cần >=1.0×".

Chạy:  python analysis/sm_screen_backtest.py
"""

import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FLOW_DIR = "data/flow_history"
HIST_DIR = "data/history"
GTGD_FLOOR = 15e9
VOLR_MIN, FR_MIN, PR_MIN = 1.5, 2.0, 3.0
PERIODS = [("2019-01-01", "2021-12-31", "2019-21"),
           ("2022-01-01", "2023-12-31", "2022-23"),
           ("2024-01-01", "2026-12-31", "2024-26")]


def build_panel() -> pd.DataFrame:
    rows = []
    for fp in sorted(glob.glob(os.path.join(FLOW_DIR, "*.parquet"))):
        sym = os.path.basename(fp)[:-8]
        hp = os.path.join(HIST_DIR, f"{sym}.parquet")
        if not os.path.exists(hp):
            continue
        h = pd.read_parquet(hp, columns=["time", "close", "volume"])
        h["date"] = pd.to_datetime(h["time"]).dt.strftime("%Y-%m-%d")
        h = h[h["date"] >= "2018-10-01"].reset_index(drop=True)
        if len(h) < 60:
            continue
        fl = pd.read_parquet(fp)
        h = h.merge(fl, on="date", how="left")
        c, v = h["close"].to_numpy(float), h["volume"].to_numpy(float)
        gtgd = c * v
        h["vol_ma20"] = pd.Series(v).rolling(20).mean().shift(1)
        h["gtgd20"] = pd.Series(gtgd).rolling(20).mean().shift(1)
        h["gtgd5"] = pd.Series(gtgd).rolling(5).mean().shift(1)
        h["vol_ratio"] = v / h["vol_ma20"]
        h["fr_pct"] = h["fr_net"] / h["gtgd5"] * 100
        h["prop_pct"] = h["prop_net"] / h["gtgd5"] * 100
        # forward returns từ D0 và D1
        for base, tag in ((0, ""), (1, "_d1")):
            entry = pd.Series(c).shift(-base)
            for n in (3, 5, 10):
                h[f"ret_t{n}{tag}"] = (pd.Series(c).shift(-(base + n)) / entry - 1) * 100
        fw = pd.concat([(pd.Series(c).shift(-k) / pd.Series(c) - 1) * 100
                        for k in range(1, 6)], axis=1)
        h["mfe5"] = fw.max(axis=1)
        h["mae5"] = fw.min(axis=1)
        h["symbol"] = sym
        rows.append(h[h["date"] >= "2019-01-01"][[
            "symbol", "date", "close", "vol_ratio", "gtgd20", "fr_net", "prop_net",
            "fr_pct", "prop_pct", "ret_t3", "ret_t5", "ret_t10",
            "ret_t3_d1", "ret_t5_d1", "mfe5", "mae5"]])
    df = pd.concat(rows, ignore_index=True)
    from analysis.manipulation_blacklist import tag
    df = tag(df)
    df = df[~df["manip"]]
    return df[df["gtgd20"] >= GTGD_FLOOR].dropna(subset=["vol_ratio", "gtgd5"]
             if "gtgd5" in df.columns else ["vol_ratio"]).reset_index(drop=True)


def stats(g, label, d1=False):
    if len(g) == 0:
        return f"{label:<28} n=0"
    r3 = g["ret_t3_d1"] if d1 else g["ret_t3"]
    r5 = g["ret_t5_d1"] if d1 else g["ret_t5"]
    return (f"{label:<28} n={len(g):6,} · win3 {100*(r3>0).mean():4.1f}% · "
            f"T+3 {r3.mean():+5.2f} · T+5 {r5.mean():+5.2f} · "
            f"T+10 {g['ret_t10'].mean():+5.2f} · MAE {g['mae5'].mean():+5.2f}")


def main():
    df = build_panel()
    print(f"Panel: {len(df):,} mã-ngày qua sàn thanh khoản · {df['symbol'].nunique()} mã · "
          f"{df['date'].min()} → {df['date'].max()}")
    has_flow = df["fr_net"].notna()
    print(f"Có dữ liệu NN: {100*has_flow.mean():.0f}% dòng · có TD: "
          f"{100*df['prop_net'].notna().mean():.0f}%")

    base_pick = ((df["vol_ratio"] >= VOLR_MIN)
                 & ((df["fr_pct"] >= FR_MIN) | (df["prop_pct"] >= PR_MIN)))
    variants = {
        "BASE (live rule)": base_pick,
        "chỉ NN >=2% (vol>=1.5)": (df["vol_ratio"] >= VOLR_MIN) & (df["fr_pct"] >= FR_MIN),
        "chỉ TD >=3% (vol>=1.5)": (df["vol_ratio"] >= VOLR_MIN) & (df["prop_pct"] >= PR_MIN),
        "NN >=5% (vol>=1.5)": (df["vol_ratio"] >= VOLR_MIN) & (df["fr_pct"] >= 5),
        "override NN>=20%,vol>=1.0": (((df["vol_ratio"] >= VOLR_MIN)
                                       & ((df["fr_pct"] >= FR_MIN) | (df["prop_pct"] >= PR_MIN)))
                                      | ((df["vol_ratio"] >= 1.0) & (df["fr_pct"] >= 20))),
    }
    for lo, hi, name in PERIODS:
        sel = (df["date"] >= lo) & (df["date"] <= hi)
        d = df[sel]
        print(f"\n=== {name} ===")
        print(stats(d, "POOL (baseline)"))
        for vn, mask in variants.items():
            print(stats(df[sel & mask], vn))
        print("  -- vào lệnh D+1 (thực tế) --")
        print(stats(d, "POOL (D+1)", d1=True))
        print(stats(df[sel & base_pick], "BASE (D+1)", d1=True))

    # Insight cắt lớp trên 2019-23 (không đụng 2024-26)
    eva = (df["date"] <= "2023-12-31")
    print("\n=== CẮT LỚP (2019-23, BASE picks) ===")
    picks = df[eva & base_pick].copy()
    # theo sức mạnh NN
    picks["fr_bucket"] = pd.cut(picks["fr_pct"], [-1e9, 2, 5, 10, 20, 1e9],
                                labels=["<2 (TD-only)", "2-5", "5-10", "10-20", ">20"])
    for b, g in picks.groupby("fr_bucket", observed=True):
        print(stats(g, f"NN {b}"))
    # theo vị trí giá so đỉnh 20 phiên (screen bắt đáy hay bắt đỉnh?)
    print("  -- vị trí giá: cách đỉnh 20 phiên --")
    top20 = {}
    for sym, g in df[eva].groupby("symbol"):
        top20[sym] = g.set_index("date")["close"].rolling(20).max().shift(1)
    dist = []
    for _, r in picks.iterrows():
        t = top20.get(r["symbol"])
        p = t.get(r["date"]) if t is not None else None
        dist.append((r["close"] / p - 1) * 100 if p and p == p else None)
    picks["dist20"] = dist
    picks["pos"] = pd.cut(picks["dist20"], [-1e9, -10, -3, 0, 1e9],
                          labels=["sâu dưới đỉnh <-10%", "-10..-3%", "-3..0%", "vượt đỉnh"])
    for b, g in picks.groupby("pos", observed=True):
        print(stats(g, f"vị trí {b}"))
    out = "data/backtest/sm_screen_picks.parquet"
    picks_all = df[base_pick]
    picks_all.to_parquet(out, index=False)
    print(f"\nLưu {len(picks_all):,} picks → {out}")


if __name__ == "__main__":
    main()
