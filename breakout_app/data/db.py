"""SQLite persistence: accumulating OHLCV history, intraday score snapshots, app state.

The DB is the durable source. On startup the app can reload today's OHLCV from
here instead of re-fetching, and `scan_snapshots` accumulates the intraday
evolution of each stock's BUY score for later review/backtesting.
"""

import json
import os
import sqlite3
from contextlib import contextmanager

import pandas as pd

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ohlcv_daily (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS scan_snapshots (
    ts         TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    buy_score  REAL,
    liquidity  REAL,
    momentum   REAL,
    breakout   REAL,
    regime     TEXT,
    PRIMARY KEY (ts, symbol)
);
CREATE INDEX IF NOT EXISTS idx_snap_symbol ON scan_snapshots(symbol, ts);
CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS sent_alerts (
    date      TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    ts        TEXT,
    buy_score REAL,
    PRIMARY KEY (date, symbol)
);
-- Phase 3: forward-tracking / validation ------------------------------------------
CREATE TABLE IF NOT EXISTS tracked_signals (
    symbol         TEXT NOT NULL,
    reco_date      TEXT NOT NULL,      -- day the stock first entered the reco table
    reco_ts        TEXT,               -- timestamp of that first crossing
    reco_close     REAL,              -- price when first recommended (entry reference)
    buy_score      REAL,
    state          TEXT,
    breakout_ratio REAL,
    breakout_age   INTEGER,
    rsi            REAL,
    liquidity      REAL,
    momentum       REAL,
    signal         REAL,
    PRIMARY KEY (symbol, reco_date)
);
-- Lịch cổ tức TIỀN MẶT (VCI không hồi tố tiền mặt vào giá — xem FIX-cash-dividend-returns.md).
-- Dùng để cộng lại giá trị cổ tức vào ret_t* khi lệnh đi qua ngày GDKHQ.
CREATE TABLE IF NOT EXISTS cash_dividends (
    symbol          TEXT NOT NULL,
    exright_date    TEXT NOT NULL,      -- YYYY-MM-DD (ngày GDKHQ)
    value_per_share REAL NOT NULL,      -- ĐỒNG/cp (cùng đơn vị ohlcv_daily)
    PRIMARY KEY (symbol, exright_date)
);
CREATE TABLE IF NOT EXISTS signal_outcomes (
    symbol     TEXT NOT NULL,
    reco_date  TEXT NOT NULL,
    close_t1 REAL, close_t2 REAL, close_t3 REAL, close_t4 REAL, close_t5 REAL,
    ret_t1 REAL, ret_t2 REAL, ret_t3 REAL, ret_t4 REAL, ret_t5 REAL,
    mfe REAL, mae REAL,               -- max favourable / adverse excursion over T+1..T+5
    win_t3     INTEGER,               -- 1 if ret_t3 > 0 (first sellable ~T+2.5), else 0; NULL until 3 sessions elapse
    n_forward  INTEGER,               -- how many forward sessions are available so far
    updated_ts TEXT,
    PRIMARY KEY (symbol, reco_date)
);
-- Phase 4: human-in-the-loop feedback ---------------------------------------------
CREATE TABLE IF NOT EXISTS feedback (
    symbol     TEXT NOT NULL,
    reco_date  TEXT NOT NULL,
    verdict    TEXT,                  -- good | bad | took | skipped | false_breakout | couldnt_buy
    note       TEXT,
    user_entry REAL,
    user_exit  REAL,
    ts         TEXT,
    PRIMARY KEY (symbol, reco_date)
);
-- Loss-review registry: which losing signals were already post-mortemed (so a
-- "review các mã Thua" session only picks up UNREVIEWED cases).
CREATE TABLE IF NOT EXISTS loss_reviews (
    symbol      TEXT NOT NULL,
    reco_date   TEXT NOT NULL,
    cause       TEXT,               -- classification from analysis/loss_reviews.md
    reviewed_ts TEXT,
    PRIMARY KEY (symbol, reco_date)
);
-- Missed-winner review registry (false negatives: not recommended but won big).
CREATE TABLE IF NOT EXISTS miss_reviews (
    symbol      TEXT NOT NULL,
    obs_date    TEXT NOT NULL,
    cause       TEXT,
    reviewed_ts TEXT,
    PRIMARY KEY (symbol, obs_date)
);
-- Market Health (observe-only phase 1): daily fragility score history
CREATE TABLE IF NOT EXISTS market_health (
    date        TEXT PRIMARY KEY,
    health      REAL,
    dist_days   INTEGER,
    breadth_pct REAL,
    canary_pct  REAL,
    index_ratio REAL,
    updated_ts  TEXT
);
-- Phase 4b: whole Layer-1 pool daily snapshot (unbiased learning + reco-quality eval)
CREATE TABLE IF NOT EXISTS daily_observations (
    obs_date   TEXT NOT NULL,         -- EOD snapshot date
    symbol     TEXT NOT NULL,
    state      TEXT,
    buy_score  REAL,
    liquidity  REAL,
    momentum   REAL,
    signal     REAL,
    close_ref  REAL,                  -- EOD close = uniform entry reference for ALL pool stocks
    is_reco    INTEGER,               -- 1 if the app actually recommended it (actionable ≥ threshold)
    ret_t3     REAL,
    win_t3     INTEGER,
    ret_t5     REAL,                  -- swing-window view (spec: vài phiên đến 1-2 tuần)
    win_t5     INTEGER,
    n_forward  INTEGER,
    updated_ts TEXT,
    -- Hướng B (19/07): metric mà BACKTEST MÙ — tích lũy live để calibrate band
    -- intraday/flow sau ~6-12 tháng (xem DEVELOPMENT #40)
    intraday_ratio  REAL,
    foreign_net_pct REAL,
    prop_net_pct    REAL,
    PRIMARY KEY (obs_date, symbol)
);
"""


def init_db():
    """Create the data dir and tables if they don't exist (+ light migrations)."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with _conn() as c:
        c.executescript(_SCHEMA)
        # Migration: T+5 columns on daily_observations (added 09/07/2026; CREATE IF
        # NOT EXISTS doesn't alter pre-existing tables) + intraday/flow (19/07).
        cols = {r[1] for r in c.execute("PRAGMA table_info(daily_observations)").fetchall()}
        for col, typ in (("ret_t5", "REAL"), ("win_t5", "INTEGER"),
                         ("intraday_ratio", "REAL"), ("foreign_net_pct", "REAL"),
                         ("prop_net_pct", "REAL")):
            if col not in cols:
                c.execute(f"ALTER TABLE daily_observations ADD COLUMN {col} {typ}")


@contextmanager
def _conn():
    conn = sqlite3.connect(config.DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── OHLCV history ────────────────────────────────────────────────────────────────
def upsert_ohlcv(symbol: str, df: pd.DataFrame):
    """UPSERT a symbol's daily bars. df needs columns time/open/high/low/close/volume."""
    if df is None or df.empty:
        return
    rows = [
        (symbol, pd.Timestamp(r["time"]).strftime("%Y-%m-%d"),
         float(r["open"]), float(r["high"]), float(r["low"]),
         float(r["close"]), float(r["volume"]))
        for _, r in df.iterrows()
    ]
    with _conn() as c:
        c.executemany(
            "INSERT INTO ohlcv_daily(symbol,date,open,high,low,close,volume) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(symbol,date) DO UPDATE SET "
            "open=excluded.open, high=excluded.high, low=excluded.low, "
            "close=excluded.close, volume=excluded.volume",
            rows,
        )


def load_ohlcv(symbol: str, limit: int = None) -> pd.DataFrame:
    """Load a symbol's stored daily bars, ascending by date."""
    q = "SELECT date AS time, open, high, low, close, volume FROM ohlcv_daily WHERE symbol=? ORDER BY date"
    with _conn() as c:
        df = pd.read_sql_query(q, c, params=(symbol,))
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"])
        if limit:
            df = df.tail(limit).reset_index(drop=True)
    return df


# ── Score snapshots ──────────────────────────────────────────────────────────────
def save_snapshots(ts: str, ranked: pd.DataFrame, regime: str):
    """Persist one scan's per-stock scores (the intraday signal trail)."""
    if ranked is None or ranked.empty:
        return
    rows = [
        (ts, r["symbol"], float(r["buy_score"]), float(r["liquidity"]),
         float(r["momentum"]), float(r["breakout"]), regime)
        for _, r in ranked.iterrows()
    ]
    with _conn() as c:
        c.executemany(
            "INSERT OR REPLACE INTO scan_snapshots"
            "(ts,symbol,buy_score,liquidity,momentum,breakout,regime) VALUES (?,?,?,?,?,?,?)",
            rows,
        )


def load_symbol_snapshots(symbol: str, limit: int = 100) -> pd.DataFrame:
    q = ("SELECT ts, buy_score, liquidity, momentum, breakout FROM scan_snapshots "
         "WHERE symbol=? ORDER BY ts DESC LIMIT ?")
    with _conn() as c:
        return pd.read_sql_query(q, c, params=(symbol, limit))


# ── Key-value app state ──────────────────────────────────────────────────────────
def set_state(key: str, value):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO app_state(key,value) VALUES (?,?)",
                  (key, json.dumps(value, default=str)))


def get_state(key: str, default=None):
    with _conn() as c:
        row = c.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


# ── Telegram alert de-duplication ─────────────────────────────────────────────────
def already_sent(date: str) -> set:
    """Symbols already alerted today (so we never re-send the same stock)."""
    with _conn() as c:
        rows = c.execute("SELECT symbol FROM sent_alerts WHERE date=?", (date,)).fetchall()
    return {r[0] for r in rows}


def mark_sent(date: str, items):
    """Record alerted symbols. ``items`` = iterable of (symbol, buy_score)."""
    import datetime as _dt
    ts = _dt.datetime.now().isoformat(timespec="seconds")
    rows = [(date, str(sym), ts, float(score)) for sym, score in items]
    if not rows:
        return
    with _conn() as c:
        c.executemany(
            "INSERT OR IGNORE INTO sent_alerts(date,symbol,ts,buy_score) VALUES (?,?,?,?)",
            rows,
        )


# ── Forward-tracking / validation (Phase 3) ───────────────────────────────────────
def record_tracked_signals(reco_date: str, rows):
    """INSERT OR IGNORE tracked signals for the day. ``rows`` = iterable of dicts.

    IGNORE keeps the FIRST crossing of the day (so reco_close = price when the stock
    first entered the recommendation table today)."""
    import datetime as _dt
    ts = _dt.datetime.now().isoformat(timespec="seconds")
    payload = [(
        str(r["symbol"]), reco_date, ts, _f(r.get("close")), _f(r.get("buy_score")),
        r.get("state"), _f(r.get("breakout_ratio")), _i(r.get("breakout_age")),
        _f(r.get("rsi")), _f(r.get("liquidity")), _f(r.get("momentum")), _f(r.get("signal")),
    ) for r in rows]
    if not payload:
        return 0
    with _conn() as c:
        cur = c.executemany(
            "INSERT OR IGNORE INTO tracked_signals"
            "(symbol,reco_date,reco_ts,reco_close,buy_score,state,breakout_ratio,"
            " breakout_age,rsi,liquidity,momentum,signal) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            payload,
        )
        return cur.rowcount


def open_tracked_signals(cutoff_date: str):
    """Tracked signals with reco_date >= cutoff whose outcome is not yet complete
    (fewer than 5 forward sessions). Returns list of (symbol, reco_date, reco_close)."""
    q = ("SELECT t.symbol, t.reco_date, t.reco_close FROM tracked_signals t "
         "LEFT JOIN signal_outcomes o ON o.symbol=t.symbol AND o.reco_date=t.reco_date "
         "WHERE t.reco_date >= ? AND (o.n_forward IS NULL OR o.n_forward < 5)")
    with _conn() as c:
        return c.execute(q, (cutoff_date,)).fetchall()


def forward_closes(symbol: str, reco_date: str, n: int = 5):
    """Up to ``n`` sessions AFTER reco_date, ascending, as ``[(date, close)]``.

    Returns dates too so outcome maths can tell which sessions crossed a cash-
    dividend ex-date (see FIX-cash-dividend-returns.md)."""
    q = ("SELECT date, close FROM ohlcv_daily WHERE symbol=? AND date>? "
         "ORDER BY date LIMIT ?")
    with _conn() as c:
        return c.execute(q, (symbol, reco_date, n)).fetchall()


def upsert_cash_dividends(rows) -> int:
    """Upsert ``[(symbol, exright_date, value_per_share_VND)]`` into the calendar."""
    if not rows:
        return 0
    with _conn() as c:
        c.executemany(
            "INSERT INTO cash_dividends(symbol,exright_date,value_per_share) "
            "VALUES (?,?,?) ON CONFLICT(symbol,exright_date) DO UPDATE SET "
            "value_per_share=excluded.value_per_share", rows)
    return len(rows)


def dividends_between(symbol: str, after_date: str, upto_date: str) -> float:
    """Cổ tức tiền mặt/cp nhận được nếu vào lệnh ngày ``after_date``, giữ tới ``upto_date``.

    Luật VN: mua ở phiên CUỐI trước ngày GDKHQ là còn hưởng quyền → điều kiện
    exright_date > after_date (chặt) và <= upto_date. Kiểm chứng: VHM GDKHQ
    29/06/2026 (thứ Hai) — 26/06 (thứ Sáu) là phiên cuối còn hưởng quyền."""
    q = ("SELECT COALESCE(SUM(value_per_share),0) FROM cash_dividends "
         "WHERE symbol=? AND exright_date>? AND exright_date<=?")
    with _conn() as c:
        return float(c.execute(q, (symbol, after_date, upto_date)).fetchone()[0])


def _div_adj(symbol: str, entry_date: str, upto_date: str, basis: float) -> float:
    """Dividend add-back for one forward close, with a unit sanity cap.

    A computed yield > 30% of the entry price almost certainly means a unit
    mismatch (e.g. thousands-of-VND prices vs VND dividends) — refuse to adjust
    loudly rather than emit garbage returns."""
    div = dividends_between(symbol, entry_date, upto_date)
    if div <= 0:
        return 0.0
    if basis and div > 0.30 * basis:
        print(f"[db] ⚠️ bỏ qua hiệu chỉnh cổ tức {symbol} {entry_date}→{upto_date}: "
              f"{div:,.0f}đ > 30% giá vào lệnh {basis:,.0f}đ — nghi lệch đơn vị")
        return 0.0
    return div


def close_on(symbol: str, date: str):
    """The stored daily close ON a given date (None if absent).

    Outcome returns MUST use this as the entry basis rather than the live price
    captured at recommendation time: providers rewrite history with ADJUSTED
    prices after corporate actions (splits/rights), so an unadjusted live entry
    vs adjusted forward closes fabricates huge fake losses (PET −31% incident,
    09/07/2026). Close-vs-close within ohlcv_daily is always on one basis."""
    with _conn() as c:
        row = c.execute("SELECT close FROM ohlcv_daily WHERE symbol=? AND date=?",
                        (symbol, date)).fetchone()
    return row[0] if row else None


def upsert_outcome(symbol: str, reco_date: str, closes, reco_close: float):
    """Compute + store forward returns for one tracked signal.

    ``closes`` = [(date, close)]. Cash dividends whose ex-date falls inside the
    holding window are added back to the exit close (VCI prices are NOT back-
    adjusted for cash dividends — the holder does receive the money). Stored
    close_t* stay RAW (real traded prices); only ret_* are adjusted."""
    import datetime as _dt
    if not reco_close:
        return
    rets = [((c + _div_adj(symbol, reco_date, d, reco_close)) / reco_close - 1) * 100
            for d, c in closes]                              # % returns
    n = len(rets)
    def at(i):
        return rets[i] if n > i else None
    def cat(i):
        return closes[i][1] if n > i else None
    win_t3 = None if n < 3 else (1 if rets[2] > 0 else 0)
    row = (
        symbol, reco_date,
        cat(0), cat(1), cat(2), cat(3), cat(4),
        at(0), at(1), at(2), at(3), at(4),
        (max(rets) if rets else None), (min(rets) if rets else None),
        win_t3, n, _dt.datetime.now().isoformat(timespec="seconds"),
    )
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO signal_outcomes"
            "(symbol,reco_date,close_t1,close_t2,close_t3,close_t4,close_t5,"
            " ret_t1,ret_t2,ret_t3,ret_t4,ret_t5,mfe,mae,win_t3,n_forward,updated_ts)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row,
        )


def load_tracking(limit: int = 200) -> pd.DataFrame:
    """Tracked signals joined with outcomes + user feedback, newest first (UI tab)."""
    q = ("SELECT t.reco_date, t.symbol, t.state, t.reco_close, t.buy_score, "
         "       t.breakout_age, o.ret_t1, o.ret_t2, o.ret_t3, o.ret_t4, o.ret_t5, "
         "       o.mfe, o.mae, o.win_t3, o.n_forward, f.verdict AS user_verdict "
         "FROM tracked_signals t "
         "LEFT JOIN signal_outcomes o ON o.symbol=t.symbol AND o.reco_date=t.reco_date "
         "LEFT JOIN feedback f ON f.symbol=t.symbol AND f.reco_date=t.reco_date "
         "ORDER BY t.reco_date DESC, t.buy_score DESC LIMIT ?")
    with _conn() as c:
        return pd.read_sql_query(q, c, params=(limit,))


# ── Feedback (Phase 4) ────────────────────────────────────────────────────────────
def save_feedback(symbol: str, reco_date: str, verdict: str, note: str = "",
                  user_entry=None, user_exit=None):
    """Upsert one human verdict for a recommended stock."""
    import datetime as _dt
    ts = _dt.datetime.now().isoformat(timespec="seconds")
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO feedback"
            "(symbol,reco_date,verdict,note,user_entry,user_exit,ts) VALUES (?,?,?,?,?,?,?)",
            (str(symbol), str(reco_date), verdict, note or "", _f(user_entry), _f(user_exit), ts),
        )


