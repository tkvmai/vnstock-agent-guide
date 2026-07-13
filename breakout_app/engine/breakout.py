"""Trigger (confirmed-breakout) score — Spec RevD 2.2a.

Trigger = (0.35·price_fresh + 0.25·volume + 0.20·dry_up + 0.10·base_quality
           + 0.10·closing) × age_factor

RevD changes vs RevC:
- `price_fresh` REWARDS a fresh cross and DECAYS with extension (RevC rewarded
  extension → recommended late).
- `breakout_age` (sessions since the cross) demotes stale breakouts via age_factor.
- RevC's `risk_ratio` penalty is dropped (extension→price_fresh, staleness→age,
  volatility→base_quality; keeping it would double-count).

The raw ratios (dry_up_ratio, narrowing_ratio, volume_ratio, closing_strength) are
ALWAYS computed — even below the pivot — so `engine/setup.py` can reuse them for the
pre-breakout Setup score.
"""

import pandas as pd

import config
from . import tables as T
from . import indicators as ind


def breakout_age(hist_close, close_today: float, lookback: int = None) -> int:
    """Sessions since the current run above the prior-`lookback` high began.

    0 = today is the first close above its own prior-20d high; 1 = broke out
    yesterday and still above; etc. Returns -1 when price is not above the pivot.
    """
    lb = lookback or config.LOOKBACK_CLOSE20
    closes = list(hist_close)
    if len(closes) < lb:
        return -1
    pivot_today = max(closes[-lb:])
    if close_today < pivot_today:
        return -1                       # below the pivot → not a breakout (age n/a)
    age = 0
    idx = len(closes) - 1               # start at yesterday
    while idx - lb >= 0:
        if closes[idx] > max(closes[idx - lb:idx]):
            age += 1                    # that session was itself a breakout day
            idx -= 1
        else:
            break
    return age


def score_breakout(hist: pd.DataFrame, live: dict, time_ratio: float = 1.0) -> dict:
    """Compute the Trigger score + all raw breakout metrics.

    Always returns the raw ratios (so setup.py can reuse them). ``breakout`` (the
    Trigger score) is 0 when price has not cleared the pivot (gate)."""
    close_today = float(live["close"])
    hist_close = hist["close"].reset_index(drop=True)
    pivot = float(hist_close.iloc[-config.LOOKBACK_CLOSE20:].max())
    breakout_ratio = close_today / pivot if pivot else 0.0
    age = breakout_age(hist_close, close_today)

    # ── Raw ratios (computed regardless of gate — reused by setup.py) ─────────────
    # Volume breakout (time-adjusted)
    avg_vol_20d = float(hist["volume"].iloc[-config.LOOKBACK_ATR_LONG:].mean())
    vol_intraday = float(live.get("volume", 0))
    vol_expected = avg_vol_20d * max(time_ratio, 1e-6)
    volume_ratio = vol_intraday / vol_expected if vol_expected else 0.0

    # Volume dry-up before breakout: 4 sessions T-5..T-1 vs 20 sessions T-6..T-25
    pre_vol_avg = float(hist["volume"].iloc[-5:-1].mean())
    base_vol_avg = float(hist["volume"].iloc[-25:-5].mean())
    dry_up_ratio = pre_vol_avg / base_vol_avg if base_vol_avg else 1.0

    # Base quality: ATR contraction (5d vs 20d), excluding today
    atr_5d = ind.atr(hist, config.LOOKBACK_ATR_SHORT)
    atr_20d = ind.atr(hist, config.LOOKBACK_ATR_LONG)
    narrowing = atr_5d / atr_20d if atr_20d else 1.0

    # Closing strength
    high, low = float(live.get("high", close_today)), float(live.get("low", close_today))
    closing_strength = 50.0 if high == low else (close_today - low) / (high - low) * 100

    # Failed-breakout detector (for PRE_BREAKOUT clean-coil check): how many of the
    # last N completed sessions closed above their own prior-20d high.
    lb = config.LOOKBACK_CLOSE20
    closes = list(hist_close)
    recent_above = 0
    for k in range(1, config.PRE_BREAKOUT_NO_BREAK_SESSIONS + 1):
        i = len(closes) - k
        if i - lb >= 0 and closes[i] > max(closes[i - lb:i]):
            recent_above += 1

    # Long-term high (~4 months = full fetched history) for the overhead-supply
    # penalty (P10): a 20d-pivot breakout deep below this level faces trapped sellers.
    hist_high = float(hist_close.max())
    dist_to_high = (close_today / hist_high - 1) * 100 if hist_high else None

    raw_metrics = {
        "breakout_ratio": round(breakout_ratio, 4),
        "breakout_age": age,
        "pivot": round(pivot, 2),
        "dist_to_high": round(dist_to_high, 2) if dist_to_high is not None else None,
        "recent_above": recent_above,
        "volume_ratio": round(volume_ratio, 2),
        "dry_up_ratio": round(dry_up_ratio, 2),
        "narrowing_ratio": round(narrowing, 2),
        "closing_strength": round(closing_strength, 1),
    }

    # ── Gate: no confirmed breakout → Trigger score 0 (setup may still apply) ─────
    if breakout_ratio < 1.0:
        return {"breakout": 0.0, "gated": True, **raw_metrics}

    # ── Trigger score (RevD 2.2a) ────────────────────────────────────────────────
    s_price = T.piecewise(breakout_ratio, T.PRICE_FRESH_BANDS, T.PRICE_FRESH_DEFAULT)
    s_vol = T.piecewise(volume_ratio, T.VOLUME_RATIO_BANDS, T.VOLUME_RATIO_DEFAULT)
    s_dryup = T.piecewise(dry_up_ratio, T.DRYUP_BANDS, T.DRYUP_DEFAULT)
    s_base = T.piecewise(narrowing, T.BASE_QUALITY_BANDS, T.BASE_QUALITY_DEFAULT)
    s_closing = T.piecewise(closing_strength, T.CLOSING_STRENGTH_BANDS, T.CLOSING_STRENGTH_DEFAULT)

    w = config.W_TRIGGER
    raw = (w["price"] * s_price + w["volume"] * s_vol + w["dry_up"] * s_dryup
           + w["base_quality"] * s_base + w["closing"] * s_closing)
    factor = T.age_factor(age)
    trigger = raw * factor

    return {
        "breakout": round(trigger, 2),
        "gated": False,
        "score_price_fresh": s_price,
        "age_factor": factor,
        **raw_metrics,
    }
