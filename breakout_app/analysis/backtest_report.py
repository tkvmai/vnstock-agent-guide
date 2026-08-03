"""Phase C — Báo cáo backtest: đọc data/backtest/bt_*.parquet và trả lời các câu hỏi cốt lõi.

KỶ LUẬT CHỐNG OVERFIT (DEVELOPMENT #32): mặc định chỉ hiển thị TRAIN (2016-21) và
VALIDATION (2022-23). HOLDOUT (2024-26) bị khóa — chỉ mở bằng --unlock-holdout khi
đã chốt xong mọi tinh chỉnh (bước xác nhận cuối cùng, một lần duy nhất).

Run:
    & python breakout_app/analysis/backtest_report.py
    & python breakout_app/analysis/backtest_report.py --unlock-holdout   # bước cuối!
"""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import config
import manipulation_blacklist as manip

BT_DIR = os.path.join(config.DATA_DIR, "backtest")
TRAIN = ("2016-01-01", "2021-12-31")
VALID = ("2022-01-01", "2023-12-31")
HOLDOUT = ("2024-01-01", "2026-12-31")


def regime_by_date() -> pd.Series:
    """Regime gate mỗi ngày từ VNINDEX lịch sử — ĐÚNG công thức layer1.check_market_regime:
    blocked nếu close/MA20 < 0.97 và MA5 < MA20; caution nếu < 1.00; còn lại ok."""
    vn = pd.read_parquet(os.path.join(config.DATA_DIR, "history", "VNINDEX.parquet"))
    close = vn["close"]
    ma20 = close.rolling(20).mean()
    ma5 = close.rolling(5).mean()
    ratio = close / ma20
    regime = pd.Series("ok", index=vn.index)
    regime[ratio < 1.00] = "caution"
    regime[(ratio < 0.97) & (ma5 < ma20)] = "blocked"
    return pd.Series(regime.values, index=vn["time"].astype(str).str[:10])


_REGIME = None


def load(period) -> pd.DataFrame:
    global _REGIME
    files = sorted(glob.glob(os.path.join(BT_DIR, "bt_*.parquet")))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    lo, hi = period
    df = df[(df["date"] >= lo) & (df["date"] <= hi)].copy()
    if _REGIME is None:
        _REGIME = regime_by_date()
    df["regime"] = df["date"].map(_REGIME).fillna("ok")
    return manip.tag(df)          # cột `manip` = cửa sổ thao túng đã kết luận


def _fmt_group(g: pd.DataFrame) -> str:
    r = g[g["ret_t3"].notna()]
    if not len(r):
        return "(trống)"
    return (f"n={len(r):>6,} · winT3 {(r['ret_t3'] > 0).mean() * 100:>4.0f}% · "
            f"T3 {r['ret_t3'].mean():+.2f}% · T5 {r['ret_t5'].mean():+.2f}% · "
            f"T10 {r['ret_t10'].mean():+.2f}% · MFE5 {r['mfe5'].mean():+.2f}% · "
            f"MAE5 {r['mae5'].mean():+.2f}%")