def load_calibration_data() -> pd.DataFrame:
    """Resolved signals (T+3 available) joined with feedback — input to calibrate.py.

    Excludes picks the user flagged 'couldnt_buy' (unexecutable → not the algo's fault)."""
    q = ("SELECT t.buy_score, t.state, t.liquidity, t.momentum, t.signal, "
         "       t.breakout_age, t.rsi, t.breakout_ratio, o.ret_t3, o.win_t3, "
         "       f.verdict AS user_verdict "
         "FROM tracked_signals t "
         "JOIN signal_outcomes o ON o.symbol=t.symbol AND o.reco_date=t.reco_date "
         "LEFT JOIN feedback f ON f.symbol=t.symbol AND f.reco_date=t.reco_date "
         "WHERE o.n_forward >= 3 AND o.win_t3 IS NOT NULL")
    with _conn() as c:
        df = pd.read_sql_query(q, c)
    if not df.empty:
        df = df[df["user_verdict"].fillna("") != "couldnt_buy"].reset_index(drop=True)
    return df


# ── Loss-review registry ───────────────────────────────────────────────────────────
def mark_loss_reviewed(symbol: str, reco_date: str, cause: str):
    """Register that a losing signal has been post-mortemed (see analysis/loss_reviews.md)."""
    import datetime as _dt
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO loss_reviews(symbol,reco_date,cause,reviewed_ts) "
            "VALUES (?,?,?,?)",
            (str(symbol), str(reco_date), cause, _dt.datetime.now().isoformat(timespec="seconds")),
        )


