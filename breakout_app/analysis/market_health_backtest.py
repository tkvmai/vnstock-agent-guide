"""Backtest điểm Sức khỏe thị trường trên toàn bộ lịch sử đã tích lũy.

Với mỗi phiên từ ~giữa tháng 6: tính lại 4 thành phần y như live —
  dist_days (VN-Index 25 phiên), breadth (% mã pool > MA20, từ ohlcv_daily),
  canary (% tín hiệu KN 1-2 phiên trước còn ≥ close ngày KN; nguồn sent_alerts
  + tracked_signals), index_ratio (close/MA20) — rồi in chuỗi điểm theo ngày
  kèm return VN-Index 3 phiên SAU đó, để trả lời: điểm có TỤT TRƯỚC các cú
  điều chỉnh không, và có đứng cao trong uptrend không?

Run:
    & "C:\\Users\\tkvmai\\.venv\\Scripts\\python.exe" breakout_app/analysis/market_health_backtest.py
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import config
from data import db, fetchers
from engine import market_health as MH


def main():
    pd.set_option("display.width", 200)
    c = sqlite3.connect(config.DB_PATH)

    vn = fetchers.fetch_vnindex(days=120).reset_index(drop=True)
    vn["ds"] = vn["time"].dt.strftime("%Y-%m-%d")

    # Toàn bộ giá pool: symbol → DataFrame(ds, close)
    px = pd.read_sql_query("SELECT symbol, date, close FROM ohlcv_daily ORDER BY date", c)
    by_sym = {s: g.reset_index(drop=True) for s, g in px.groupby("symbol")}

    # Tín hiệu KN (RevC từ sent_alerts + RevD từ tracked_signals)
    sig = pd.concat([
        pd.read_sql_query("SELECT date AS d, symbol FROM sent_alerts", c),
        pd.read_sql_query("SELECT reco_date AS d, symbol FROM tracked_signals", c),
    ]).drop_duplicates()

    days = [d for d in vn["ds"] if d >= "2026-06-14"]
    rows = []
    for d in days:
        vi = vn.index[vn["ds"] == d]
        if len(vi) == 0 or vi[0] < 25:
            continue
        i = vi[0]
        window = vn.iloc[: i + 1]

        dist = MH.count_distribution_days(window)
        ma20 = float(window["close"].iloc[-20:].mean())
        ratio = float(window["close"].iloc[-1]) / ma20 if ma20 else float("nan")

        above = total = 0
        for s, g in by_sym.items():
            gd = g[g["date"] <= d]
            if len(gd) < 21 or gd["date"].iloc[-1] != d:
                continue
            m = float(gd["close"].iloc[-21:-1].mean())
            if not m:
                continue
            total += 1
            if float(gd["close"].iloc[-1]) > m:
                above += 1
        breadth = above / total * 100 if total else float("nan")

        prev_days = sorted({x for x in sig["d"] if x < d})[-2:]
        ok = tot = 0
        for _, r in sig[sig["d"].isin(prev_days)].iterrows():
            g = by_sym.get(r["symbol"])
            if g is None:
                continue
            e = g[g["date"] == r["d"]]["close"]
            cur = g[g["date"] == d]["close"]
            if len(e) and len(cur):
                tot += 1
                if float(cur.iloc[0]) >= float(e.iloc[0]):
                    ok += 1
        canary = ok / tot * 100 if tot >= 3 else None

        mh = MH.score_market_health(dist, breadth, canary, ratio)
        fwd = (float(vn["close"].iloc[i + 3]) / float(vn["close"].iloc[i]) - 1) * 100 \
            if i + 3 < len(vn) else None
        rows.append({"ngày": d, "health": mh["health"], "dist": dist,
                     "breadth%": round(breadth, 0) if breadth == breadth else None,
                     "canary%": round(canary, 0) if canary is not None else None,
                     "idx/MA20": round(ratio, 3),
                     "VNIndex_T+3%": round(fwd, 2) if fwd is not None else None})

    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    done = df[df["VNIndex_T+3%"].notna()]
    if len(done) > 5:
        lo = done[done["health"] < 55]
        hi = done[done["health"] >= 55]
        print(f"\nHealth <55 : {len(lo)} phiên · VN-Index T+3 TB {lo['VNIndex_T+3%'].mean():+.2f}%")
        print(f"Health >=55: {len(hi)} phiên · VN-Index T+3 TB {hi['VNIndex_T+3%'].mean():+.2f}%")


if __name__ == "__main__":
    main()
