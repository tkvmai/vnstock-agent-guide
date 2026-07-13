"""Momentum Score (Spec 3.2).

Momentum = 0.30·composite + 0.20·ma + 0.20·rs + 0.25·flow + 0.10·technical
(weights sum to 1.05 per the spec — normalised by their sum here; see config.py).
"""

import numpy as np
import pandas as pd

import config
from . import tables as T
from . import indicators as ind


def _pct_change_n(close_today: float, hist_close: pd.Series, n: int) -> float:
    """% change of close_today vs the close n sessions ago (clamped to history)."""
    if len(hist_close) == 0:
        return 0.0
    n = min(n, len(hist_close))
    past = float(hist_close.iloc[-n])
    if past == 0:
        return 0.0
    return (close_today - past) / past * 100


def _score_composite(close_today: float, hist_close: pd.Series) -> dict:
    r1 = _pct_change_n(close_today, hist_close, 1)
    r5 = _pct_change_n(close_today, hist_close, 5)
    r20 = _pct_change_n(close_today, hist_close, 20)
    s1 = T.piecewise(r1, T.RETURN_1D_BANDS, T.RETURN_1D_DEFAULT)
    s5 = T.piecewise(r5, T.RETURN_5D_BANDS, T.RETURN_5D_DEFAULT)
    s20 = T.piecewise(r20, T.RETURN_20D_BANDS, T.RETURN_20D_DEFAULT)
    n_positive = sum(1 for r in (r1, r5, r20) if r > 0)
    mult = config.CONSISTENCY_MULT[n_positive]
    w = config.W_COMPOSITE
    base = w["r1d"] * s1 + w["r5d"] * s5 + w["r20d"] * s20
    composite = min(100.0, base * mult)
    return {"composite": composite, "return_1d": r1, "return_5d": r5,
            "return_20d": r20, "consistency_mult": mult}


def _score_ma(close_today: float, hist_close: pd.Series) -> dict:
    ma20 = ind.sma(hist_close, config.LOOKBACK_MA20)
    ma50 = ind.sma(hist_close, config.LOOKBACK_MA50)
    # MA values as of 10 sessions ago (for slope)
    ma20_10d = ind.sma(hist_close.iloc[:-config.LOOKBACK_SLOPE], config.LOOKBACK_MA20)
    ma50_10d = ind.sma(hist_close.iloc[:-config.LOOKBACK_SLOPE], config.LOOKBACK_MA50)

    pvma20 = (close_today - ma20) / ma20 * 100 if ma20 else 0.0
    pvma50 = (close_today - ma50) / ma50 * 100 if ma50 else 0.0
    ma20_vs_ma50 = (ma20 - ma50) / ma50 * 100 if ma50 else 0.0
    slope_ma20 = (ma20 - ma20_10d) / ma20_10d * 100 if ma20_10d else 0.0
    slope_ma50 = (ma50 - ma50_10d) / ma50_10d * 100 if ma50_10d else 0.0

    s_pvma20 = T.piecewise(pvma20, T.PRICE_VS_MA20_BANDS, T.PRICE_VS_MA20_DEFAULT)
    s_pvma50 = T.piecewise(pvma50, T.PRICE_VS_MA50_BANDS, T.PRICE_VS_MA50_DEFAULT)
    s_align = T.piecewise(ma20_vs_ma50, T.ALIGNMENT_BANDS, T.ALIGNMENT_DEFAULT)
    s_slope20 = T.piecewise(slope_ma20, T.SLOPE_MA20_BANDS, T.SLOPE_MA20_DEFAULT)
    s_slope50 = T.piecewise(slope_ma50, T.SLOPE_MA50_BANDS, T.SLOPE_MA50_DEFAULT)
    s_slope = config.W_SLOPE["ma20"] * s_slope20 + config.W_SLOPE["ma50"] * s_slope50

    w = config.W_MA
    score = (w["price_vs_ma20"] * s_pvma20 + w["price_vs_ma50"] * s_pvma50
             + w["alignment"] * s_align + w["slope"] * s_slope)
    return {"ma": score, "price_vs_ma20": pvma20, "price_vs_ma50": pvma50,
            "ma20_vs_ma50": ma20_vs_ma50, "slope_ma20": slope_ma20,
            "slope_ma50": slope_ma50}


