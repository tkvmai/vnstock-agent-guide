# -*- coding: utf-8 -*-
"""W2-W4 — Máy calibrate: shadow scorer + simulator + optimizer trên kho backtest.

Shadow scorer (`rescore`): tính lại TOÀN BỘ pipeline điểm (sub-score → nhóm → state
machine → BUY) từ metric thô đã lưu trong bt_*.parquet, dưới một bộ tham số bất kỳ
(weights + bands) — vectorized numpy, ~1s/233k dòng → thử hàng nghìn cấu hình không
cần chạy lại engine.

PARITY BẮT BUỘC: với bộ tham số hiện hành (dựng tự động từ config/tables), rescore
phải tái tạo buy_score/state đã lưu (xem `parity()`). Không đạt → sửa cho đạt rồi
mới được tune.

Xấp xỉ kế thừa từ backtest (giống cả 2 phía nên không phá parity): flow trung tính
(SMF=40), intraday score cố định (lưu sẵn trong liq_score_intraday).

CLI:
    python analysis/tuner.py parity            # kiểm parity trên toàn kho
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

import config
from engine import tables as T

BT_DIR = os.path.join(config.DATA_DIR, "backtest")


# ── Bộ tham số: dựng TỰ ĐỘNG từ code hiện hành ─────────────────────────────────────
def _band(bands, default):
    """(uppers, scores, default) — bản sao tunable của tables.piecewise bands."""
    return {"uppers": [b[0] for b in bands], "scores": [b[1] for b in bands],
            "default": default}


def default_params() -> dict:
    return {
        # weights
        "W_BUY": dict(config.W_BUY),
        "STATE_MULT": dict(config.STATE_MULT),
        "W_TRIGGER": dict(config.W_TRIGGER),
        "W_SETUP": dict(config.W_SETUP),
        "W_SETUP_STRUCTURE": dict(config.W_SETUP_STRUCTURE),
        "W_LIQUIDITY": dict(config.W_LIQUIDITY),
        "W_MOMENTUM": dict(config.W_MOMENTUM),
        "W_COMPOSITE": dict(config.W_COMPOSITE),
        "W_MA": dict(config.W_MA),
        "W_SLOPE": dict(config.W_SLOPE),
        "W_RS": dict(config.W_RS),
        "W_FLOW": dict(config.W_FLOW),
        "W_TECHNICAL": dict(config.W_TECHNICAL),
        # gates / knobs
        "FRESH_MAX_RATIO": config.FRESH_MAX_RATIO,
        "FRESH_MAX_AGE": config.FRESH_MAX_AGE,
        "FRESH_MIN_RETURN_1D": config.FRESH_MIN_RETURN_1D,
        "FRESH_MIN_CLOSING": config.FRESH_MIN_CLOSING,
        "PRE_MAX_DIST": config.PRE_BREAKOUT_MAX_DIST,
        "PRE_DRYUP_MAX": config.PRE_BREAKOUT_DRYUP_MAX,
        "PRE_NARROWING_MAX": config.PRE_BREAKOUT_NARROWING_MAX,
        "CONSISTENCY_MULT": dict(config.CONSISTENCY_MULT),
        "NEUTRAL_FLOW": config.NEUTRAL_FLOW_SCORE,
        # bands (bản tunable của engine/tables)
        "B_GTGD20": _band(T.GTGD20_BANDS, T.GTGD20_DEFAULT),
        "B_CV": _band(T.CV_BANDS, T.CV_DEFAULT),
        "B_R1D": _band(T.RETURN_1D_BANDS, T.RETURN_1D_DEFAULT),
        "B_R5D": _band(T.RETURN_5D_BANDS, T.RETURN_5D_DEFAULT),
        "B_R20D": _band(T.RETURN_20D_BANDS, T.RETURN_20D_DEFAULT),
        "B_PVMA20": _band(T.PRICE_VS_MA20_BANDS, T.PRICE_VS_MA20_DEFAULT),
        "B_PVMA50": _band(T.PRICE_VS_MA50_BANDS, T.PRICE_VS_MA50_DEFAULT),
        "B_ALIGN": _band(T.ALIGNMENT_BANDS, T.ALIGNMENT_DEFAULT),
        "B_SLOPE20": _band(T.SLOPE_MA20_BANDS, T.SLOPE_MA20_DEFAULT),
        "B_SLOPE50": _band(T.SLOPE_MA50_BANDS, T.SLOPE_MA50_DEFAULT),
        "B_RSW": _band(T.RS_WEIGHTED_BANDS, T.RS_WEIGHTED_DEFAULT),
        "B_AD": _band(T.AD_RATIO_BANDS, T.AD_RATIO_DEFAULT),
        "B_RSI": _band(T.RSI_BANDS, T.RSI_DEFAULT),
        "B_MACD": _band(T.MACD_HIST_BANDS, T.MACD_HIST_DEFAULT),
        "B_PRICE_FRESH": _band(T.PRICE_FRESH_BANDS, T.PRICE_FRESH_DEFAULT),
        "B_VOLR": _band(T.VOLUME_RATIO_BANDS, T.VOLUME_RATIO_DEFAULT),
        "B_DRYUP": _band(T.DRYUP_BANDS, T.DRYUP_DEFAULT),
        "B_BASE": _band(T.BASE_QUALITY_BANDS, T.BASE_QUALITY_DEFAULT),
        "B_CLOSING": _band(T.CLOSING_STRENGTH_BANDS, T.CLOSING_STRENGTH_DEFAULT),
        "B_PROX": _band(T.PROXIMITY_BANDS, T.PROXIMITY_DEFAULT),
        # multiplier "bands" (viết lại dạng band để tune được)
        "B_AGE_FACTOR": {"uppers": [1, 2, 3], "scores": [1.00, 0.90, 0.60], "default": 0.30},
        "B_OVERHEAT_RSI": {"uppers": [70, 75, 80], "scores": [1.00, 0.90, 0.75], "default": 0.55},
        "B_OVERHEAT_EXT": {"uppers": [6, 9, 13], "scores": [1.00, 0.85, 0.65], "default": 0.45},
        "B_OVERHEAD": {"uppers": [-10, -5], "scores": [0.70, 0.90], "default": 1.00},
        "B_RS_ACCEL": {"uppers": [-5, 0, 5], "scores": [0.80, 0.90, 1.00], "default": 1.10},
    }


def _pw(values, band):
    """piecewise vectorized — giữ đúng ngữ nghĩa tables.piecewise (value < upper)."""
    v = np.asarray(values, dtype=float)
    uppers = np.asarray(band["uppers"], dtype=float)
    scores = np.asarray(list(band["scores"]) + [band["default"]], dtype=float)
    idx = np.searchsorted(uppers, v, side="right")
    out = scores[idx]
    return np.where(np.isnan(v), np.nan, out)


# ── Shadow scorer ───────────────────────────────────────────────────────────────────
def rescore(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Chấm lại BUY + state cho toàn bộ kho dưới bộ tham số ``p``. Trả df copy có
    cột shadow_state / shadow_buy (+ nhóm điểm shadow_* để debug)."""
    d = df
    n = len(d)

    # 1. Liquidity — intraday giữ điểm đã lưu (không tunable từ EOD)
    s_gtgd = _pw(d["liq_safety_ratio"], p["B_GTGD20"])
    s_cv = _pw(d["liq_cv"], p["B_CV"])
    s_intr = d["liq_score_intraday"].to_numpy(dtype=float)
    wl = p["W_LIQUIDITY"]
    liq = wl["gtgd20"] * s_gtgd + wl["intraday"] * s_intr + wl["cv"] * s_cv

    # 2. Momentum
    r1 = d["mom_return_1d"].to_numpy(dtype=float)
    r5 = d["mom_return_5d"].to_numpy(dtype=float)
    r20 = d["mom_return_20d"].to_numpy(dtype=float)
    s1, s5, s20 = _pw(r1, p["B_R1D"]), _pw(r5, p["B_R5D"]), _pw(r20, p["B_R20D"])
    npos = (r1 > 0).astype(int) + (r5 > 0).astype(int) + (r20 > 0).astype(int)
    cmult = np.asarray([p["CONSISTENCY_MULT"][k] for k in npos])
    wc = p["W_COMPOSITE"]
    composite = np.minimum(100.0, (wc["r1d"] * s1 + wc["r5d"] * s5 + wc["r20d"] * s20) * cmult)

    wm = p["W_MA"]
    s_slope = (p["W_SLOPE"]["ma20"] * _pw(d["mom_slope_ma20"], p["B_SLOPE20"])
               + p["W_SLOPE"]["ma50"] * _pw(d["mom_slope_ma50"], p["B_SLOPE50"]))
    ma = (wm["price_vs_ma20"] * _pw(d["mom_price_vs_ma20"], p["B_PVMA20"])
          + wm["price_vs_ma50"] * _pw(d["mom_price_vs_ma50"], p["B_PVMA50"])
          + wm["alignment"] * _pw(d["mom_ma20_vs_ma50"], p["B_ALIGN"])
          + wm["slope"] * s_slope)

    rs1 = d["mom_rs_1m"].to_numpy(dtype=float)
    rs3 = d["mom_rs_3m"].to_numpy(dtype=float)
    rsw = p["W_RS"]["rs_3m"] * rs3 + p["W_RS"]["rs_1m"] * rs1
    rs = np.minimum(100.0, _pw(rsw, p["B_RSW"]) * _pw(rs1 - rs3, p["B_RS_ACCEL"]))

    # flow: SMF trung tính (backtest không có flow) — convergence bucket hóa
    ad_ratio = d["mom_ad_ratio"].to_numpy(dtype=float)
    s_ad = np.where(np.isinf(ad_ratio), p["B_AD"]["default"], _pw(ad_ratio, p["B_AD"]))
    smf = np.full(n, float(p["NEUTRAL_FLOW"]))
    ad_b = np.where(s_ad >= 70, 2, np.where(s_ad >= 40, 1, 0))     # HIGH/MID/LOW
    conv = np.where(ad_b == 2, 1.05, np.where(ad_b == 1, 1.00, 0.92))  # SMF=MID cố định
    flow = np.minimum(100.0, (p["W_FLOW"]["ad"] * s_ad + p["W_FLOW"]["smf"] * smf) * conv)

    tech = (p["W_TECHNICAL"]["rsi"] * _pw(d["mom_rsi"], p["B_RSI"])
            + p["W_TECHNICAL"]["macd"] * _pw(d["mom_macd_hist_pct"], p["B_MACD"]))

    wmo = p["W_MOMENTUM"]
    mom_raw = (wmo["composite"] * composite + wmo["ma"] * ma + wmo["rs"] * rs
               + wmo["flow"] * flow + wmo["technical"] * tech)
    momentum = mom_raw / sum(wmo.values())

    # 3. State machine
    ratio = d["bo_breakout_ratio"].to_numpy(dtype=float)
    age = d["breakout_age"].to_numpy(dtype=float)
    closing = d["bo_closing_strength"].to_numpy(dtype=float)
    dry = d["bo_dry_up_ratio"].to_numpy(dtype=float)
    narrow = d["bo_narrowing_ratio"].to_numpy(dtype=float)
    dist_below = d["setup_dist_below_pivot"].to_numpy(dtype=float)
    recent_above = d["bo_recent_above"].fillna(0).to_numpy(dtype=float)
    align_raw = d["mom_ma20_vs_ma50"].to_numpy(dtype=float)
    slope20_raw = d["mom_slope_ma20"].to_numpy(dtype=float)

    above = ratio >= 1.0
    thrust = (r1 > p["FRESH_MIN_RETURN_1D"]) & (closing >= p["FRESH_MIN_CLOSING"])
    fresh_window = above & (age <= p["FRESH_MAX_AGE"]) & (ratio <= p["FRESH_MAX_RATIO"])
    is_fresh = fresh_window & thrust
    is_late = above & ~fresh_window
    is_pre = (~above & (recent_above == 0)
              & (dist_below <= p["PRE_MAX_DIST"])
              & (dry < p["PRE_DRYUP_MAX"]) & (narrow < p["PRE_NARROWING_MAX"])
              & (align_raw > 0) & (slope20_raw > 0) & (rsw >= 0))
    state = np.where(is_fresh, "BREAKOUT_FRESH",
             np.where(is_late, "BREAKOUT_LATE",
              np.where(is_pre, "PRE_BREAKOUT", "NONE")))

    # 4. Signal: Trigger / Setup
    wt = p["W_TRIGGER"]
    age_f = _pw(np.maximum(age, 0), p["B_AGE_FACTOR"])
    trigger = (wt["price"] * _pw(ratio, p["B_PRICE_FRESH"])
               + wt["volume"] * _pw(d["bo_volume_ratio"], p["B_VOLR"])
               + wt["dry_up"] * _pw(dry, p["B_DRYUP"])
               + wt["base_quality"] * _pw(narrow, p["B_BASE"])
               + wt["closing"] * _pw(closing, p["B_CLOSING"])) * age_f

    ws = p["W_SETUP"]
    s_align = _pw(align_raw, p["B_ALIGN"])
    structure = (p["W_SETUP_STRUCTURE"]["alignment"] * s_align
                 + p["W_SETUP_STRUCTURE"]["slope"] * s_slope)
    setup = (ws["proximity"] * _pw(dist_below, p["B_PROX"])
             + ws["base_quality"] * _pw(narrow, p["B_BASE"])
             + ws["dry_up"] * _pw(dry, p["B_DRYUP"])
             + ws["structure"] * structure + ws["rs"] * rs)

    signal = np.where(state == "PRE_BREAKOUT", setup,
              np.where(above, trigger, 0.0))
    signal = np.where(state == "NONE", 0.0, signal)

    # 5. BUY cuối
    overheat = _pw(d["mom_rsi"], p["B_OVERHEAT_RSI"]) * _pw(d["mom_price_vs_ma20"], p["B_OVERHEAT_EXT"])
    overhead = _pw(d["bo_dist_to_high"], p["B_OVERHEAD"])
    smult = np.select([state == "BREAKOUT_FRESH", state == "PRE_BREAKOUT",
                       state == "BREAKOUT_LATE"],
                      [p["STATE_MULT"]["BREAKOUT_FRESH"], p["STATE_MULT"]["PRE_BREAKOUT"],
                       p["STATE_MULT"]["BREAKOUT_LATE"]], 0.0)
    wb = p["W_BUY"]
    buy = (wb["liquidity"] * liq + wb["momentum"] * momentum + wb["signal"] * signal) \
        * overheat * overhead * smult

    out = d.copy()
    out["shadow_state"] = state
    out["shadow_buy"] = buy
    out["shadow_liq"] = liq
    out["shadow_mom"] = momentum
    out["shadow_signal"] = signal
    # multiplier cache — cho fast-path recombine khi chỉ tune W_BUY / STATE_MULT
    out["shadow_overheat"] = overheat
    out["shadow_overhead"] = overhead
    return out


