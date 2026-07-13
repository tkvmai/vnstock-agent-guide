"""Pre-breakout Setup score — Spec RevD 2.2b.

Setup = 0.30·proximity + 0.25·base_quality + 0.20·dry_up + 0.15·structure + 0.10·rs

Answers "is this coiled tight, right under resistance, ready to pop?" — for stocks
that have NOT yet broken out (breakout_ratio < 1.0). Reuses the raw ratios already
computed by ``breakout.score_breakout`` (dry_up_ratio, narrowing_ratio, pivot) and
the structure/RS numbers from ``momentum.score_momentum`` — no recomputation of
indicators, keeping the engine cheap and consistent.
"""

import config
from . import tables as T


def dist_below_pivot(close_today: float, pivot: float) -> float:
    """% the current close sits BELOW the pivot (0 = at pivot, +3 = 3% under)."""
    if not pivot:
        return float("inf")
    return (pivot - close_today) / pivot * 100


def score_setup(close_today: float, bo: dict, mom: dict) -> dict:
    """Setup score from breakout raw metrics (`bo`) + momentum result (`mom`)."""
    dist = dist_below_pivot(close_today, bo.get("pivot", 0.0))

    s_prox = T.piecewise(dist, T.PROXIMITY_BANDS, T.PROXIMITY_DEFAULT)
    s_base = T.piecewise(bo.get("narrowing_ratio"), T.BASE_QUALITY_BANDS, T.BASE_QUALITY_DEFAULT)
    s_dryup = T.piecewise(bo.get("dry_up_ratio"), T.DRYUP_BANDS, T.DRYUP_DEFAULT)

    # Structure = MA20-vs-MA50 alignment + slope (reuse RevC bands on mom's raw values)
    s_align = T.piecewise(mom.get("ma20_vs_ma50"), T.ALIGNMENT_BANDS, T.ALIGNMENT_DEFAULT)
    s_slope20 = T.piecewise(mom.get("slope_ma20"), T.SLOPE_MA20_BANDS, T.SLOPE_MA20_DEFAULT)
    s_slope50 = T.piecewise(mom.get("slope_ma50"), T.SLOPE_MA50_BANDS, T.SLOPE_MA50_DEFAULT)
    s_slope = config.W_SLOPE["ma20"] * s_slope20 + config.W_SLOPE["ma50"] * s_slope50
    ws = config.W_SETUP_STRUCTURE
    s_structure = ws["alignment"] * s_align + ws["slope"] * s_slope

    s_rs = mom.get("rs", 0.0)  # RS score already computed by momentum

    w = config.W_SETUP
    total = (w["proximity"] * s_prox + w["base_quality"] * s_base + w["dry_up"] * s_dryup
             + w["structure"] * s_structure + w["rs"] * s_rs)
    return {
        "setup": round(total, 2),
        "dist_below_pivot": round(dist, 2) if dist != float("inf") else None,
        "score_proximity": s_prox,
        "score_setup_structure": round(s_structure, 1),
    }


def is_pre_breakout(close_today: float, bo: dict, mom: dict) -> bool:
    """PRE_BREAKOUT eligibility (RevD 2.1): coiling just below the pivot with
    dry-up, ATR contraction, Stage-2 structure and non-negative RS."""
    if bo.get("breakout_ratio", 0.0) >= 1.0:
        return False
    # Clean-coil check (loss-review P2): a close above the pivot in the last few
    # sessions means this is a FAILED breakout falling back, not a coiling base.
    if (bo.get("recent_above") or 0) > 0:
        return False
    dist = dist_below_pivot(close_today, bo.get("pivot", 0.0))
    return (
        dist <= config.PRE_BREAKOUT_MAX_DIST
        and (bo.get("dry_up_ratio") or 1.0) < config.PRE_BREAKOUT_DRYUP_MAX
        and (bo.get("narrowing_ratio") or 1.0) < config.PRE_BREAKOUT_NARROWING_MAX
        and (mom.get("ma20_vs_ma50") or 0.0) > 0          # MA20 > MA50 (Stage 2)
        and (mom.get("slope_ma20") or 0.0) > 0            # MA20 rising
        and (mom.get("rs_weighted") or 0.0) >= 0          # not underperforming
    )
