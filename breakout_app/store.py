"""Thread-safe in-memory store holding the latest scan result for the dashboard."""

import threading

import pandas as pd

import config

_lock = threading.RLock()
_state = {
    "ranked": pd.DataFrame(),       # Layer-2 ranked frame (passed + scored)
    "layer1": pd.DataFrame(),       # all universe stocks with pass/fail + reason
    "regime": "ok",
    "regime_ratio": None,
    "regime_msg": "",
    "market_health": None,          # observe-only fragility score (engine/market_health)
    "ftd": None,                    # observe-only Follow-Through Day state (engine/ftd)
    "smart_money_live": None,       # NN live mỗi scan {ts, minutes, rows} (tab 💰)
    "last_scan": None,              # datetime of last completed scan
    "status": "idle",              # idle | scanning | error
    "error": None,
    "universe_total": 0,
    "universe_passed": 0,
    "settings": {
        "position_size": config.DEFAULT_POSITION_SIZE,
        "min_score": config.DEFAULT_MIN_SCORE,
        "min_price": config.MIN_PRICE,
        "min_gtgd20": config.MIN_GTGD20,
        "exchanges": list(config.DEFAULT_EXCHANGES),
    },
}


def get() -> dict:
    with _lock:
        snap = dict(_state)
        snap["ranked"] = _state["ranked"].copy()
        snap["layer1"] = _state["layer1"].copy()
        snap["settings"] = dict(_state["settings"])
        return snap


# Listeners notified after every state change so background (scheduler) scans can
# push a UI refresh to connected dashboard sessions.
_listeners = []


def add_listener(fn):
    if fn not in _listeners:
        _listeners.append(fn)


def _notify():
    for fn in list(_listeners):
        try:
            fn()
        except Exception:
            pass


def update(**kwargs):
    with _lock:
        _state.update(kwargs)
    _notify()


def update_settings(**kwargs):
    with _lock:
        _state["settings"].update({k: v for k, v in kwargs.items() if v is not None})
        return dict(_state["settings"])


def get_settings() -> dict:
    with _lock:
        return dict(_state["settings"])