def recombine(scored: pd.DataFrame, w_buy: dict, state_mult: dict) -> pd.DataFrame:
    """Fast-path: tính lại BUY từ cột đã cache khi CHỈ đổi W_BUY/STATE_MULT
    (liq/mom/signal/overheat/overhead không đổi) — mili-giây thay vì giây."""
    st = scored["shadow_state"].to_numpy()
    smult = np.select([st == "BREAKOUT_FRESH", st == "PRE_BREAKOUT", st == "BREAKOUT_LATE"],
                      [state_mult["BREAKOUT_FRESH"], state_mult["PRE_BREAKOUT"],
                       state_mult["BREAKOUT_LATE"]], 0.0)
    buy = ((w_buy["liquidity"] * scored["shadow_liq"].to_numpy()
            + w_buy["momentum"] * scored["shadow_mom"].to_numpy()
            + w_buy["signal"] * scored["shadow_signal"].to_numpy())
           * scored["shadow_overheat"].to_numpy() * scored["shadow_overhead"].to_numpy()
           * smult)
    out = scored.copy()
    out["shadow_buy"] = buy
    return out


# ── Parity ──────────────────────────────────────────────────────────────────────────
def load_store(lo="2016-01-01", hi="2026-12-31") -> pd.DataFrame:
    import glob
    import manipulation_blacklist as manip
    files = sorted(glob.glob(os.path.join(BT_DIR, "bt_*.parquet")))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df = df[(df["date"] >= lo) & (df["date"] <= hi)]
    return manip.tag(df).reset_index(drop=True)


