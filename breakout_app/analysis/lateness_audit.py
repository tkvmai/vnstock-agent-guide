"""Audit độ trễ đề xuất (lateness audit) — chạy lại định kỳ khi tích lũy thêm tín hiệu.

Trả lời 3 câu hỏi trên dữ liệu tín hiệu đã ghi (tracked_signals + ohlcv_daily):
  1. Lúc đề xuất, breakout đã cũ bao nhiêu (age) và giá đã chạy bao xa (ratio)?
  2. Win-rate theo LẦN đề xuất thứ n của cùng một mã (đề xuất lặp ngày 3+ có còn tốt?).
  3. Kênh bắt sớm PRE_BREAKOUT bỏ lỡ vì điều kiện nào (replay phiên trước ngày vượt)?

Run:
    & "C:\\Users\\tkvmai\\.venv\\Scripts\\python.exe" breakout_app/analysis/lateness_audit.py
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import config
from data import db, fetchers
from engine import breakout as BO
from engine import momentum as MO


def retro_alert_audit(c, until="2026-07-03"):
    """Hồi tố các alert TRƯỚC khi tracked_signals tồn tại (lứa RevC, từ sent_alerts):
    tính lại age/run-up tại ngày alert + kết quả T+3 close-to-close từ ohlcv_daily."""
    alerts = pd.read_sql_query(
        "SELECT date, symbol FROM sent_alerts WHERE date < ? ORDER BY date", c, params=(until,))
    rows = []
    for _, a in alerts.iterrows():
        h = db.load_ohlcv(a["symbol"]).reset_index(drop=True)
        h["ds"] = h["time"].dt.strftime("%Y-%m-%d")
        idx = h.index[h["ds"] == a["date"]]
        if len(idx) == 0 or idx[0] < 60:
            continue
        i = idx[0]
        closes_before = h["close"].iloc[:i]
        close_d = float(h["close"].iloc[i])
        pivot = float(closes_before.iloc[-20:].max())
        fwd = h["close"].iloc[i + 1:i + 6].tolist()
        ret3 = (fwd[2] / close_d - 1) * 100 if len(fwd) >= 3 else None
        rows.append({"date": a["date"], "symbol": a["symbol"],
                     "age": BO.breakout_age(closes_before, close_d),
                     "run_pct": (close_d / pivot - 1) * 100, "ret_t3": ret3,
                     "win": None if ret3 is None else int(ret3 > 0)})
    df = pd.DataFrame(rows)
    if df.empty:
        print("(không có alert lịch sử trước", until, ")")
        return
    df["rep_n"] = df.groupby("symbol").cumcount() + 1
    print(f"\n=== HỒI TỐ lứa alert trước {until}: {len(df)} tín hiệu / {df['symbol'].nunique()} mã ===")
    bo = df[df["age"] >= 0]
    print("age lúc alert:", bo["age"].value_counts().sort_index().to_dict())
    print(f"run-up lúc alert: TB {bo['run_pct'].mean():+.2f}% · max {bo['run_pct'].max():+.2f}%")
    r = df[df["win"].notna()]
    if len(r):
        print(f"T+3: win {r['win'].mean() * 100:.0f}% · TB {r['ret_t3'].mean():+.2f}%")
        g = r.groupby(r["rep_n"].clip(upper=4))
        print(pd.DataFrame({"n": g.size(), "win%": (g["win"].mean() * 100).round(0),
                            "retT3": g["ret_t3"].mean().round(2)}).to_string())


def main():
    pd.set_option("display.width", 200)
    c = sqlite3.connect(config.DB_PATH)

    # 1. Độ trễ tại thời điểm đề xuất --------------------------------------------------
    df = pd.read_sql_query(
        "SELECT reco_date, symbol, state, breakout_age, breakout_ratio FROM tracked_signals", c)
    fresh = df[df["state"] == "BREAKOUT_FRESH"].copy()
    print("=== 1. Độ trễ lúc đề xuất (mã FRESH) ===")
    print("breakout_age:", fresh["breakout_age"].value_counts().sort_index().to_dict())
    run = (fresh["breakout_ratio"] - 1) * 100
    print(f"giá đã chạy trên đỉnh: TB {run.mean():.2f}% · median {run.median():.2f}% · max {run.max():.2f}%")

    # 2. Win-rate theo lần đề xuất thứ n ----------------------------------------------
    out = pd.read_sql_query(
        "SELECT t.reco_date, t.symbol, o.win_t3, o.ret_t3 FROM tracked_signals t "
        "LEFT JOIN signal_outcomes o ON o.symbol=t.symbol AND o.reco_date=t.reco_date "
        "ORDER BY t.symbol, t.reco_date", c)
    out["rep_n"] = out.groupby("symbol").cumcount() + 1
    res = out[out["win_t3"].notna()]
    print("\n=== 2. Win-rate theo lần đề xuất thứ n (4 = thứ 4 trở lên) ===")
    g = res.groupby(res["rep_n"].clip(upper=4))
    print(pd.DataFrame({"n": g.size(), "win%": (g["win_t3"].mean() * 100).round(0),
                        "retT3_TB": g["ret_t3"].mean().round(2)}).to_string())

    # 3. Vì sao PRE không bắt sớm được (replay phiên trước ngày vượt đầu tiên) ---------
    first_fresh = pd.read_sql_query(
        "SELECT symbol, MIN(reco_date) d FROM tracked_signals "
        "WHERE state='BREAKOUT_FRESH' GROUP BY symbol", c)
    vn = fetchers.fetch_vnindex(days=120)
    fails = {"dist>3%": 0, "dry_up>=0.9": 0, "narrow>=0.9": 0, "ma20<=ma50": 0,
             "slope<=0": 0, "rs<0": 0, "đã_trên_pivot(gap)": 0, "thiếu_data": 0}
    ok = n = 0
    for _, r in first_fresh.iterrows():
        h = db.load_ohlcv(r["symbol"])
        h = h[h["time"] < r["d"]].reset_index(drop=True)
        if len(h) < 60:
            fails["thiếu_data"] += 1
            continue
        prev, hist = h.iloc[-1], h.iloc[:-1].reset_index(drop=True)
        n += 1
        live = {"close": prev["close"], "high": prev["high"], "low": prev["low"],
                "volume": prev["volume"], "minutes_elapsed": 225}
        bo = BO.score_breakout(hist, live, 1.0)
        vnh = vn[vn["time"] < r["d"]]
        mom = MO.score_momentum(hist, live,
                                vnh[["close"]].reset_index(drop=True) if len(vnh) else None, None)
        if bo["breakout_ratio"] >= 1.0:
            fails["đã_trên_pivot(gap)"] += 1
            continue
        dist = (1 - bo["breakout_ratio"]) * 100
        bad = False
        if dist > config.PRE_BREAKOUT_MAX_DIST:
            fails["dist>3%"] += 1; bad = True
        if bo["dry_up_ratio"] >= config.PRE_BREAKOUT_DRYUP_MAX:
            fails["dry_up>=0.9"] += 1; bad = True
        if bo["narrowing_ratio"] >= config.PRE_BREAKOUT_NARROWING_MAX:
            fails["narrow>=0.9"] += 1; bad = True
        if (mom.get("ma20_vs_ma50") or 0) <= 0:
            fails["ma20<=ma50"] += 1; bad = True
        if (mom.get("slope_ma20") or 0) <= 0:
            fails["slope<=0"] += 1; bad = True
        if (mom.get("rs_weighted") or 0) < 0:
            fails["rs<0"] += 1; bad = True
        if not bad:
            ok += 1
    print(f"\n=== 3. Replay {n} mã — phiên NGAY TRƯỚC ngày vượt đỉnh đầu tiên ===")
    print(f"Đủ điều kiện PRE hôm trước (bắt sớm được): {ok}")
    for k, v in sorted(fails.items(), key=lambda x: -x[1]):
        if v:
            print(f"  chặn bởi {k}: {v}")

    # 4. Hồi tố lứa RevC (trước khi tracked_signals tồn tại) ---------------------------
    retro_alert_audit(c)


if __name__ == "__main__":
    main()