def unreviewed_losses() -> pd.DataFrame:
    """Resolved losing signals (win_t3=0) that have NOT been post-mortemed yet."""
    q = ("SELECT t.reco_date, t.symbol, t.state, t.reco_close, t.buy_score, "
         "       t.breakout_ratio, t.breakout_age, t.rsi, o.ret_t3, o.mfe, o.mae "
         "FROM tracked_signals t "
         "JOIN signal_outcomes o ON o.symbol=t.symbol AND o.reco_date=t.reco_date "
         "LEFT JOIN loss_reviews r ON r.symbol=t.symbol AND r.reco_date=t.reco_date "
         "WHERE o.win_t3 = 0 AND r.symbol IS NULL "
         "ORDER BY t.reco_date, o.ret_t3")
    with _conn() as c:
        return pd.read_sql_query(q, c)


# ── Market Health history ────────────────────────────────────────────────────────────
def save_market_health(date: str, mh: dict):
    import datetime as _dt
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO market_health"
            "(date,health,dist_days,breadth_pct,canary_pct,index_ratio,updated_ts)"
            " VALUES (?,?,?,?,?,?,?)",
            (date, _f(mh.get("health")), _i(mh.get("dist_days")), _f(mh.get("breadth_pct")),
             _f(mh.get("canary_pct")), _f(mh.get("index_ratio")),
             _dt.datetime.now().isoformat(timespec="seconds")),
        )