def parity(df: pd.DataFrame) -> bool:
    """Bộ tham số hiện hành phải tái tạo được buy_score/state đã lưu."""
    r = rescore(df, default_params())
    state_match = (r["shadow_state"] == r["state"]).mean()
    diff = (r["shadow_buy"] - r["buy_score"]).abs()
    print(f"PARITY trên {len(r):,} dòng:")
    print(f"  state khớp : {state_match*100:.2f}%  (yêu cầu ≥ 99%)")
    print(f"  |Δbuy| trung vị {diff.median():.3f} · p95 {diff.quantile(0.95):.3f} · "
          f"max {diff.max():.2f}  (yêu cầu p95 < 0.5)")
    bad = r[(r["shadow_state"] != r["state"])].head(5)
    if len(bad):
        print("  Ví dụ lệch state:")
        print(bad[["date", "symbol", "state", "shadow_state", "bo_breakout_ratio",
                   "breakout_age", "mom_return_1d", "bo_closing_strength"]].to_string(index=False))
    return state_match >= 0.99 and diff.quantile(0.95) < 0.5


# ── W3: Simulator + objective ───────────────────────────────────────────────────────
TRAIN = ("2016-01-01", "2021-12-31")
VALID = ("2022-01-01", "2023-12-31")
MAE_PENALTY = 0.3        # objective = mean(retT5) − 0.3·mean(|MAE5|)  (user chốt 19/07)
TOP_N = 5
MIN_BUY = 50.0


