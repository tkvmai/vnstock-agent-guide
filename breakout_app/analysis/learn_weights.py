"""[DEPRECATED 19/07/2026 — KHÔNG CÒN ĐƯỢC GỌI]

Khai tử sau 2 bằng chứng: (1) sự cố regime-bias — learner hạ signal 0.40→0.30 từ
367 mẫu toàn kỳ điều chỉnh với tương quan ±0.02 (nhiễu thuần); (2) chiến dịch
calibrate 10 năm/233k mẫu chứng minh mặt objective PHẲNG quanh trọng số hiện tại —
learner nhỏ giọt về nguyên tắc chỉ có thể học nhiễu. Giữ file làm tư liệu.
Vai trò "học từ live" được thay bằng: chuông báo trôi dạt (drift alarm) + calibrate
band intraday/flow khi daily_observations tích lũy đủ (DEVELOPMENT #40).

--- Docstring gốc ---
Auto-tune W_BUY from objective T+3 outcomes (RevD Phase 4 extension).

Driven by `win_t3` (the app already computes lãi/lỗ at T+3), so **no manual feedback
is required**. For each top-level BUY component (liquidity, momentum, signal) it
measures how strongly the component score separates T+3 winners from losers
(point-biserial correlation), nudges the DEFAULT weights toward the data by a
sample-size-scaled learning rate, clamps each weight within ±LEARN_MAX_DELTA of its
default, and writes `data/learned_weights.json`.

Guardrails (avoid overfit / self-reinforcement):
  - needs ≥ LEARN_MIN_SAMPLE resolved signals, else no change;
  - always blends from the DEFAULT weights (not the previous learned) → bounded, no drift;
  - each weight stays within ±LEARN_MAX_DELTA of default;
  - alpha = min(cap, n/scale) → trusts data more only as samples accumulate.

Reversible: delete data/learned_weights.json or set config.USE_LEARNED_WEIGHTS=False.
config.py is never rewritten. Picks flagged 'couldnt_buy' are excluded upstream
(db.load_calibration_data).

Run manually:
    & "C:\\Users\\tkvmai\\.venv\\Scripts\\python.exe" breakout_app/analysis/learn_weights.py
"""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import config
from data import db

COMPONENTS = ["liquidity", "momentum", "signal"]


def _pointbiserial(x, y):
    """Correlation of a component score with the binary win_t3 label."""
    a = pd.to_numeric(x, errors="coerce")
    b = pd.to_numeric(y, errors="coerce")
    m = a.notna() & b.notna()
    if m.sum() < 3 or a[m].std() == 0 or b[m].std() == 0:
        return None
    return float(np.corrcoef(a[m], b[m])[0, 1])


def compute_weights(df: pd.DataFrame):
    """Return {w, corr, alpha, data_w} or None if the data carries no positive signal."""
    n = len(df)
    corr = {c: _pointbiserial(df[c], df["win_t3"]) for c in COMPONENTS}
    pos = {c: max(corr[c] or 0.0, 0.0) for c in COMPONENTS}   # only positive predictors pull weight
    total = sum(pos.values())
    if total <= 0:
        return None
    data_w = {c: pos[c] / total for c in COMPONENTS}
    alpha = min(config.LEARN_ALPHA_CAP, n / config.LEARN_ALPHA_SCALE)
    base = config.W_BUY
    # Blend (sums to 1 since base & data_w each sum to 1) → deviations (sum to 0).
    tgt = {c: (1 - alpha) * base[c] + alpha * data_w[c] for c in COMPONENTS}
    dev = {c: tgt[c] - base[c] for c in COMPONENTS}
    # Scale ALL deviations by one factor so max|dev| ≤ MAX_DELTA. This keeps the sum
    # at exactly 1 (deviations still sum to 0) AND respects the ±MAX_DELTA box —
    # unlike clamp-then-renormalise, which can push a weight back past the cap.
    max_dev = max((abs(v) for v in dev.values()), default=0.0)
    k = min(1.0, config.LEARN_MAX_DELTA / max_dev) if max_dev > 0 else 1.0
    w = {c: round(base[c] + k * dev[c], 3) for c in COMPONENTS}
    return {"w": w, "corr": corr, "alpha": alpha, "data_w": data_w}


def learn_and_save():
    """Recompute + persist learned W_BUY. Returns a summary dict, or None if skipped.

    Uses the WHOLE Layer-1 pool (`load_learning_data`), NOT just recommended stocks,
    so the correlation of each component with T+3 wins is unbiased (includes stocks the
    app did NOT recommend but which rose anyway)."""
    df = db.load_learning_data()
    n = len(df)
    if n < config.LEARN_MIN_SAMPLE:
        return None
    res = compute_weights(df)
    if res is None:
        return None
    win_rate = float(df["win_t3"].mean())
    payload = {
        "W_BUY": res["w"],
        "meta": {
            "updated": datetime.datetime.now().isoformat(timespec="seconds"),
            "n_samples": n,
            "win_rate": round(win_rate, 4),
            "alpha": round(res["alpha"], 3),
            "corr": {c: (round(res["corr"][c], 3) if res["corr"][c] is not None else None)
                     for c in COMPONENTS},
            "default_W_BUY": config.W_BUY,
        },
    }
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(config.LEARNED_WEIGHTS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    config.reload_learned_weights()
    return {"weights": res["w"], "n_samples": n, "win_rate": win_rate,
            "corr": payload["meta"]["corr"]}


def main():
    r = learn_and_save()
    if r is None:
        print(f"Chưa đủ mẫu để học (cần ≥ {config.LEARN_MIN_SAMPLE} tín hiệu đủ T+3, "
              "hoặc dữ liệu chưa có tín hiệu dự báo dương). Giữ nguyên trọng số mặc định.")
        return
    print(f"Đã cập nhật W_BUY: {r['weights']}")
    print(f"  dựa trên {r['n_samples']} tín hiệu · win-rate T+3 = {r['win_rate']*100:.0f}%")
    print(f"  tương quan thành phần↔thắng: {r['corr']}")
    print(f"  (mặc định: {config.W_BUY}) — ghi vào {config.LEARNED_WEIGHTS_PATH}")


if __name__ == "__main__":
    main()