def recent_reco_entries(before_date: str, n_days: int = 2):
    """(symbol, reco_date) của các tín hiệu KN trong n_days phiên GẦN NHẤT trước
    ``before_date`` — đầu vào cho leadership canary."""
    q = ("SELECT DISTINCT reco_date FROM tracked_signals WHERE reco_date < ? "
         "ORDER BY reco_date DESC LIMIT ?")
    with _conn() as c:
        days = [r[0] for r in c.execute(q, (before_date, n_days)).fetchall()]
        if not days:
            return []
        marks = ",".join("?" * len(days))
        return c.execute(
            f"SELECT symbol, reco_date FROM tracked_signals WHERE reco_date IN ({marks})",
            days).fetchall()


# ── Missed-winner registry & views ──────────────────────────────────────────────────
def mark_miss_reviewed(symbol: str, obs_date: str, cause: str):
    """Register that a missed winner has been post-mortemed (analysis/miss_reviews.md)."""
    import datetime as _dt
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO miss_reviews(symbol,obs_date,cause,reviewed_ts) "
            "VALUES (?,?,?,?)",
            (str(symbol), str(obs_date), cause, _dt.datetime.now().isoformat(timespec="seconds")),
        )


def unreviewed_misses(min_ret_t3: float) -> pd.DataFrame:
    """Significant missed winners (is_reco=0, ret_t3 ≥ bar) NOT yet post-mortemed."""
    q = ("SELECT o.obs_date, o.symbol, o.state, o.buy_score, o.liquidity, o.momentum, "
         "       o.signal, o.close_ref, o.ret_t3 "
         "FROM daily_observations o "
         "LEFT JOIN miss_reviews r ON r.symbol=o.symbol AND r.obs_date=o.obs_date "
         "WHERE o.is_reco=0 AND o.win_t3=1 AND o.ret_t3 >= ? AND r.symbol IS NULL "
         "ORDER BY o.ret_t3 DESC")
    with _conn() as c:
        return pd.read_sql_query(q, c, params=(min_ret_t3,))