def attach_regime(df: pd.DataFrame) -> pd.DataFrame:
    """Regime gate mỗi ngày (đúng công thức live) — dùng cho luật chọn danh sách."""
    vn = pd.read_parquet(os.path.join(config.DATA_DIR, "history", "VNINDEX.parquet"))
    close = vn["close"]
    ma20 = close.rolling(20).mean()
    ma5 = close.rolling(5).mean()
    ratio = close / ma20
    regime = pd.Series("ok", index=vn.index)
    regime[ratio < 1.00] = "caution"
    regime[(ratio < 0.97) & (ma5 < ma20)] = "blocked"
    m = pd.Series(regime.values, index=vn["time"].astype(str).str[:10])
    out = df.copy()
    out["regime"] = out["date"].map(m).fillna("ok")
    return out


def simulate(scored: pd.DataFrame) -> pd.DataFrame:
    """Mô phỏng danh sách alert theo luật live trên điểm shadow: loại manip; bỏ ngày
    blocked; LATE chỉ khi regime ok; BUY ≥ 50; top-5/ngày theo BUY.
    (Xấp xỉ: không sector-cap — kho không có ngành lịch sử; không dedup xuyên ngày.)"""
    d = scored[~scored["manip"]]
    d = d[d["regime"] != "blocked"]
    ok_late = (d["shadow_state"] == "BREAKOUT_LATE") & (d["regime"] == "ok")
    actionable = d[d["shadow_state"].isin(["BREAKOUT_FRESH", "PRE_BREAKOUT"]) | ok_late]
    eligible = actionable[actionable["shadow_buy"] >= MIN_BUY]
    picks = (eligible.sort_values(["date", "shadow_buy"], ascending=[True, False])
             .groupby("date").head(TOP_N))
    return picks


