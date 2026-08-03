"""Phase A — Nền dữ liệu backtest: fetch TOÀN BỘ lịch sử OHLCV (~2015→nay).

Đối tượng: mọi mã cổ phiếu 3 ký tự trên HSX + HNX + DELISTED (gộp nhóm hủy niêm yết
để giảm survivorship bias; loại trái phiếu/chứng quyền — mã dài hơn 3 ký tự) + VNINDEX.

Lưu trữ: 1 parquet/mã tại ``data/history/<SYMBOL>.parquet`` (giá đã quy về VND đầy đủ,
cùng heuristic ×1000 như fetchers). File tồn tại = đã fetch → chạy lại chỉ fetch phần
thiếu (resume). Kết quả tổng hợp ghi ``data/history/_manifest.json``.

Run (một lần, ~5-10 phút với Golden 500 req/min):
    & "C:\\Users\\tkvmai\\.venv\\Scripts\\python.exe" breakout_app/analysis/fetch_full_history.py
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import config

HIST_DIR = os.path.join(config.DATA_DIR, "history")
MANIFEST = os.path.join(HIST_DIR, "_manifest.json")
START = "2014-01-01"          # xin sớm hơn mức provider có — nhận được gì lấy nấy
END = None                    # đến hôm nay
WORKERS = 6                   # trần an toàn rate-limit (xem DEVELOPMENT §4.5)


def _list_symbols():
    from vnstock_data import Listing
    lst = Listing(source="VCI").symbols_by_exchange()
    lst["exchange"] = lst["exchange"].str.upper()
    ok = lst[lst["exchange"].isin(["HSX", "HNX", "DELISTED"])].copy()
    ok = ok[ok["symbol"].str.len() == 3]          # loại bond/CW/chứng chỉ quỹ
    if "type" in ok.columns:
        ok = ok[ok["type"].fillna("STOCK").str.upper().isin(["STOCK"])]
    return ok[["symbol", "exchange"]].drop_duplicates("symbol").reset_index(drop=True)


def _fetch_one(symbol: str) -> dict:
    """Fetch + scale + save một mã. Trả metadata cho manifest."""
    from vnstock_data import Market
    import datetime
    end = END or datetime.date.today().isoformat()
    try:
        df = Market().equity(symbol).ohlcv(start=START, end=end)
        if df is None or df.empty or "time" not in df.columns:
            return {"symbol": symbol, "status": "empty"}
        df = df[["time", "open", "high", "low", "close", "volume"]].copy()
        if df["close"].median() < 1000:            # API trả nghìn VND → quy về VND
            for col in ("open", "high", "low", "close"):
                df[col] = df[col] * 1000
        df["time"] = pd.to_datetime(df["time"]).dt.strftime("%Y-%m-%d")
        df.to_parquet(os.path.join(HIST_DIR, f"{symbol}.parquet"), index=False)
        return {"symbol": symbol, "status": "ok", "rows": len(df),
                "from": df["time"].min(), "to": df["time"].max()}
    except Exception as e:
        return {"symbol": symbol, "status": f"error: {type(e).__name__}: {str(e)[:80]}"}


def main():
    os.makedirs(HIST_DIR, exist_ok=True)
    symbols = _list_symbols()
    print(f"Danh sách: {len(symbols)} mã "
          f"({symbols['exchange'].value_counts().to_dict()})")

    done = {f[:-8] for f in os.listdir(HIST_DIR) if f.endswith(".parquet")}
    todo = [s for s in symbols["symbol"] if s not in done]
    if "VNINDEX" not in done:
        todo.append("VNINDEX")
    print(f"Đã có {len(done)} · cần fetch {len(todo)}")

    results, t0 = [], time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(_fetch_one, s): s for s in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            results.append(r)
            if i % 50 == 0 or i == len(todo):
                ok = sum(1 for x in results if x["status"] == "ok")
                print(f"  {i}/{len(todo)} · ok {ok} · {time.time()-t0:.0f}s")

    manifest = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "start_asked": START,
                "results": results}
    prev = {}
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            prev = {r["symbol"]: r for r in json.load(f).get("results", [])}
    prev.update({r["symbol"]: r for r in results})
    manifest["results"] = list(prev.values())
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    ok = [r for r in manifest["results"] if r.get("status") == "ok"]
    if ok:
        rows = sum(r.get("rows", 0) for r in ok)
        froms = sorted(r["from"] for r in ok)
        print(f"\nTỔNG KẾT: {len(ok)} mã ok · {rows:,} dòng · "
              f"ngày sớm nhất {froms[0]} · median from {froms[len(froms)//2]}")


if __name__ == "__main__":
    main()
