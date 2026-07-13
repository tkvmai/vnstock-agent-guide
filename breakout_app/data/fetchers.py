"""Fetch market data from vnstock_data with concurrency and unit normalisation.

Unit notes (verified against the live API):
- Market().ohlcv() returns prices in THOUSANDS of VND (VCB close = 61.6).
- price_board / screener.filter return FULL VND (VCB = 61,600).
We normalise OHLCV to full VND so prices, turnover (GTGD), and thresholds are all
in the same VND scale as the spec ("5,000 VND", "20 tỷ VND").
"""

import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import pandas as pd

import config

# vnai (telemetry) spawns git subprocesses without redirecting stdin; under a
# detached/headless launch that can hang. Force DEVNULL stdin defensively.
_orig_popen_init = subprocess.Popen.__init__
def _popen_no_stdin(self, *a, **k):
    if k.get("stdin") is None:
        k["stdin"] = subprocess.DEVNULL
    _orig_popen_init(self, *a, **k)
subprocess.Popen.__init__ = _popen_no_stdin


# ── Universe ─────────────────────────────────────────────────────────────────────
# Whole-market liquid universe via the server-side screener: every HOSE/HNX stock
# whose 30-day average turnover clears a floor, sorted by liquidity. This scans
# the entire market (not just index members); the Layer-1 GTGD20 filter then
# applies the exact per-stock threshold. screener.filter() needs explicit
# `filters=` — without it the API returns an arbitrary tiny subset.
_EXCHANGE_CODE = {"HOSE": "hsx", "HSX": "hsx", "HNX": "hnx"}


def _insights():
    from vnstock_data import Insights
    return Insights()


def _listing():
    try:
        from vnstock_data import Listing
        return Listing(source="VCI")
    except Exception:
        from vnstock import Listing
        return Listing(source="vci")


def fetch_universe(exchanges=None, min_gtgd20: float = None) -> pd.DataFrame:
    """Whole-market liquid universe across the selected exchanges.

    The size is self-adjusting: it is EVERY stock whose 30-day average turnover
    clears the pre-filter floor (≈70% of ``min_gtgd20``, a buffer below the exact
    Layer-1 GTGD20 threshold so no borderline name is dropped). The only cap is
    ``config.MAX_UNIVERSE``, a safety ceiling that is not expected to bind.
    Returns columns: symbol, exchange, avg_value_30d (sorted by liquidity desc).
    """
    selected = [e.upper() for e in (exchanges or config.DEFAULT_EXCHANGES)]
    floor = (min_gtgd20 if min_gtgd20 is not None else config.MIN_GTGD20) * 0.7
    ins = _insights()
    frames = []
    for exch in selected:
        code = _EXCHANGE_CODE.get(exch)
        if not code:
            continue
        filters = [
            {"name": "exchange", "conditionOptions": [{"type": "value", "value": code}]},
            {"name": "adtv", "extraName": "30Days", "conditionOptions": [{"from": floor, "to": 1e15}]},
        ]
        try:
            df = ins.screener.filter(filters=filters, limit=2000)
            if df is not None and not df.empty:
                frames.append(df[["symbol", "exchange", "avg_value_30d"]])
        except Exception:
            continue

    if not frames:
        return _fallback_universe(selected)

    out = pd.concat(frames, ignore_index=True).drop_duplicates("symbol")
    out["avg_value_30d"] = pd.to_numeric(out["avg_value_30d"], errors="coerce")
    out = out.sort_values("avg_value_30d", ascending=False).head(config.MAX_UNIVERSE)
    return out.reset_index(drop=True)


def _fallback_universe(selected) -> pd.DataFrame:
    """Fallback: full stock list by exchange (no liquidity pre-filter)."""
    want = {e.upper() for e in selected} | {"HSX" if e.upper() == "HOSE" else e.upper() for e in selected}
    try:
        df = _listing().symbols_by_exchange()
    except Exception:
        return pd.DataFrame(columns=["symbol", "exchange", "avg_value_30d"])
    df = df[(df.get("type") == "STOCK") & (df["exchange"].str.upper().isin(want))].copy()
    df["exchange"] = df["exchange"].str.upper().replace({"HSX": "HOSE"})
    df["avg_value_30d"] = float("nan")
    return df[["symbol", "exchange", "avg_value_30d"]].head(config.MAX_UNIVERSE).reset_index(drop=True)


