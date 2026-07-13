"""Parquet cache for the raw daily OHLCV pull, keyed by trading date.

Lets the app skip re-fetching the whole universe's history if it restarts on the
same day. SQLite (db.py) is the long-term store; this is a fast same-day cache.
"""

import os

import pandas as pd

import config


def _path(tag: str) -> str:
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    return os.path.join(config.CACHE_DIR, f"{tag}.parquet")


def save_ohlcv_bundle(date_tag: str, df: pd.DataFrame):
    """Save a long-format OHLCV frame (with a 'symbol' column) for one date."""
    if df is not None and not df.empty:
        df.to_parquet(_path(f"ohlcv_{date_tag}"), index=False)


def load_ohlcv_bundle(date_tag: str) -> pd.DataFrame:
    p = _path(f"ohlcv_{date_tag}")
    if os.path.exists(p):
        return pd.read_parquet(p)
    return pd.DataFrame()


def has_bundle(date_tag: str) -> bool:
    return os.path.exists(_path(f"ohlcv_{date_tag}"))