def objective(picks: pd.DataFrame) -> dict:
    r = picks[picks["ret_t5"].notna()]
    if len(r) < 50:                       # quá ít tín hiệu → phạt nặng (cấu hình tồi)
        return {"obj": -99.0, "n": len(r)}
    obj = r["ret_t5"].mean() - MAE_PENALTY * r["mae5"].abs().mean()
    return {"obj": round(float(obj), 4), "n": len(r),
            "ret_t3": round(float(r["ret_t3"].mean()), 3),
            "ret_t5": round(float(r["ret_t5"].mean()), 3),
            "ret_t10": round(float(r["ret_t10"].mean()), 3),
            "win_t3": round(float((r["ret_t3"] > 0).mean() * 100), 1),
            "mae5": round(float(r["mae5"].abs().mean()), 3),
            "picks_per_year": round(len(r) / max(1, len(r["date"].str[:4].unique())), 0)}


def evaluate(df: pd.DataFrame, p: dict, period=TRAIN) -> dict:
    """Điểm objective của bộ tham số ``p`` trên một giai đoạn."""
    d = df[(df["date"] >= period[0]) & (df["date"] <= period[1])]
    return objective(simulate(rescore(d, p)))


# ── W4: Tối ưu có kỷ luật trên TRAIN ────────────────────────────────────────────────
import copy
import itertools
import json

TRIAL_LOG = os.path.join(BT_DIR, "tuning_trials.jsonl")

WEIGHT_SETS = ["W_BUY", "STATE_MULT", "W_TRIGGER", "W_SETUP", "W_MOMENTUM",
               "W_LIQUIDITY", "W_MA", "W_COMPOSITE", "W_TECHNICAL", "W_RS", "W_FLOW"]
