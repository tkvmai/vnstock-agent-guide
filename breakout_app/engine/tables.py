"""Scoring lookup tables from Stock Trading Spec RevC.

Every band table is encoded as an ascending list of ``(upper_exclusive, score)``
pairs plus a ``default`` for values at/above the last threshold. ``piecewise``
returns the score of the first band whose upper bound the value falls under.
This single representation handles both "higher is better" and "lower is better"
tables, including the non-monotonic ones (e.g. RSI, return_5d) where a too-high
value scores lower again.
"""

from typing import List, Tuple


def piecewise(value: float, bands: List[Tuple[float, float]], default: float) -> float:
    """Map ``value`` to a score using ascending (upper_exclusive, score) bands."""
    if value is None:
        return default
    for upper, score in bands:
        if value < upper:
            return score
    return default


# ── Liquidity (Spec 3.1) ────────────────────────────────────────────────────────
# safety_ratio = GTGD20 / position_size  (Spec 3.1.1)
GTGD20_BANDS = [(10, 0), (20, 20), (50, 40), (100, 60), (200, 80)]
GTGD20_DEFAULT = 100

# intraday_ratio as a percentage (Spec 3.1.2)
INTRADAY_BANDS = [(30, 0), (60, 20), (100, 40), (150, 60), (200, 80)]
INTRADAY_DEFAULT = 100

# CV as a percentage — lower is better (Spec 3.1.3)
CV_BANDS = [(30, 100), (50, 80), (75, 60), (100, 40), (150, 20)]
CV_DEFAULT = 0

# ── Momentum composite (Spec 3.2.1) ──────────────────────────────────────────────
RETURN_1D_BANDS = [(-1, 0), (0, 20), (1, 50), (3, 75)]
RETURN_1D_DEFAULT = 90

RETURN_5D_BANDS = [(-3, 0), (0, 15), (2, 40), (5, 70), (10, 90), (15, 100)]
RETURN_5D_DEFAULT = 65  # > 15% = extended weekly, soft cap

RETURN_20D_BANDS = [(-5, 0), (0, 20), (5, 50), (15, 80), (25, 100)]
RETURN_20D_DEFAULT = 75  # > 25% = soft cap

# ── MA structure (Spec 3.2.2) ─────────────────────────────────────────────────────
PRICE_VS_MA20_BANDS = [(-2, 0), (0, 15), (1.5, 75), (3.5, 90), (6, 65), (9, 30)]
PRICE_VS_MA20_DEFAULT = 0  # > 9% = too extended

PRICE_VS_MA50_BANDS = [(-2, 0), (0, 15), (3, 50), (8, 80), (15, 100)]
PRICE_VS_MA50_DEFAULT = 70

ALIGNMENT_BANDS = [(-3, 0), (-1, 20), (0, 40), (1, 65), (3, 85)]
ALIGNMENT_DEFAULT = 100  # MA20 > MA50 by > 3% = clear Stage 2

SLOPE_MA20_BANDS = [(-0.3, 0), (0, 15), (0.3, 40), (0.6, 70)]
SLOPE_MA20_DEFAULT = 100

SLOPE_MA50_BANDS = [(-0.2, 0), (0, 20), (0.2, 50), (0.4, 80)]
SLOPE_MA50_DEFAULT = 100

# ── Relative strength (Spec 3.2.3) ────────────────────────────────────────────────
RS_WEIGHTED_BANDS = [(-5, 0), (0, 20), (3, 45), (8, 65), (15, 85)]
RS_WEIGHTED_DEFAULT = 100  # > 15% = superleader


def rs_accel_mult(rs_1m: float, rs_3m: float) -> float:
    """RS acceleration multiplier (Spec 3.2.3 table 3)."""
    accel = rs_1m - rs_3m
    if accel > 5:
        return 1.10
    if accel >= 0:
        return 1.00
    if accel >= -5:
        return 0.90
    return 0.80


# ── Money flow (Spec 3.2.4) ───────────────────────────────────────────────────────
AD_RATIO_BANDS = [(0.7, 20), (1.0, 40), (1.5, 60), (2.0, 80)]
AD_RATIO_DEFAULT = 100  # >= 2.0 strong accumulation

FOREIGN_NET_BANDS = [(-2, 0), (-0.5, 20), (0.5, 40), (2, 60), (5, 80)]
FOREIGN_NET_DEFAULT = 100

PROP_NET_BANDS = [(-1, 0), (-0.3, 20), (0.3, 40), (1, 60), (3, 80)]
PROP_NET_DEFAULT = 100


def _flow_bucket(score: float) -> str:
    """HIGH >= 70, MID 40–69, LOW <= 39 (Spec 3.2.4.3)."""
    if score >= 70:
        return "HIGH"
    if score >= 40:
        return "MID"
    return "LOW"


