# -*- coding: utf-8 -*-
"""Market Health Phase 2 — backtest gating trên 10 năm (Việc 2).

Tính điểm Sức khỏe thị trường MỖI NGÀY 2016-2026 (bộ đếm phân phối ĐÃ SỬA luật hết
hạn O'Neil) rồi kiểm định: điều tiết alert theo health có cải thiện objective đã cam
kết (retT5 − 0.3·|MAE5|) so với baseline chỉ-có-regime-gate không?

Các biến thể gating (tune ngưỡng trên TRAIN 2016-21, cổng VALIDATION 2022-23):
  HARD   — health < X       → bỏ toàn bộ pick của ngày đó
  GRAD   — health < X       → nâng ngưỡng BUY lên 65 (chỉ giữ pick mạnh);
           health < X − 15  → bỏ hết
  LATEOFF— health < X       → tắt riêng kênh LATE (kênh nhạy đảo chiều nhất)

Run:
    & python breakout_app/analysis/mh_phase2.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

import config
from engine import market_health as MH
import tuner

HIST_DIR = os.path.join(config.DATA_DIR, "history")


def close_matrix() -> pd.DataFrame:
    """Ma trận (date × symbol) close từ kho lịch sử — cho breadth + canary."""
    frames = []
    for f in os.listdir(HIST_DIR):
        if not f.endswith(".parquet") or f.startswith("_") or f == "VNINDEX.parquet":
            continue
        df = pd.read_parquet(os.path.join(HIST_DIR, f), columns=["time", "close"])
        df["symbol"] = f[:-8]
        frames.append(df)
    long = pd.concat(frames, ignore_index=True)
    return long.pivot_table(index="time", columns="symbol", values="close", aggfunc="last")


def daily_health(picks_for_canary: pd.DataFrame) -> pd.DataFrame:
    """Chuỗi health mỗi ngày (dist đã sửa + breadth + canary + index)."""
    vn = pd.read_parquet(os.path.join(HIST_DIR, "VNINDEX.parquet"))
    vn["ds"] = vn["time"].astype(str).str[:10]
    px = close_matrix()
    ma20 = px.rolling(20).mean().shift(1)
    above = (px > ma20)
    valid = px.notna() & ma20.notna()
    breadth_series = (above & valid).sum(axis=1) / valid.sum(axis=1).replace(0, np.nan) * 100
    breadth_series.index = breadth_series.index.astype(str).str[:10]

    ratio_ma20 = vn["close"] / vn["close"].rolling(20).mean()

    picks_by_day = {d: g[["symbol"]].assign(entry=g["close"].values)
                    for d, g in picks_for_canary.groupby("date")}
    px_str = px.copy()
    px_str.index = px_str.index.astype(str).str[:10]

    rows = []
    dates = vn["ds"].tolist()
    for i, d in enumerate(dates):
        if i < 25:
            continue
        dist = MH.count_distribution_days(vn.iloc[: i + 1])
        breadth = float(breadth_series.get(d, np.nan))
        # canary: picks của 2 phiên giao dịch TRƯỚC d còn ≥ giá entry?
        prev_days = [x for x in dates[max(0, i - 2):i]]
        ok = tot = 0
        for pd_ in prev_days:
            for _, r in picks_by_day.get(pd_, pd.DataFrame()).iterrows():
                cur = px_str.at[d, r["symbol"]] if (d in px_str.index and r["symbol"] in px_str.columns) else np.nan
                if cur == cur and r["entry"]:
                    tot += 1
                    if cur >= r["entry"]:
                        ok += 1
        canary = ok / tot * 100 if tot >= 3 else None
        mh = MH.score_market_health(dist, breadth, canary, float(ratio_ma20.iloc[i]))
        rows.append({"date": d, "health": mh["health"], "dist": dist,
                     "breadth": mh["breadth_pct"], "canary": mh["canary_pct"],
                     "idx_ratio": mh["index_ratio"]})
    return pd.DataFrame(rows)


def apply_gate(picks: pd.DataFrame, health: pd.Series, variant: str, x: float) -> pd.DataFrame:
    h = picks["date"].map(health)
    if variant == "HARD":
        return picks[h >= x]
    if variant == "GRAD":
        keep_strong = (h < x) & (h >= x - 15) & (picks["shadow_buy"] >= 65)
        return picks[(h >= x) | keep_strong]
    if variant == "LATEOFF":
        drop = (h < x) & (picks["shadow_state"] == "BREAKOUT_LATE")
        return picks[~drop]
    raise ValueError(variant)


def main():
    pd.set_option("display.width", 200)
    print("Nạp kho backtest + chấm điểm hiện hành…")
    df = tuner.attach_regime(tuner.load_store())
    scored = tuner.rescore(df, tuner.default_params())
    picks = tuner.simulate(scored)
    print(f"  picks baseline: {len(picks):,}")

    print("Tính chuỗi health 10 năm (dist đã sửa luật hết hạn)…")
    hd = daily_health(picks)
    hd.to_parquet(os.path.join(config.DATA_DIR, "backtest", "market_health_daily.parquet"))
    health = hd.set_index("date")["health"]
    print(f"  {len(hd)} phiên · health TB {hd['health'].mean():.0f} · "
          f"phân phối TB {hd['dist'].mean():.1f} (đã sửa; phiên có dist≥5: "
          f"{(hd['dist'] >= 5).mean() * 100:.0f}%)")

    def ev(pk, period):
        d = pk[(pk["date"] >= period[0]) & (pk["date"] <= period[1])]
        return tuner.objective(d)

    base_tr = ev(picks, tuner.TRAIN)
    base_va = ev(picks, tuner.VALID)
    print(f"\nBASELINE  TRAIN {base_tr['obj']} · VALID {base_va['obj']}")

    print("\n— Tune gating trên TRAIN —")
    results = []
    for variant in ("HARD", "GRAD", "LATEOFF"):
        for x in (20, 25, 30, 35, 40, 45, 50, 55):
            g = apply_gate(picks, health, variant, x)
            r = ev(g, tuner.TRAIN)
            results.append({"variant": variant, "x": x, **r})
            print(f"  {variant:<8} X={x:<3} obj={r['obj']:>8} n={r['n']:>5} "
                  f"win={r.get('win_t3', '—')}")
    res = pd.DataFrame(results)
    best = res.loc[res["obj"].idxmax()]
    print(f"\nBest TRAIN: {best['variant']} X={best['x']} obj={best['obj']} "
          f"(baseline {base_tr['obj']})")

    g_va = apply_gate(picks, health, best["variant"], best["x"])
    va = ev(g_va, tuner.VALID)
    print(f"CỔNG VALIDATION: baseline {base_va['obj']} → gated {va['obj']} "
          f"({'PASS ✅' if va['obj'] > base_va['obj'] else 'FAIL ❌'})")
    print(f"  chi tiết VALID gated: {va}")


if __name__ == "__main__":
    main()