BAND_SETS = ["B_PRICE_FRESH", "B_DRYUP", "B_BASE", "B_CLOSING", "B_VOLR", "B_RSI",
             "B_MACD", "B_R1D", "B_R5D", "B_R20D", "B_PVMA20", "B_PVMA50", "B_ALIGN",
             "B_SLOPE20", "B_SLOPE50", "B_RSW", "B_AD", "B_GTGD20", "B_CV", "B_PROX",
             "B_AGE_FACTOR", "B_OVERHEAT_RSI", "B_OVERHEAT_EXT", "B_OVERHEAD"]
NON_MONOTONIC = {"B_RSI", "B_R5D", "B_R20D", "B_PVMA20", "B_PVMA50"}   # spec chủ ý có đỉnh giữa


def _log_trial(kind, name, params_desc, res):
    with open(TRIAL_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": kind, "name": name, "desc": params_desc,
                            **res}, ensure_ascii=False) + "\n")


def bootstrap_sigma(df_train, p, n_boot=30) -> float:
    """Sàn nhiễu: std của objective khi block-bootstrap THEO NGÀY danh sách picks."""
    picks = simulate(rescore(df_train, p))
    days = picks["date"].unique()
    rng = np.random.default_rng(7)
    objs = []
    for _ in range(n_boot):
        sample_days = rng.choice(days, size=len(days), replace=True)
        counts = pd.Series(sample_days).value_counts()
        idx = picks.index[picks["date"].isin(counts.index)]
        b = picks.loc[idx].merge(counts.rename("w"), left_on="date", right_index=True)
        r = b[b["ret_t5"].notna()]
        obj = (np.average(r["ret_t5"], weights=r["w"])
               - MAE_PENALTY * np.average(r["mae5"].abs(), weights=r["w"]))
        objs.append(obj)
    return float(np.std(objs))


def _simplex_grid(current: dict, step=0.05, radius=0.15, cap=120):
    """Các tổ hợp trọng số quanh điểm hiện tại (±radius, bước step, tổng bảo toàn).
    Bộ >3 thành phần nổ tổ hợp → lấy mẫu ngẫu nhiên có seed, tối đa ``cap`` combo
    (mỗi combo với bộ trong cần full-rescore ~2s → cap giữ tổng thời gian hợp lý)."""
    keys = list(current.keys())
    total = round(sum(current.values()), 4)
    ranges = [np.arange(max(0.0, current[k] - radius), current[k] + radius + 1e-9, step)
              for k in keys]
    n_product = int(np.prod([len(r) for r in ranges]))
    combos, seen = [], set()

    def _add(vals):
        s = sum(vals)
        if s <= 0:
            return
        w = {k: round(v * total / s, 3) for k, v in zip(keys, vals)}
        key = tuple(w.values())
        if key not in seen:
            seen.add(key)
            combos.append(w)

    if n_product <= cap * 3:
        for vals in itertools.product(*ranges):
            _add(vals)
    else:
        rng = np.random.default_rng(11)
        for _ in range(cap * 3):
            _add([float(rng.choice(r)) for r in ranges])
    _add([current[k] for k in keys])          # luôn gồm điểm hiện tại
    return combos[: cap * 3]


def tune_weight_set(df_train, base_params, set_name, sigma):
    """Grid quanh điểm hiện tại cho MỘT bộ trọng số; giữ nếu không vượt sàn nhiễu."""
    p = copy.deepcopy(base_params)
    fast = set_name in ("W_BUY", "STATE_MULT")
    scored_cache = rescore(df_train, p) if fast else None
    best_w, best_obj = dict(p[set_name]), None

    if set_name == "STATE_MULT":
        # NONE cố định 0; FRESH neo 1.0; chỉ dò PRE & LATE (0.5→1.1, bước 0.05)
        combos = [{"BREAKOUT_FRESH": 1.0, "PRE_BREAKOUT": round(pre, 2),
                   "BREAKOUT_LATE": round(late, 2), "NONE": 0.0}
                  for pre in np.arange(0.7, 1.101, 0.05)
                  for late in np.arange(0.5, 1.101, 0.05)]
    else:
        combos = _simplex_grid(p[set_name])

    for w in combos:
        if fast:
            scored = recombine(scored_cache, w if set_name == "W_BUY" else p["W_BUY"],
                               w if set_name == "STATE_MULT" else p["STATE_MULT"])
        else:
            p2 = copy.deepcopy(p)
            p2[set_name] = w
            scored = rescore(df_train, p2)
        res = objective(simulate(scored))
        if best_obj is None or res["obj"] > best_obj["obj"]:
            best_obj, best_w = res, w
    base_res = evaluate(df_train, base_params, (df_train["date"].min(), df_train["date"].max()))
    improved = best_obj["obj"] - base_res["obj"]
    keep = improved > sigma          # phải vượt sàn nhiễu bootstrap
    _log_trial("weights", set_name, best_w, {**best_obj, "improve": round(improved, 4),
                                             "sigma": round(sigma, 4), "kept": keep})
    return (best_w if keep else dict(base_params[set_name])), improved, keep


