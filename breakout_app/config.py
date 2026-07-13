"""Central configuration for the Breakout Screener (Stock Trading Spec RevC).

All weights, thresholds, and tunable parameters live here so the algorithm can
be adjusted without touching engine logic.
"""

import json
import os

# ── Paths ──────────────────────────────────────────────────────────────────────
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
DB_PATH = os.path.join(DATA_DIR, "screener.db")
TELEGRAM_CONFIG_PATH = os.path.join(DATA_DIR, "telegram_config.json")

# ── Telegram hourly alerts ──────────────────────────────────────────────────────
ALERT_TOP_N = 5             # how many top Layer-2 stocks to push per send
ALERT_MIN_SCORE = 50.0      # only alert stocks at/above this BUY score (0 = no filter)
ALERT_START_HOUR = 10       # first hourly send (Vietnam time)
ALERT_END_HOUR = 15         # last hourly send (inclusive)

# ── User settings (overridable from the dashboard) ──────────────────────────────
DEFAULT_POSITION_SIZE = 50_000_000     # VND — max money per stock (Spec 3.1.1)
DEFAULT_MIN_SCORE = 50.0              # hide stocks below this BUY score in the UI
# The universe is the whole HOSE+HNX market pre-filtered by liquidity (adtv ≥ a
# fraction of min_gtgd20); its size is self-adjusting. This is only a hard safety
# ceiling so a misconfiguration can't trigger a runaway fetch — not a user knob.
MAX_UNIVERSE = 800

# ── Layer 1 — Hard Filter thresholds (Spec section 2) ──────────────────────────
MIN_SESSIONS = 60                     # filter #3: need >= 60 sessions of OHLCV
MIN_PRICE = 5_000                     # filter #4: price >= 5,000 VND
MIN_GTGD20 = 20_000_000_000           # filter #5: GTGD20 >= 20B VND
MIN_INTRADAY_ACTIVE_PCT = 30.0        # filter #6: GTGD_intraday >= 30% of expected
CV_CAP = 200.0                        # filter #8: CV < 200%
ALLOWED_EXCHANGES = ("HSX", "HOSE", "HNX")  # filter #1: HOSE + HNX only
EXCHANGE_OPTIONS = ["HOSE", "HNX"]    # user-selectable exchanges (spec excludes UPCOM)
DEFAULT_EXCHANGES = ["HOSE", "HNX"]

# Ceiling/floor band fallback when price_board lacks the fields.
CEILING_BAND = {"HOSE": 0.07, "HSX": 0.07, "HNX": 0.10, "UPCOM": 0.15}

# ── Trading session timing (Spec 3.1.2) ─────────────────────────────────────────
# 225 real trading minutes = morning 9:15–11:30 (135) + afternoon 13:00–14:45 (105).
TRADING_MINUTES = 225
SESSION_OPEN = (9, 15)                # (hour, minute) Vietnam time, UTC+7
MORNING_CLOSE = (11, 30)
AFTERNOON_OPEN = (13, 0)
SESSION_CLOSE = (14, 45)
VN_UTC_OFFSET = 7                     # Vietnam = UTC+7

# ── Top-level BUY Score weights (RevD Layer 2 — timing-aware) ────────────────────
# Signal = Trigger (confirmed breakout) OR Setup (pre-breakout), chosen by state.
W_BUY = {"liquidity": 0.35, "momentum": 0.25, "signal": 0.40}

# ── RevD state machine (Spec RevD 2.1) ───────────────────────────────────────────
FRESH_MAX_RATIO = 1.04        # breakout_ratio ceiling to still count as FRESH
FRESH_MAX_AGE = 1             # breakout_age ceiling for FRESH (sessions since cross)
# Thrust gate (loss-review 09/07/2026, P1): a "breakout" on a flat/red day with a
# weak close is a pivot TOUCH, not a breakout — 3/4 losers of 03/07 had chg ≤ 0 &
# closing ≤ 71 while all 9 winners averaged +2.9% / 81%. Both must hold for FRESH.
FRESH_MIN_RETURN_1D = 0.0     # reco-day return must be strictly ABOVE this (%)
FRESH_MIN_CLOSING = 40.0      # reco-day closing strength floor (%)
PRE_BREAKOUT_MAX_DIST = 3.0   # % below pivot to qualify as PRE_BREAKOUT
PRE_BREAKOUT_DRYUP_MAX = 0.9  # dry-up must be tightening
PRE_BREAKOUT_NARROWING_MAX = 0.9  # ATR must be contracting
# Clean-coil requirement (loss-review 09/07/2026, P2): a stock that closed above
# the pivot recently and fell back is a FAILED breakout, not a coiling setup (BMP
# scored 78.5 as PRE right after its breakout died). No above-pivot close allowed
# in the last N sessions.
PRE_BREAKOUT_NO_BREAK_SESSIONS = 3

# ── Missed-winner review (false negatives) ─────────────────────────────────────────
# A non-recommended pool stock only counts as a reviewable "miss" when its T+3
# return clears this bar — small drifts are noise, not missed swing opportunities.
MISS_MIN_RET_T3 = 3.0   # %
STATE_MULT = {"BREAKOUT_FRESH": 1.00, "PRE_BREAKOUT": 0.95,
              "BREAKOUT_LATE": 0.60, "NONE": 0.0}
STATE_LABELS = {"BREAKOUT_FRESH": "🟢 Mua ngay", "PRE_BREAKOUT": "🔵 Sắp breakout",
                "BREAKOUT_LATE": "🟠 Muộn", "NONE": "—"}

# Setup (pre-breakout) sub-weights (RevD 2.2b)
W_SETUP = {"proximity": 0.30, "base_quality": 0.25, "dry_up": 0.20,
           "structure": 0.15, "rs": 0.10}
