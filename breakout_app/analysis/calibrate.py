"""Calibration & attribution report (Spec RevD Phase 4) — human-in-the-loop.

Reads the resolved tracked signals (T+3 available) + user feedback and prints:
  1. Overall + per-score-band + per-state T+3 win-rate and avg return.
  2. Correlation of each BUY component (and diagnostics: breakout_age, rsi) with the
     T+3 outcome → which components actually carry edge on YOUR data.
  3. A SUGGESTED W_BUY rebalance (blended 50/50 with the current weights to avoid
     overfitting). It is PRINTED ONLY — this script never writes config.py. You review
     and edit weights manually.

Run:
    & "C:\\Users\\tkvmai\\.venv\\Scripts\\python.exe" breakout_app/analysis/calibrate.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import config
from data import db

MIN_SAMPLE = 15          # below this, results are not trustworthy
COMPONENTS = ["liquidity", "momentum", "signal"]   # map 1:1 to config.W_BUY
DIAGNOSTICS = ["breakout_age", "rsi", "breakout_ratio", "buy_score"]


def _corr(x: pd.Series, y: pd.Series):
    """Pearson correlation, or None if undefined (too few points / constant series)."""
    a = pd.to_numeric(x, errors="coerce")
    b = pd.to_numeric(y, errors="coerce")
    mask = a.notna() & b.notna()
    if mask.sum() < 3 or a[mask].std() == 0 or b[mask].std() == 0:
        return None
    return float(np.corrcoef(a[mask], b[mask])[0, 1])


def _fmt(v, nd=2, suffix=""):
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.{nd}f}{suffix}"


def main():
    df = db.load_calibration_data()
    n = len(df)
    print("=" * 68)
    print(f" BÁO CÁO HIỆU CHỈNH RevD — {n} tín hiệu đã đủ T+3 (loại 'không mua được')")
    print("=" * 68)
    if n < MIN_SAMPLE:
        print(f"\n⚠️  Chưa đủ mẫu (cần ≥ {MIN_SAMPLE}). Hãy để app theo dõi thêm vài tuần.")
        print("   Vẫn in các số hiện có bên dưới nhưng KHÔNG nên dựa vào để chỉnh trọng số.\n")

    win = df["win_t3"].mean() * 100
    avg = pd.to_numeric(df["ret_t3"], errors="coerce").mean()
    print(f"\nTỔNG THỂ: win-rate T+3 = {_fmt(win,0,'%')} · return T+3 TB = {_fmt(avg,2,'%')}")

    # 1. By score band ---------------------------------------------------------------
    print("\n— Theo dải điểm BUY —")
    print(f"  {'Dải':<14}{'N':>4}{'Win%':>7}{' retT3 TB':>11}")
    for thr, label, _c in config.SCORE_BANDS:
        sub = df[df["buy_score"] >= thr]
        # exclusive upper: remove those already counted in a higher band
        higher = [t for t, _l, _cc in config.SCORE_BANDS if t > thr]
        if higher:
            sub = sub[sub["buy_score"] < min(higher)]
        if len(sub):
            print(f"  {label:<14}{len(sub):>4}{sub['win_t3'].mean()*100:>6.0f}%"
                  f"{pd.to_numeric(sub['ret_t3']).mean():>10.2f}%")

    # 2. By state --------------------------------------------------------------------
    print("\n— Theo trạng thái —")
    for st, g in df.groupby("state"):
        lbl = config.STATE_LABELS.get(st, st)
        print(f"  {lbl:<16}{len(g):>4}  win {g['win_t3'].mean()*100:>3.0f}% · "
              f"retT3 TB {pd.to_numeric(g['ret_t3']).mean():+.2f}%")

    # 2b. Recommendation quality — full Layer-1 pool (does the filter add edge?) --------
    try:
        pool = db.recommendation_quality()
    except Exception:
        pool = None
    if pool is not None and not pool.empty:
        print("\n— Chất lượng khuyến nghị (toàn bộ pool Layer-1) —")
        for flag, lbl in ((1, "Được khuyến nghị"), (0, "KHÔNG khuyến nghị")):
            g = pool[pool["is_reco"] == flag]
            if len(g):
                print(f"  {lbl:<18}{len(g):>4}  win {g['win_t3'].mean()*100:>3.0f}% · "
                      f"retT3 TB {pd.to_numeric(g['ret_t3']).mean():+.2f}%")
        rec = pool[pool["is_reco"] == 1]["win_t3"].mean() if (pool["is_reco"] == 1).any() else None
        non = pool[pool["is_reco"] == 0]["win_t3"].mean() if (pool["is_reco"] == 0).any() else None
        if rec is not None and non is not None:
            edge = (rec - non) * 100
            verdict = "có edge ✔" if edge > 3 else ("không rõ edge ⚠️" if edge > -3 else "ĐANG BỎ SÓT ✗")
            print(f"  → Chênh win-rate KN − không-KN = {edge:+.0f}đ ({verdict})")
        print("  Theo trạng thái (cả pool):")
        for st, g in pool.groupby("state"):
            lbl = config.STATE_LABELS.get(st, st)
            print(f"    {lbl:<16}{len(g):>4}  win {g['win_t3'].mean()*100:>3.0f}% · "
                  f"retT3 TB {pd.to_numeric(g['ret_t3']).mean():+.2f}%")

    # 3. Component / diagnostic correlations vs T+3 return ----------------------------
    print("\n— Tương quan với return T+3 (Pearson; + = giúp thắng, − = hại) —")
    corrs = {}
    for col in COMPONENTS + DIAGNOSTICS:
        c = _corr(df[col], df["ret_t3"])
        corrs[col] = c
        tag = ""
        if col == "breakout_age":
            tag = "  (kỳ vọng < 0 → xác nhận phạt breakout cũ)"
        elif col == "rsi":
            tag = "  (kỳ vọng < 0 ở vùng cao → xác nhận phạt quá nóng)"
        print(f"  {col:<16}{_fmt(c,3):>8}{tag}")

    # 4. Suggested W_BUY (printed only) ----------------------------------------------
    print("\n— GỢI Ý trọng số W_BUY (chỉ IN, KHÔNG tự áp dụng) —")
    comp_corr = {c: corrs.get(c) for c in COMPONENTS}
    if any(v is None for v in comp_corr.values()) or n < MIN_SAMPLE:
        print("  (Chưa đủ dữ liệu tin cậy để gợi ý — giữ nguyên trọng số hiện tại.)")
    else:
        pos = {c: max(v, 0.01) for c, v in comp_corr.items()}   # floor to keep positive
        s = sum(pos.values())
        data_w = {c: pos[c] / s for c in COMPONENTS}
        cur = config.W_BUY
        blended = {c: 0.5 * cur[c] + 0.5 * data_w[c] for c in COMPONENTS}
        bs = sum(blended.values())
        suggested = {c: round(blended[c] / bs, 3) for c in COMPONENTS}
        print(f"  {'Thành phần':<12}{'hiện tại':>10}{'gợi ý':>10}{'Δ':>8}")
        for c in COMPONENTS:
            print(f"  {c:<12}{cur[c]:>10.2f}{suggested[c]:>10.3f}{suggested[c]-cur[c]:>+8.3f}")
        print("\n  → Sửa tay trong config.py W_BUY nếu bạn đồng ý (blend 50/50 với dữ liệu"
              " để tránh overfit).")
    print()


if __name__ == "__main__":
    main()