def load_missed_winners(min_ret_t3: float, limit: int = 200) -> pd.DataFrame:
    """All resolved significant missed winners (for the UI tab), newest/biggest first."""
    q = ("SELECT o.obs_date, o.symbol, o.state, o.buy_score, o.close_ref, o.ret_t3, "
         "       r.cause AS review_cause "
         "FROM daily_observations o "
         "LEFT JOIN miss_reviews r ON r.symbol=o.symbol AND r.obs_date=o.obs_date "
         "WHERE o.is_reco=0 AND o.win_t3=1 AND o.ret_t3 >= ? "
         "ORDER BY o.obs_date DESC, o.ret_t3 DESC LIMIT ?")
    with _conn() as c:
        return pd.read_sql_query(q, c, params=(min_ret_t3, limit))


def pool_quality_stats() -> pd.DataFrame:
    """Aggregated reco-vs-non-reco and per-state T+3 (+T+5 when available) stats."""
    q = ("SELECT is_reco, state, COUNT(*) AS n, AVG(win_t3)*100 AS win_pct, "
         "       AVG(ret_t3) AS avg_ret, "
         "       SUM(CASE WHEN win_t5 IS NOT NULL THEN 1 ELSE 0 END) AS n5, "
         "       AVG(win_t5)*100 AS win5_pct, AVG(ret_t5) AS avg_ret5 "
         "FROM daily_observations WHERE n_forward >= 3 AND win_t3 IS NOT NULL "
         "GROUP BY is_reco, state")
    with _conn() as c:
        return pd.read_sql_query(q, c)