# ── OHLCV ──────────────────────────────────────────────────────────────────────────
def fetch_ohlcv(symbol: str, days: int = config.FETCH_DAYS, scale_prices: bool = True) -> pd.DataFrame:
    """Daily OHLCV for one symbol, normalised to full VND.

    scale_prices=False for indices (VNINDEX) where points are not in thousands.
    """
    from vnstock_data import Market
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=days)).isoformat()
    df = Market().equity(symbol).ohlcv(start=start, end=end, interval="1D")
    if df is None or df.empty:
        return pd.DataFrame()
    df = df[["time", "open", "high", "low", "close", "volume"]].copy()
    # Auto-scale thousands -> full VND (guard against double scaling).
    if scale_prices and df["close"].median() < 1000:
        for c in ("open", "high", "low", "close"):
            df[c] = df[c] * 1000
    return df.reset_index(drop=True)


def fetch_ohlcv_batch(symbols, days: int = config.FETCH_DAYS, workers: int = 6) -> dict:
    """Fetch OHLCV for many symbols concurrently. Returns {symbol: DataFrame}.

    workers kept ≤6: at Golden tier (500 req/min ≈ 8/s) higher concurrency risks
    rate-limiting / IP block on a whole-market fetch (per pipeline best-practices).
    """
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_ohlcv, s, days): s for s in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                df = fut.result()
                if df is not None and not df.empty:
                    out[sym] = df
            except Exception:
                pass  # skip failed symbols; layer1 will reject those with no data
    return out


def fetch_vnindex(days: int = config.FETCH_DAYS) -> pd.DataFrame:
    """VN-Index OHLCV (points, not scaled)."""
    return fetch_ohlcv("VNINDEX", days=days, scale_prices=False)


# ── Live snapshot (price board) ─────────────────────────────────────────────────
def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_price_board(symbols, chunk: int = 50) -> dict:
    """Live snapshot per symbol. Returns {symbol: {close, high, low, ceiling,
    floor, volume, exchange, foreign_buy_volume, foreign_sell_volume}}.
    """
    from vnstock_data import Trading
    symbols = list(dict.fromkeys(symbols))
    out = {}
    for i in range(0, len(symbols), chunk):
        batch = symbols[i:i + chunk]
        try:
            df = Trading(symbol=batch[0], source="KBS").price_board(symbols_list=batch)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for _, r in df.iterrows():
            close = _to_float(r.get("close_price")) or _to_float(r.get("reference_price"))
            out[r["symbol"]] = {
                "close": close,
                "high": _to_float(r.get("high_price")) or close,
                "low": _to_float(r.get("low_price")) or close,
                "ceiling": _to_float(r.get("ceiling_price")),
                "floor": _to_float(r.get("floor_price")),
                "volume": _to_float(r.get("volume_accumulated")) or 0.0,
                "exchange": r.get("exchange"),
                "foreign_buy_volume": _to_float(r.get("foreign_buy_volume")) or 0.0,
                "foreign_sell_volume": _to_float(r.get("foreign_sell_volume")) or 0.0,
            }
    return out


# ── Money flow (per-stock, exact 5-session net per Spec 3.2.4.2) ──────────────────
def _net_5d_excl_today(df: pd.DataFrame, date_col: str, value_col: str) -> float:
    """Sum the net value over the 5 most recent sessions, excluding today."""
    if df is None or df.empty or value_col not in df.columns:
        return None
    d = df.copy()
    d[date_col] = pd.to_datetime(d[date_col]).dt.date
    today = date.today()
    d = d[d[date_col] < today].sort_values(date_col).tail(config.LOOKBACK_FLOW)
    if d.empty:
        return None
    return float(pd.to_numeric(d[value_col], errors="coerce").fillna(0).sum())


def _fetch_flow_one(symbol: str, days: int = 20) -> dict:
    """Exact 5-session net foreign + proprietary value for one symbol (VND)."""
    from vnstock_data import Trading
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=days)).isoformat()
    out = {}
    try:
        f = Trading(symbol=symbol, source="VCI").foreign_trade(start=start, end=end)
        out["foreign_net_5d"] = _net_5d_excl_today(f, "trading_date", "fr_net_value_total")
    except Exception:
        out["foreign_net_5d"] = None
    try:
        p = Trading(symbol=symbol, source="VCI").prop_trade(start=start, end=end)
        out["prop_net_5d"] = _net_5d_excl_today(p, "trading_date", "total_trade_net_value")
    except Exception:
        out["prop_net_5d"] = None
    return out


def fetch_flow_per_stock(symbols, workers: int = 5) -> dict:
    """Exact 5-session foreign/proprietary net per stock (Spec 3.2.4.2).

    Returns {symbol: {foreign_net_5d, prop_net_5d}} in full VND. This is EOD data
    that the spec defines as excluding today, so it is cached once per day.
    """
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_flow_one, s): s for s in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                out[sym] = fut.result()
            except Exception:
                out[sym] = {"foreign_net_5d": None, "prop_net_5d": None}
    return out
