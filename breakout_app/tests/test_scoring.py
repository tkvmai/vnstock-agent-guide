"""Unit tests for the scoring engine (Spec RevD) using synthetic OHLCV data.

Run: python breakout_app/tests/test_scoring.py   (zero-dependency runner at bottom)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import config
from engine import tables as T
from engine import layer1, liquidity, breakout, setup, scoring


# ── Synthetic data builders ─────────────────────────────────────────────────────
def _make_hist(prices, volumes, high=None, low=None):
    n = len(prices)
    close = np.array(prices, dtype=float)
    high = close * 1.01 if high is None else np.array(high, dtype=float)
    low = close * 0.99 if low is None else np.array(low, dtype=float)
    return pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=n, freq="D"),
        "open": close, "high": high, "low": low, "close": close,
        "volume": np.array(volumes, dtype=float),
    })


def _base_then_breakout_hist(n=70):
    """Rising trend → flat base (below the pivot) for the last ~29 sessions, so a
    fresh cross has breakout_age 0 (not a monotonic string of new highs)."""
    rise = list(np.linspace(10_000, 12_900, n - 30))
    base = [12_900.0] * 29
    prices = rise + base                                   # n-1 completed sessions
    # dry-up into the base: last 4 completed sessions taper down
    vols = [1_000_000] * (len(prices) - 4) + [700_000, 600_000, 520_000, 500_000]
    return _make_hist(prices, vols)


def _live_from(hist, ratio, vol=2_000_000):
    """Build a live snapshot at `ratio` × pivot(20d high of hist)."""
    pivot = float(hist["close"].iloc[-config.LOOKBACK_CLOSE20:].max())
    close = pivot * ratio
    return {"close": close, "high": close * 1.005, "low": close * 0.995,
            "volume": vol, "minutes_elapsed": 225}


def _flat_market(n=70):
    rng = np.random.default_rng(42)
    prices = list(11_000 + rng.normal(0, 80, n))
    vols = list(800_000 + rng.normal(0, 50_000, n))
    return prices, vols


def _vnindex(n=70):
    return _make_hist(list(np.linspace(1_200, 1_250, n)), [1e8] * n)


# ── piecewise + RevD table functions ──────────────────────────────────────────────
def test_piecewise_monotonic():
    assert T.piecewise(5, T.GTGD20_BANDS, T.GTGD20_DEFAULT) == 0
    assert T.piecewise(250, T.GTGD20_BANDS, T.GTGD20_DEFAULT) == 100


def test_price_fresh_rewards_freshness_not_extension():
    # RevD: fresh cross scores 100, extended decays (opposite of RevC)
    assert T.piecewise(1.01, T.PRICE_FRESH_BANDS, T.PRICE_FRESH_DEFAULT) == 100
    assert T.piecewise(1.05, T.PRICE_FRESH_BANDS, T.PRICE_FRESH_DEFAULT) == 50
    assert T.piecewise(1.10, T.PRICE_FRESH_BANDS, T.PRICE_FRESH_DEFAULT) == 20


def test_age_factor_demotes_stale():
    assert T.age_factor(0) == 1.0
    assert T.age_factor(1) == 0.9
    assert T.age_factor(2) == 0.6
    assert T.age_factor(5) == 0.3


def test_overheat_penalizes():
    assert T.overheat_mult(65, 0) == 1.0            # calm
    assert T.overheat_rsi_mult(85) == 0.55          # very overbought
    assert T.overheat_ext_mult(10) == 0.65          # extended above MA20
    assert T.overheat_mult(85, 10) < 0.4            # both → compounding penalty


def test_overhead_supply_penalizes():
    # P10: 20d breakout deep below the long-term high faces trapped sellers
    assert T.overhead_mult(-2) == 1.0     # near the highs — clean air above
    assert T.overhead_mult(-7) == 0.90
    assert T.overhead_mult(-25) == 0.70   # deep inside a big base (DGC/PC1 cases)
    assert T.overhead_mult(None) == 1.0


def test_proximity_band():
    assert T.piecewise(0.5, T.PROXIMITY_BANDS, T.PROXIMITY_DEFAULT) == 100
    assert T.piecewise(2.5, T.PROXIMITY_BANDS, T.PROXIMITY_DEFAULT) == 55
    assert T.piecewise(4.0, T.PROXIMITY_BANDS, T.PROXIMITY_DEFAULT) == 0


# ── Market regime gate ──────────────────────────────────────────────────────────
def test_regime_ok():
    close = pd.Series(np.linspace(1_200, 1_300, 30))
    regime, ratio, _ = layer1.check_market_regime(close)
    assert regime == "ok" and ratio > 1.0


def test_regime_blocked():
    close = pd.Series(list(np.linspace(1_300, 1_250, 25)) + list(np.linspace(1_240, 1_150, 5)))
    regime, ratio, _ = layer1.check_market_regime(close)
    assert regime == "blocked" and ratio < 0.97


# ── Liquidity ─────────────────────────────────────────────────────────────────────
def test_liquidity_high_for_liquid_stock():
    metrics = {"gtgd20": 30e9, "cv": 20, "intraday_ratio": 180, "time_ratio": 1.0}
    out = liquidity.score_liquidity(metrics, position_size=50_000_000)
    assert out["liquidity"] > 75


# ── Breakout: gate, freshness, age ────────────────────────────────────────────────
def test_breakout_gate_when_below_high():
    hist = _base_then_breakout_hist()
    live = _live_from(hist, ratio=0.95)
    out = breakout.score_breakout(hist, live, time_ratio=1.0)
    assert out["gated"] is True and out["breakout"] == 0.0


def test_breakout_fresh_scores_high_and_age_zero():
    hist = _base_then_breakout_hist()
    live = _live_from(hist, ratio=1.015)
    out = breakout.score_breakout(hist, live, time_ratio=1.0)
    assert out["gated"] is False
    assert out["breakout_age"] == 0
    assert out["breakout"] > 50


# ── State machine + full BUY ──────────────────────────────────────────────────────
def test_fresh_beats_late():
    vn = _vnindex()
    hist = _base_then_breakout_hist()
    res_fresh = scoring.score_stock("FRESH", hist, _live_from(hist, 1.015), vn,
                                    flow={"foreign_net_pct": 3.0, "prop_net_pct": 1.5})
    res_late = scoring.score_stock("LATE", hist, _live_from(hist, 1.09), vn,
                                   flow={"foreign_net_pct": 3.0, "prop_net_pct": 1.5})
    assert res_fresh["state"] == "BREAKOUT_FRESH"
    assert res_late["state"] == "BREAKOUT_LATE"
    assert res_fresh["buy_score"] > res_late["buy_score"]
    assert 0 <= res_fresh["buy_score"] <= 100


def test_none_state_excluded_flat():
    vn = _vnindex()
    p_fl, v_fl = _flat_market()
    hist_fl = _make_hist(p_fl[:-1], v_fl[:-1])
    live_fl = {"close": p_fl[-1], "high": p_fl[-1] * 1.002, "low": p_fl[-1] * 0.998,
               "volume": v_fl[-1], "minutes_elapsed": 225}
    res = scoring.score_stock("FLAT", hist_fl, live_fl, vn)
    assert res["state"] in ("NONE", "PRE_BREAKOUT")   # flat/choppy is not a breakout
    if res["state"] == "NONE":
        assert res["buy_score"] == 0.0


# ── Thrust gate (loss-review P1): flat/red pivot touch is NOT a breakout ─────────
def test_thrust_gate_rejects_flat_touch():
    vn = _vnindex()
    hist = _base_then_breakout_hist()
    pivot = float(hist["close"].iloc[-config.LOOKBACK_CLOSE20:].max())
    # exactly at pivot, 0% day, weak close near the low (TCX/BMP 03/07 pattern)
    live = {"close": pivot * 1.0005, "high": pivot * 1.02, "low": pivot * 1.0,
            "volume": 2_000_000, "minutes_elapsed": 225}
    res = scoring.score_stock("FLATTOUCH", hist, live, vn)
    assert res["state"] == "NONE"          # no thrust → not a breakout
    assert res["buy_score"] == 0.0


# ── Pre-breakout detection (unit-level, deterministic) ────────────────────────────
def test_pre_breakout_detection():
    bo = {"breakout_ratio": 0.99, "pivot": 100.0, "dry_up_ratio": 0.6, "narrowing_ratio": 0.7,
          "recent_above": 0}
    mom = {"ma20_vs_ma50": 2.0, "slope_ma20": 0.5, "slope_ma50": 0.2,
           "rs_weighted": 5.0, "rs": 70.0}
    assert setup.is_pre_breakout(99.0, bo, mom) is True
    # Failed breakout falling back below the pivot (BMP 04-06/07) → NOT a clean coil
    assert setup.is_pre_breakout(99.0, dict(bo, recent_above=2), mom) is False
    out = setup.score_setup(99.0, bo, mom)
    assert out["setup"] > 40 and out["dist_below_pivot"] == 1.0

    # Already broken out → not pre-breakout
    bo2 = dict(bo, breakout_ratio=1.02)
    assert setup.is_pre_breakout(102.0, bo2, mom) is False
    # Too far below pivot → not imminent
    assert setup.is_pre_breakout(95.0, {**bo, "pivot": 100.0}, mom) is False


def test_rating_labels():
    assert scoring.rating(90) == "Rất mạnh"
    assert scoring.rating(70) == "Khá"
    assert scoring.rating(30) == "Yếu"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