# ── Whole-pool daily observations (Phase 4b — unbiased learning) ───────────────────
def record_observations(obs_date: str, rows):
    """INSERT OR IGNORE one EOD snapshot per Layer-1-passing stock (incl. non-recommended).
    ``rows`` = iterable of dicts with symbol/state/buy_score/liquidity/momentum/signal/close/is_reco."""
    payload = [(
        obs_date, str(r["symbol"]), r.get("state"), _f(r.get("buy_score")),
        _f(r.get("liquidity")), _f(r.get("momentum")), _f(r.get("signal")),
        _f(r.get("close")), 1 if r.get("is_reco") else 0,
        _f(r.get("intraday_ratio")), _f(r.get("foreign_net_pct")), _f(r.get("prop_net_pct")),
    ) for r in rows]
    if not payload:
        return 0
    with _conn() as c:
        cur = c.executemany(
            "INSERT OR IGNORE INTO daily_observations"
            "(obs_date,symbol,state,buy_score,liquidity,momentum,signal,close_ref,is_reco,"
            " intraday_ratio,foreign_net_pct,prop_net_pct)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", payload,
        )
        return cur.rowcount


def open_observations(cutoff_date: str):
    """Observations with obs_date >= cutoff not yet resolved through T+5."""
    q = ("SELECT symbol, obs_date, close_ref FROM daily_observations "
         "WHERE obs_date >= ? AND (win_t3 IS NULL OR n_forward < 5)")
    with _conn() as c:
        return c.execute(q, (cutoff_date,)).fetchall()