def _score_rs(close_today: float, hist_close: pd.Series,
              vnindex_today: float, vnindex_close: pd.Series) -> dict:
    stock_1m = _pct_change_n(close_today, hist_close, config.LOOKBACK_RS_1M)
    stock_3m = _pct_change_n(close_today, hist_close, config.LOOKBACK_RS_3M)
    vn_1m = _pct_change_n(vnindex_today, vnindex_close, config.LOOKBACK_RS_1M)
    vn_3m = _pct_change_n(vnindex_today, vnindex_close, config.LOOKBACK_RS_3M)
    rs_1m = stock_1m - vn_1m
    rs_3m = stock_3m - vn_3m
    rs_weighted = config.W_RS["rs_3m"] * rs_3m + config.W_RS["rs_1m"] * rs_1m
    rs_base = T.piecewise(rs_weighted, T.RS_WEIGHTED_BANDS, T.RS_WEIGHTED_DEFAULT)
    accel = T.rs_accel_mult(rs_1m, rs_3m)
    score = min(100.0, rs_base * accel)
    return {"rs": score, "rs_1m": rs_1m, "rs_3m": rs_3m, "rs_weighted": rs_weighted}


def _score_flow(hist: pd.DataFrame, flow: dict) -> dict:
    h = hist.iloc[-config.LOOKBACK_AD:]
    diff = h["close"].diff()
    up_vol = h["volume"][diff > 0]
    down_vol = h["volume"][diff < 0]
    if len(down_vol) == 0 or down_vol.mean() == 0:
        ad_ratio = float("inf")
        s_ad = T.AD_RATIO_DEFAULT
    else:
        up_mean = up_vol.mean() if len(up_vol) else 0.0
        ad_ratio = up_mean / down_vol.mean()
        s_ad = T.piecewise(ad_ratio, T.AD_RATIO_BANDS, T.AD_RATIO_DEFAULT)

    fpct = (flow or {}).get("foreign_net_pct")
    ppct = (flow or {}).get("prop_net_pct")
    s_foreign = (T.piecewise(fpct, T.FOREIGN_NET_BANDS, T.FOREIGN_NET_DEFAULT)
                 if fpct is not None else config.NEUTRAL_FLOW_SCORE)
    s_prop = (T.piecewise(ppct, T.PROP_NET_BANDS, T.PROP_NET_DEFAULT)
              if ppct is not None else config.NEUTRAL_FLOW_SCORE)
    smf = config.W_SMF["foreign"] * s_foreign + config.W_SMF["prop"] * s_prop

    conv = T.convergence_mult(s_ad, smf)
    flow_base = config.W_FLOW["ad"] * s_ad + config.W_FLOW["smf"] * smf
    score = min(100.0, flow_base * conv)
    return {"flow": score, "ad_ratio": ad_ratio, "score_ad": s_ad,
            "smf": smf, "convergence_mult": conv,
            "foreign_net_pct": fpct, "prop_net_pct": ppct,
            "score_foreign": s_foreign, "score_prop": s_prop}


def _score_technical(close_today: float, hist_close: pd.Series) -> dict:
    # Append the live close so RSI/MACD reflect the current bar (Spec 3.2.5).
    series = pd.concat([hist_close, pd.Series([close_today])], ignore_index=True)
    rsi = ind.wilder_rsi(series, 14)
    macd_hist = ind.macd_histogram(series)
    hist_pct = macd_hist / close_today * 100 if close_today else 0.0
    s_rsi = T.piecewise(rsi, T.RSI_BANDS, T.RSI_DEFAULT)
    s_macd = T.piecewise(hist_pct, T.MACD_HIST_BANDS, T.MACD_HIST_DEFAULT)
    score = config.W_TECHNICAL["rsi"] * s_rsi + config.W_TECHNICAL["macd"] * s_macd
    return {"technical": score, "rsi": rsi, "macd_hist_pct": hist_pct}


def score_momentum(hist: pd.DataFrame, live: dict, vnindex_hist: pd.DataFrame,
                   flow: dict = None) -> dict:
    """Full momentum score for one stock."""
    close_today = float(live["close"])
    hist_close = hist["close"].reset_index(drop=True)
    vn_close = vnindex_hist["close"].reset_index(drop=True) if vnindex_hist is not None else pd.Series(dtype=float)
    vn_today = float(vn_close.iloc[-1]) if len(vn_close) else close_today

    comp = _score_composite(close_today, hist_close)
    ma = _score_ma(close_today, hist_close)
    rs = _score_rs(close_today, hist_close, vn_today, vn_close)
    fl = _score_flow(hist, flow)
    tech = _score_technical(close_today, hist_close)

    w = config.W_MOMENTUM
    raw = (w["composite"] * comp["composite"] + w["ma"] * ma["ma"]
           + w["rs"] * rs["rs"] + w["flow"] * fl["flow"]
           + w["technical"] * tech["technical"])
    momentum = raw / sum(w.values())  # normalise the 1.05 spec weight sum

    out = {"momentum": round(momentum, 2)}
    for d in (comp, ma, rs, fl, tech):
        out.update(d)
    return out