def refit_band(df_train, base_params, band_name, sigma, n_buckets=6):
    """Refit MỘT bảng band: cạnh theo quantile dữ liệu (trên nhóm dòng liên quan),
    điểm theo outcome trung bình bucket (map 20..100, làm tròn 5, isotonic nếu bảng
    đơn điệu). Giữ nếu vượt sàn nhiễu."""
    col_map = {
        "B_PRICE_FRESH": ("bo_breakout_ratio", "above"), "B_DRYUP": ("bo_dry_up_ratio", "all"),
        "B_BASE": ("bo_narrowing_ratio", "all"), "B_CLOSING": ("bo_closing_strength", "all"),
        "B_VOLR": ("bo_volume_ratio", "above"), "B_RSI": ("mom_rsi", "all"),
        "B_MACD": ("mom_macd_hist_pct", "all"), "B_R1D": ("mom_return_1d", "all"),
        "B_R5D": ("mom_return_5d", "all"), "B_R20D": ("mom_return_20d", "all"),
        "B_PVMA20": ("mom_price_vs_ma20", "all"), "B_PVMA50": ("mom_price_vs_ma50", "all"),
        "B_ALIGN": ("mom_ma20_vs_ma50", "all"), "B_SLOPE20": ("mom_slope_ma20", "all"),
        "B_SLOPE50": ("mom_slope_ma50", "all"), "B_RSW": (None, "rsw"),
        "B_AD": ("mom_ad_ratio", "all"), "B_GTGD20": ("liq_safety_ratio", "all"),
        "B_CV": ("liq_cv", "all"), "B_PROX": ("setup_dist_below_pivot", "below"),
        "B_AGE_FACTOR": ("breakout_age", "above"),
        "B_OVERHEAT_RSI": ("mom_rsi", "signal"), "B_OVERHEAT_EXT": ("mom_price_vs_ma20", "signal"),
        "B_OVERHEAD": ("bo_dist_to_high", "signal"),
    }
    col, scope = col_map[band_name]
    d = df_train
    if scope == "above":
        d = d[d["bo_breakout_ratio"] >= 1.0]
    elif scope == "below":
        d = d[d["bo_breakout_ratio"] < 1.0]
    elif scope == "signal":
        d = d[d["state"] != "NONE"]
    if scope == "rsw":
        x = (base_params["W_RS"]["rs_3m"] * d["mom_rs_3m"]
             + base_params["W_RS"]["rs_1m"] * d["mom_rs_1m"])
    else:
        x = d[col]
    y = d["ret_t5"] - MAE_PENALTY * d["mae5"].abs()
    m = x.notna() & y.notna() & np.isfinite(x)
    x, y = x[m], y[m]
    if len(x) < 2000:
        return dict(base_params[band_name]), 0.0, False

    # Cạnh mới = quantile; với bảng hệ số (multiplier) giữ nguyên cạnh, chỉ refit giá trị
    is_mult = band_name.startswith("B_AGE") or band_name.startswith("B_OVERHE")
    old = base_params[band_name]
    edges = old["uppers"] if is_mult else \
        sorted({round(float(q), 4) for q in x.quantile(np.linspace(1 / n_buckets, 1 - 1 / n_buckets,
                                                                    n_buckets - 1)).tolist()})
    buckets = np.searchsorted(np.asarray(edges, dtype=float), x.to_numpy(), side="right")
    means = pd.Series(y.to_numpy()).groupby(buckets).mean()
    if len(means) < 2:
        return dict(base_params[band_name]), 0.0, False
    # map outcome bucket → điểm 20..100 (multiplier: 0.4..1.0), làm tròn
    lo_v, hi_v = means.min(), means.max()
    span = (hi_v - lo_v) or 1.0
    if is_mult:
        vals = (0.4 + 0.6 * (means - lo_v) / span).round(2)
    else:
        vals = ((20 + 80 * (means - lo_v) / span) / 5).round() * 5
    if band_name not in NON_MONOTONIC and not is_mult:
        vals = vals.cummax() if vals.iloc[-1] >= vals.iloc[0] else vals[::-1].cummax()[::-1]
    scores = [float(vals.get(i, vals.iloc[-1])) for i in range(len(edges))]
    default = float(vals.iloc[-1]) if vals.index.max() >= len(edges) else float(vals.iloc[-1])
    cand = {"uppers": list(edges), "scores": scores, "default": default}

    p2 = copy.deepcopy(base_params)
    p2[band_name] = cand
    base_res = evaluate(df_train, base_params, (df_train["date"].min(), df_train["date"].max()))
    new_res = evaluate(df_train, p2, (df_train["date"].min(), df_train["date"].max()))
    improved = new_res["obj"] - base_res["obj"]
    keep = improved > sigma
    _log_trial("band", band_name, cand, {**new_res, "improve": round(improved, 4),
                                         "sigma": round(sigma, 4), "kept": keep})
    return (cand if keep else dict(base_params[band_name])), improved, keep