def update_observation_outcome(symbol: str, obs_date: str, closes, close_ref: float):
    """Fill T+3 (headline) and T+5 (swing-window) outcomes for one observation.

    ``closes`` = [(date, close)]; cash-dividend add-back same as upsert_outcome."""
    import datetime as _dt
    if not close_ref:
        return
    rets = [((c + _div_adj(symbol, obs_date, d, close_ref)) / close_ref - 1) * 100
            for d, c in closes]
    n = len(rets)
    ret_t3 = rets[2] if n >= 3 else None
    win_t3 = None if n < 3 else (1 if rets[2] > 0 else 0)
    ret_t5 = rets[4] if n >= 5 else None
    win_t5 = None if n < 5 else (1 if rets[4] > 0 else 0)
    with _conn() as c:
        c.execute(
            "UPDATE daily_observations SET ret_t3=?, win_t3=?, ret_t5=?, win_t5=?, "
            "n_forward=?, updated_ts=? WHERE symbol=? AND obs_date=?",
            (ret_t3, win_t3, ret_t5, win_t5, n,
             _dt.datetime.now().isoformat(timespec="seconds"), symbol, obs_date),
        )


def load_learning_data() -> pd.DataFrame:
    """Resolved WHOLE-POOL observations (T+3 available) for unbiased weight learning.
    Excludes picks flagged 'couldnt_buy'."""
    q = ("SELECT o.buy_score, o.state, o.liquidity, o.momentum, o.signal, o.is_reco, "
         "       o.ret_t3, o.win_t3, f.verdict AS user_verdict "
         "FROM daily_observations o "
         "LEFT JOIN feedback f ON f.symbol=o.symbol AND f.reco_date=o.obs_date "
         "WHERE o.n_forward >= 3 AND o.win_t3 IS NOT NULL")
    with _conn() as c:
        df = pd.read_sql_query(q, c)
    if not df.empty:
        df = df[df["user_verdict"].fillna("") != "couldnt_buy"].reset_index(drop=True)
    return df


def recommendation_quality() -> pd.DataFrame:
    """Resolved observations for the 'does the recommendation filter add edge?' report."""
    q = ("SELECT is_reco, state, win_t3, ret_t3 FROM daily_observations "
         "WHERE n_forward >= 3 AND win_t3 IS NOT NULL")
    with _conn() as c:
        return pd.read_sql_query(q, c)


def _f(v):
    try:
        return None if v is None or (isinstance(v, float) and v != v) else float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None