W_SETUP_STRUCTURE = {"alignment": 0.5, "slope": 0.5}

# ── Learned W_BUY weights (auto-tuned from T+3 outcomes) ──────────────────────────
# The learner (analysis/learn_weights.py) nudges the DEFAULT W_BUY toward whatever
# actually predicts T+3 wins, bounded within ±LEARN_MAX_DELTA of the default and
# written to data/learned_weights.json (reversible — delete the file or set the
# toggle False to revert). config.py itself is never rewritten. Use get_w_buy() in
# the engine so learned weights take effect; reload_learned_weights() refreshes it.
USE_LEARNED_WEIGHTS = True
LEARNED_WEIGHTS_PATH = os.path.join(DATA_DIR, "learned_weights.json")
LEARN_MIN_SAMPLE = 20        # need this many resolved (T+3) signals before adjusting
LEARN_MAX_DELTA = 0.10       # a learned weight can deviate at most this far from default
LEARN_ALPHA_CAP = 0.5        # max learning rate (trust in data)
LEARN_ALPHA_SCALE = 200      # alpha = min(cap, n_samples / scale)

_W_BUY_EFFECTIVE = dict(W_BUY)


def get_w_buy() -> dict:
    """The W_BUY the engine should use (learned if valid+enabled, else default)."""
    return dict(_W_BUY_EFFECTIVE)


def reload_learned_weights() -> dict:
    """Refresh the effective W_BUY from data/learned_weights.json (call after the
    learner writes it, or at the start of a scan). Falls back to default on any issue."""
    global _W_BUY_EFFECTIVE
    if USE_LEARNED_WEIGHTS:
        try:
            with open(LEARNED_WEIGHTS_PATH, encoding="utf-8") as f:
                w = (json.load(f) or {}).get("W_BUY") or {}
            if set(w) >= set(W_BUY) and abs(sum(float(w[k]) for k in W_BUY) - 1) < 1e-2:
                _W_BUY_EFFECTIVE = {k: float(w[k]) for k in W_BUY}
                return dict(_W_BUY_EFFECTIVE)
        except Exception:
            pass
    _W_BUY_EFFECTIVE = dict(W_BUY)
    return dict(_W_BUY_EFFECTIVE)


reload_learned_weights()   # load once at import

# ── Liquidity sub-weights (Spec 3.1) ────────────────────────────────────────────
W_LIQUIDITY = {"gtgd20": 0.55, "intraday": 0.30, "cv": 0.15}

# ── Momentum sub-weights (Spec 3.2) ─────────────────────────────────────────────
# The spec listed these summing to 1.05; per user decision flow is lowered from
# 0.25 to 0.20 so the weights sum to exactly 1.00. (momentum.py still normalises
# by the sum, which is now a no-op.)
W_MOMENTUM = {
    "composite": 0.30,
    "ma": 0.20,
    "rs": 0.20,
    "flow": 0.20,
    "technical": 0.10,
}

# Multi-timeframe composite (Spec 3.2.1)
W_COMPOSITE = {"r1d": 0.15, "r5d": 0.50, "r20d": 0.35}
CONSISTENCY_MULT = {3: 1.10, 2: 1.00, 1: 0.85, 0: 0.70}

# MA structure (Spec 3.2.2)
W_MA = {"price_vs_ma20": 0.35, "price_vs_ma50": 0.20, "alignment": 0.20, "slope": 0.25}
W_SLOPE = {"ma20": 0.55, "ma50": 0.45}

# Relative strength (Spec 3.2.3)
W_RS = {"rs_3m": 0.35, "rs_1m": 0.65}

# Money flow (Spec 3.2.4)
W_FLOW = {"ad": 0.40, "smf": 0.60}
W_SMF = {"foreign": 0.60, "prop": 0.40}
NEUTRAL_FLOW_SCORE = 40.0             # default when per-stock flow data is missing

# Technical confirmation (Spec 3.2.5)
W_TECHNICAL = {"rsi": 0.60, "macd": 0.40}

# ── Trigger (confirmed breakout) sub-weights (RevD 2.2a) ─────────────────────────
# RevD reweights RevC's breakout: price freshness up (0.30→0.35), base down
# (0.15→0.10). The extension-rewarding price band and risk_ratio penalty are
# replaced by freshness bands + breakout_age factor (see engine/tables.py).
W_TRIGGER = {
    "price": 0.35,
    "volume": 0.25,
    "dry_up": 0.20,
    "base_quality": 0.10,
    "closing": 0.10,
}

# ── Lookback windows (sessions) ─────────────────────────────────────────────────
LOOKBACK_GTGD = 20
LOOKBACK_MA20 = 20
LOOKBACK_MA50 = 50
LOOKBACK_SLOPE = 10
LOOKBACK_RS_1M = 21
LOOKBACK_RS_3M = 63
LOOKBACK_AD = 20
LOOKBACK_FLOW = 5
LOOKBACK_CLOSE20 = 20
LOOKBACK_DRYUP = 4                    # 4 sessions T-5..T-1
LOOKBACK_ATR_SHORT = 5
LOOKBACK_ATR_LONG = 20
FETCH_DAYS = 100                      # calendar days of history to fetch (>= 65 sessions)

# ── Score band guide for the UI ─────────────────────────────────────────────────
SCORE_BANDS = [
    (85, "Rất mạnh", "#1b5e20"),
    (75, "Mạnh", "#388e3c"),
    (65, "Khá", "#f9a825"),
    (50, "Trung bình", "#ef6c00"),
    (0, "Yếu", "#9e9e9e"),
]
