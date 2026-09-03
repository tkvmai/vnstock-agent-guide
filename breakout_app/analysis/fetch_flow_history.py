"""Kéo LỊCH SỬ dòng tiền khối ngoại + tự doanh theo ngày (2019→nay) cho backtest
screen Dòng tiền thông minh (21/08/2026).

API VCI trần 100 dòng/call → phân trang cửa sổ ~3.5 tháng. Resume: mỗi mã một
parquet trong data/flow_history/, có file thì bỏ qua. Universe: mã trong kho
data/history/ từng có GTGD20 ≥ 12 tỷ giai đoạn 2019+ (hơi rộng hơn sàn 15 tỷ để
không cắt cụt biên), tối đa MAX_SYMBOLS mã theo GTGD đỉnh.

Chạy:  python analysis/fetch_flow_history.py
"""

import glob
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT_DIR = "data/flow_history"
START = date(2019, 1, 1)
WINDOW_DAYS = 105          # ~72 phiên < trần 100 dòng
GTGD_FLOOR = 12e9
MAX_SYMBOLS = 220
WORKERS = 3                # 500 req/phút Golden: 6 workers chạm trần bị kill cứng
CALL_SLEEP = 0.30          # throttle mỗi call/thread → ~300 req/phút, an toàn


def pick_universe():
    syms = []
    for f in glob.glob("data/history/*.parquet"):
        sym = os.path.basename(f)[:-8]
        if sym.startswith("_") or sym == "VNINDEX" or len(sym) != 3:
            continue
        try:
            df = pd.read_parquet(f, columns=["time", "close", "volume"])
        except Exception:
            continue
        df = df[df["time"] >= "2019-01-01"]
        if len(df) < 120:
            continue
        g20 = (df["close"] * df["volume"]).rolling(20).mean()
        peak = float(g20.max())
        if peak >= GTGD_FLOOR:
            syms.append((sym, peak))
    syms.sort(key=lambda x: -x[1])
    return [s for s, _ in syms[:MAX_SYMBOLS]]


def windows():
    out = []
    lo = START
    today = date.today()
    while lo <= today:
        hi = min(lo + timedelta(days=WINDOW_DAYS - 1), today)
        out.append((lo.isoformat(), hi.isoformat()))
        lo = hi + timedelta(days=1)
    return out


def fetch_one(sym: str, wins) -> str:
    from vnstock_data import Trading
    path = os.path.join(OUT_DIR, f"{sym}.parquet")
    if os.path.exists(path):
        return "skip"
    import time
    t = Trading(symbol=sym, source="VCI")
    fr, pr = [], []
    for lo, hi in wins:
        try:
            f = t.foreign_trade(start=lo, end=hi)
            if f is not None and not f.empty:
                fr.append(f[["trading_date", "fr_net_value_total"]])
        except Exception:
            pass
        time.sleep(CALL_SLEEP)
        try:
            p = t.prop_trade(start=lo, end=hi)
            if p is not None and not p.empty:
                pr.append(p[["trading_date", "total_trade_net_value"]])
        except Exception:
            pass
        time.sleep(CALL_SLEEP)
    fd = (pd.concat(fr).rename(columns={"fr_net_value_total": "fr_net"})
          if fr else pd.DataFrame(columns=["trading_date", "fr_net"]))
    pdf = (pd.concat(pr).rename(columns={"total_trade_net_value": "prop_net"})
           if pr else pd.DataFrame(columns=["trading_date", "prop_net"]))
    m = pd.merge(fd, pdf, on="trading_date", how="outer")
    m["date"] = pd.to_datetime(m["trading_date"]).dt.strftime("%Y-%m-%d")
    m = m[["date", "fr_net", "prop_net"]].drop_duplicates("date").sort_values("date")
    m.to_parquet(path, index=False)
    return f"{len(m)} dòng"


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    syms = pick_universe()
    wins = windows()
    print(f"universe {len(syms)} mã · {len(wins)} cửa sổ/mã · ~{len(syms)*len(wins)*2:,} calls", flush=True)
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(fetch_one, s, wins): s for s in syms}
        for fut in as_completed(futs):
            s = futs[fut]
            done += 1
            try:
                r = fut.result()
            except Exception as e:
                r = f"LỖI {type(e).__name__}"
            if done % 10 == 0 or r.startswith("LỖI"):
                print(f"[{done}/{len(syms)}] {s}: {r}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