# (ad_bucket, smf_bucket) -> convergence multiplier (Spec 3.2.4.3 table)
CONVERGENCE_MULT = {
    ("HIGH", "HIGH"): 1.20,
    ("MID", "HIGH"): 1.10,
    ("HIGH", "MID"): 1.05,
    ("MID", "MID"): 1.00,
    ("LOW", "HIGH"): 0.90,
    ("HIGH", "LOW"): 0.85,
    ("MID", "LOW"): 0.90,
    ("LOW", "MID"): 0.92,
    ("LOW", "LOW"): 0.70,
}


def convergence_mult(ad_score: float, smf_score: float) -> float:
    return CONVERGENCE_MULT[(_flow_bucket(ad_score), _flow_bucket(smf_score))]


# ── Technical confirmation (Spec 3.2.5) ───────────────────────────────────────────
RSI_BANDS = [(40, 0), (45, 10), (50, 35), (60, 60), (70, 100), (80, 60)]
RSI_DEFAULT = 20  # > 80 overbought

MACD_HIST_BANDS = [(-0.10, 0), (0, 20), (0.05, 50), (0.20, 75)]
MACD_HIST_DEFAULT = 100  # > 0.20% strong positive

# ── Breakout (RevD 2.2a) ──────────────────────────────────────────────────────────
# RevD price_fresh — REWARDS freshness, DECAYS with extension (opposite of RevC,
# which rewarded extension). Applies only when breakout_ratio >= 1.0.
PRICE_FRESH_BANDS = [(1.02, 100), (1.04, 80), (1.07, 50)]
PRICE_FRESH_DEFAULT = 20  # >= 1.07 = chasing, avoid

# Legacy RevC band (extension-rewarding) — kept for reference, no longer used.
BREAKOUT_RATIO_BANDS = [(1.01, 40), (1.02, 70)]
BREAKOUT_RATIO_DEFAULT = 100


def age_factor(age: float) -> float:
    """breakout_age multiplier on the Trigger score (RevD 2.2a) — demotes stale
    breakouts. age 0 (fresh cross) → 1.0; older → progressively lower."""
    if age is None or age <= 0:
        return 1.00
    if age == 1:
        return 0.90
    if age == 2:
        return 0.60
    return 0.30


# ── Overheat / timing multiplier (RevD 2.5) ───────────────────────────────────────
def overheat_rsi_mult(rsi: float) -> float:
    """RSI(14) overbought penalty — makes overbought actually cost points."""
    if rsi is None or rsi != rsi:  # None or NaN
        return 1.00
    if rsi > 80:
        return 0.55
    if rsi > 75:
        return 0.75
    if rsi > 70:
        return 0.90
    return 1.00


def overheat_ext_mult(price_vs_ma20: float) -> float:
    """Extension-above-MA20 penalty (how far above the short-term mean)."""
    if price_vs_ma20 is None or price_vs_ma20 != price_vs_ma20:
        return 1.00
    if price_vs_ma20 > 13:
        return 0.45
    if price_vs_ma20 > 9:
        return 0.65
    if price_vs_ma20 > 6:
        return 0.85
    return 1.00


def overheat_mult(rsi: float, price_vs_ma20: float) -> float:
    return overheat_rsi_mult(rsi) * overheat_ext_mult(price_vs_ma20)


def overhead_mult(dist_to_high: float) -> float:
    """Overhead-supply penalty (loss-review P10, 11/07/2026): a 20-session breakout
    deep below the LONG-term high (~4-month, full fetched history) runs into trapped
    sellers. Validated on 143 signals: <-20% → −2.11% avg T+3, −20..−10% → win 20%,
    near-high → −0.34% / win 39% (corr +0.24). dist_to_high = close/max(hist) − 1, %."""
    if dist_to_high is None or dist_to_high != dist_to_high:
        return 1.00
    if dist_to_high < -10:
        return 0.70
    if dist_to_high < -5:
        return 0.90
    return 1.00


# ── Pre-breakout proximity (RevD 2.2b) ────────────────────────────────────────────
# dist_below_pivot as a percentage — closer below the pivot = more imminent.
PROXIMITY_BANDS = [(1, 100), (2, 80), (3, 55)]
PROXIMITY_DEFAULT = 0  # >= 3% below pivot = not imminent (also excluded by state)

VOLUME_RATIO_BANDS = [(1.0, 0), (1.3, 50), (1.8, 80)]
VOLUME_RATIO_DEFAULT = 100

# dry-up and base-quality (narrowing) share the same lower-is-better band
DRYUP_BANDS = [(0.5, 100), (0.7, 80), (0.9, 60), (1.1, 40)]
DRYUP_DEFAULT = 20

BASE_QUALITY_BANDS = [(0.5, 100), (0.7, 80), (0.9, 60), (1.1, 40)]
BASE_QUALITY_DEFAULT = 20

CLOSING_STRENGTH_BANDS = [(20, 20), (40, 40), (60, 60), (80, 80)]
CLOSING_STRENGTH_DEFAULT = 100


def risk_ratio_mult(risk_ratio: float) -> float:
    """T+2.5 lock-in risk multiplier on the breakout score (Spec 3.3.6)."""
    if risk_ratio < 3:
        return 1.00
    if risk_ratio < 5:
        return 0.85
    if risk_ratio < 7:
        return 0.70
    return 0.50