def tune(df: pd.DataFrame, out_path: str):
    """Chu trình W4 đầy đủ: sensitivity → weights → bands, 2 vòng; log + lưu JSON."""
    dtr = df[(df["date"] >= TRAIN[0]) & (df["date"] <= TRAIN[1])].reset_index(drop=True)
    p = default_params()
    if os.path.exists(TRIAL_LOG):
        os.remove(TRIAL_LOG)
    base = evaluate(dtr, p, TRAIN)
    sigma = bootstrap_sigma(dtr, p)
    print(f"BASELINE TRAIN: {base} · sàn nhiễu σ = {sigma:.4f}")
    _log_trial("baseline", "current", {}, {**base, "sigma": round(sigma, 4)})

    changed = []
    for rnd in (1, 2):
        print(f"— Vòng {rnd}: weights —")
        for s in WEIGHT_SETS:
            w, imp, kept = tune_weight_set(dtr, p, s, sigma)
            if kept:
                p[s] = w
                changed.append((s, w, round(imp, 4)))
                print(f"  {s}: ĐỔI {w} (+{imp:.4f})")
            else:
                print(f"  {s}: giữ nguyên (best +{imp:.4f} ≤ σ)")
        print(f"— Vòng {rnd}: bands —")
        for b in BAND_SETS:
            band, imp, kept = refit_band(dtr, p, b, sigma)
            if kept:
                p[b] = band
                changed.append((b, band, round(imp, 4)))
                print(f"  {b}: ĐỔI (+{imp:.4f})")
            else:
                print(f"  {b}: giữ nguyên (+{imp:.4f} ≤ σ)")

    final_train = evaluate(dtr, p, TRAIN)
    final_valid = evaluate(df, p, VALID)
    base_valid = evaluate(df, default_params(), VALID)
    # Walk-forward health check: fit đã dùng cả train → chỉ report split đánh giá
    wf_fit = evaluate(df, p, ("2016-01-01", "2018-12-31"))
    wf_test = evaluate(df, p, ("2019-01-01", "2021-12-31"))
    result = {"baseline_train": base, "tuned_train": final_train,
              "baseline_valid": base_valid, "tuned_valid": final_valid,
              "walkforward_fit_2016_18": wf_fit, "walkforward_test_2019_21": wf_test,
              "sigma": sigma, "changed": [(c[0], c[2]) for c in changed],
              "params": p}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1, default=str)
    print(f"\nTRAIN : {base['obj']} → {final_train['obj']}")
    print(f"VALID : {base_valid['obj']} → {final_valid['obj']}  (cổng: phải > baseline)")
    print(f"Đã lưu {out_path}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "parity":
        ok = parity(load_store())
        print("\nPARITY:", "PASS ✅" if ok else "FAIL ❌")
        sys.exit(0 if ok else 1)
    if len(sys.argv) > 1 and sys.argv[1] == "baseline":
        df = attach_regime(load_store())
        print("Baseline (bộ hiện hành):")
        print("  TRAIN:", evaluate(df, default_params(), TRAIN))
        print("  VALID:", evaluate(df, default_params(), VALID))
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "tune":
        df = attach_regime(load_store())
        tune(df, os.path.join(BT_DIR, "tuned_params.json"))
        sys.exit(0)
    print("usage: python tuner.py parity|baseline|tune")
