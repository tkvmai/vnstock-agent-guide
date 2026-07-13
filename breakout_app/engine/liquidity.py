"""Liquidity Score (Spec 3.1).

Liquidity = 0.55 × score_gtgd20 + 0.30 × score_intraday + 0.15 × score_cv
"""

import config
from . import tables as T


def score_liquidity(metrics: dict, position_size: int) -> dict:
    """Compute liquidity score from Layer-1 metrics (gtgd20, cv, intraday_ratio)."""
    safety_ratio = metrics["gtgd20"] / position_size if position_size else 0.0
    s_gtgd = T.piecewise(safety_ratio, T.GTGD20_BANDS, T.GTGD20_DEFAULT)
    s_intraday = T.piecewise(metrics["intraday_ratio"], T.INTRADAY_BANDS, T.INTRADAY_DEFAULT)
    s_cv = T.piecewise(metrics["cv"], T.CV_BANDS, T.CV_DEFAULT)

    w = config.W_LIQUIDITY
    total = w["gtgd20"] * s_gtgd + w["intraday"] * s_intraday + w["cv"] * s_cv
    return {
        "liquidity": round(total, 2),
        "safety_ratio": round(safety_ratio, 1),
        "gtgd20": metrics.get("gtgd20"),          # raw VND (for detail display)
        "cv": metrics.get("cv"),                  # raw %
        "intraday_ratio": metrics.get("intraday_ratio"),  # raw %
        "gtgd_intraday": metrics.get("gtgd_intraday"),    # raw VND (today so far)
        "volume_intraday": metrics.get("volume_intraday"),  # raw shares (today so far)
        "score_gtgd20": s_gtgd,
        "score_intraday": s_intraday,
        "score_cv": s_cv,
    }
