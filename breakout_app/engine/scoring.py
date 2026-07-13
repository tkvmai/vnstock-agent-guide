"""BUY Score orchestration — Spec RevD Layer 2 (timing-aware).

    Signal   = Trigger (confirmed breakout)  OR  Setup (pre-breakout), by state
    BUY_raw  = 0.35·Liquidity + 0.25·Momentum + 0.40·Signal
    BUY      = BUY_raw × overheat_mult × state_mult

The state machine (RevD 2.1) decides which signal applies and how hard to weight
it, so fresh/pre-breakout names rank above late ones and NONE is excluded. Layer-1
filtering and the market regime gate are applied by the caller (scheduler).
"""

import pandas as pd

import config
from . import layer1, liquidity, momentum, breakout, setup
from . import tables as T


def _determine_state(bo: dict, setup_result: dict, close_today: float, mom: dict) -> str:
    ratio = bo.get("breakout_ratio", 0.0)
    age = bo.get("breakout_age", -1)
    if ratio >= 1.0:
        if age <= config.FRESH_MAX_AGE and ratio <= config.FRESH_MAX_RATIO:
            # Thrust gate (loss-review P1): a flat/red pivot touch with a weak close
            # is NOT a breakout — require real buying evidence on the day.
            has_thrust = ((mom.get("return_1d") or 0.0) > config.FRESH_MIN_RETURN_1D
                          and (bo.get("closing_strength") or 0.0) >= config.FRESH_MIN_CLOSING)
            return "BREAKOUT_FRESH" if has_thrust else "NONE"
        return "BREAKOUT_LATE"
    if setup.is_pre_breakout(close_today, bo, mom):
        return "PRE_BREAKOUT"
    return "NONE"


def score_stock(symbol: str, hist: pd.DataFrame, live: dict,
                vnindex_hist: pd.DataFrame, flow: dict = None,
                position_size: int = config.DEFAULT_POSITION_SIZE,
                metrics: dict = None) -> dict:
    """Score a single stock that has already passed Layer-1 hard filters."""
    m = metrics or layer1.compute_layer1_metrics(hist, live)
    close_today = float(live["close"])

    liq = liquidity.score_liquidity(m, position_size)
    mom = momentum.score_momentum(hist, live, vnindex_hist, flow)
    bo = breakout.score_breakout(hist, live, m.get("time_ratio", 1.0))
    setup_result = setup.score_setup(close_today, bo, mom)

    state = _determine_state(bo, setup_result, close_today, mom)
    if state == "PRE_BREAKOUT":
        signal = setup_result["setup"]
    elif state in ("BREAKOUT_FRESH", "BREAKOUT_LATE"):
        signal = bo["breakout"]          # Trigger score
    else:                                # NONE
        signal = 0.0

    overheat = T.overheat_mult(mom.get("rsi"), mom.get("price_vs_ma20"))
    overhead = T.overhead_mult(bo.get("dist_to_high"))
    state_mult = config.STATE_MULT[state]

    w = config.get_w_buy()          # learned weights if enabled, else default
    buy_raw = (w["liquidity"] * liq["liquidity"] + w["momentum"] * mom["momentum"]
               + w["signal"] * signal)
    buy = buy_raw * overheat * overhead * state_mult

    result = {
        "symbol": symbol,
        "buy_score": round(buy, 2),
        "state": state,
        "state_label": config.STATE_LABELS[state],
        "signal": round(signal, 2),
        "setup_score": setup_result["setup"],
        "breakout_age": bo.get("breakout_age"),
        "overheat_mult": round(overheat, 3),
        "overhead_mult": overhead,
        "dist_to_high": bo.get("dist_to_high"),
        "state_mult": state_mult,
        "liquidity": liq["liquidity"],
        "momentum": mom["momentum"],
        "breakout": bo["breakout"],
        "close": close_today,
    }
    # Flatten sub-scores for drill-down / snapshots
    result.update({f"liq_{k}": v for k, v in liq.items() if k != "liquidity"})
    result.update({f"mom_{k}": v for k, v in mom.items() if k != "momentum"})
    result.update({f"bo_{k}": v for k, v in bo.items() if k != "breakout"})
    result.update({f"setup_{k}": v for k, v in setup_result.items() if k != "setup"})
    return result


def rating(buy_score: float) -> str:
    """Map a BUY score to its spec rating label."""
    for threshold, label, _color in config.SCORE_BANDS:
        if buy_score >= threshold:
            return label
    return "Yếu"