def report(df_all: pd.DataFrame, title: str):
    # Kết quả CHÍNH loại cửa sổ thao túng đã kết luận (FLC/Louis/APEC…); phần bị loại
    # báo cáo riêng bên dưới — screener live không biết trước vụ án chưa phanh phui.
    df = df_all[~df_all["manip"]]
    manip_df = df_all[df_all["manip"]]
    print("=" * 90)
    print(f" {title}: {df_all['date'].min()} → {df_all['date'].max()} · "
          f"{len(df):,} dòng sạch (+{len(manip_df):,} dòng thao túng, loại khỏi kết quả chính)")
    print("=" * 90)

    print("\n— Theo TRẠNG THÁI (câu hỏi: state machine có tách được alpha không?) —")
    for st in ["BREAKOUT_FRESH", "PRE_BREAKOUT", "BREAKOUT_LATE", "NONE"]:
        print(f"  {st:<16}", _fmt_group(df[df["state"] == st]))

    rec = df[(df["state"].isin(["BREAKOUT_FRESH", "PRE_BREAKOUT"]))
             & (df["buy_score"] >= config.ALERT_MIN_SCORE)]
    print("\n— KHUYẾN NGHỊ (FRESH/PRE & BUY≥50) vs baseline —")
    print("  Khuyến nghị     ", _fmt_group(rec))
    print("  Toàn pool       ", _fmt_group(df))

    print("\n— Theo DẢI ĐIỂM BUY (trong nhóm khuyến nghị) —")
    bands = [(85, 101, "85-100 Rất mạnh"), (75, 85, "75-84 Mạnh"),
             (65, 75, "65-74 Khá"), (50, 65, "50-64 Trung bình")]
    for lo, hi, lbl in bands:
        print(f"  {lbl:<18}", _fmt_group(rec[(rec["buy_score"] >= lo) & (rec["buy_score"] < hi)]))

    print("\n— Theo NĂM (khuyến nghị) — độ bền qua các pha thị trường —")
    for y, g in rec.groupby(rec["date"].str[:4]):
        print(f"  {y}", _fmt_group(g))

    print("\n— Theo REGIME GATE (so sánh công bằng với live: auto-scan bỏ Layer 2 khi blocked) —")
    for rg in ["ok", "caution", "blocked"]:
        print(f"  KN khi regime {rg:<8}", _fmt_group(rec[rec["regime"] == rg]))
    live_like = rec[rec["regime"] != "blocked"]
    print("  → LIVE-LIKE (ok+caution)", _fmt_group(live_like))
    print("\n  Trạng thái × regime OK (câu hỏi LATE/FRESH trong thị trường thuận):")
    ok_df = df[df["regime"] == "ok"]
    for st in ["BREAKOUT_FRESH", "PRE_BREAKOUT", "BREAKOUT_LATE", "NONE"]:
        print(f"    {st:<16}", _fmt_group(ok_df[ok_df["state"] == st]))
    print("  FRESH RSI>75 khi regime ok:", _fmt_group(
        ok_df[(ok_df["state"] == "BREAKOUT_FRESH") & (ok_df["mom_rsi"] > 75)]))
    print("  FRESH dist>-5% khi regime ok:", _fmt_group(
        ok_df[(ok_df["state"] == "BREAKOUT_FRESH") & (ok_df["bo_dist_to_high"] > -5)]))

    print("\n— ABLATION nhanh trên metric đã lưu —")
    fresh = df[df["state"] == "BREAKOUT_FRESH"]
    print("  FRESH & thrust mạnh (r1d>1%, closing>60):",
          _fmt_group(fresh[(fresh["mom_return_1d"] > 1) & (fresh["bo_closing_strength"] > 60)]))
    print("  FRESH sát đỉnh dài hạn (dist>-5%):        ",
          _fmt_group(fresh[fresh["bo_dist_to_high"] > -5]))
    print("  FRESH sâu dưới đỉnh (dist<-10%):          ",
          _fmt_group(fresh[fresh["bo_dist_to_high"] < -10]))
    print("  FRESH RSI 60-70:                          ",
          _fmt_group(fresh[(fresh["mom_rsi"] >= 60) & (fresh["mom_rsi"] < 70)]))
    print("  FRESH RSI >75 (quá nóng):                 ",
          _fmt_group(fresh[fresh["mom_rsi"] > 75]))

    # ── Nhóm thao túng đã kết luận (bị loại khỏi kết quả chính ở trên) ────────────
    if len(manip_df):
        m_rec = manip_df[(manip_df["state"].isin(["BREAKOUT_FRESH", "PRE_BREAKOUT"]))
                         & (manip_df["buy_score"] >= config.ALERT_MIN_SCORE)]
        print("\n— ⚠️ NHÓM THAO TÚNG ĐÃ KẾT LUẬN (góc nhìn 'live không biết trước') —")
        print("  Toàn bộ dòng    ", _fmt_group(manip_df))
        print("  Bị KN (lọt lưới)", _fmt_group(m_rec))
        if len(m_rec):
            by = m_rec.groupby("symbol")["ret_t3"].agg(["count", "mean"]).round(2)
            detail = {i: "n=%d,T3TB=%+.1f%%" % (int(r["count"]), r["mean"])
                      for i, r in by.iterrows()}
            print("  Mã lọt lưới:", detail)
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--unlock-holdout", action="store_true",
                    help="MỞ KHÓA holdout 2024-26 — chỉ dùng ở bước xác nhận cuối!")
    a = ap.parse_args()

    report(load(TRAIN), "TRAIN 2016-2021")
    report(load(VALID), "VALIDATION 2022-2023")
    if a.unlock_holdout:
        print("⚠️  HOLDOUT được mở — đây phải là lần xác nhận CUỐI CÙNG, không tune thêm sau bước này.")
        report(load(HOLDOUT), "HOLDOUT 2024-2026")
    else:
        print("(HOLDOUT 2024-26 đang khóa — mở bằng --unlock-holdout khi đã chốt tinh chỉnh)")


if __name__ == "__main__":
    main()
