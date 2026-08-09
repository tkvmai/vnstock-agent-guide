# Breakout Screener — Development Reference

> Single source of truth for future coding sessions on `breakout_app/`.
> Read this **and** the relevant files in the repo `docs/` folder before changing code.

---

## 0. Golden rule (from repo `CLAUDE.md` — mandatory)

`CLAUDE.md` at the repo root is **binding**. Two instructions that were violated in the
first build and must be followed:

1. **API Inspection FIRST** — for `vnstock_data` (v3.0.0+) run `show_api()` /
   `show_doc("...")` to confirm a method's signature **before** writing code.
2. **Docs-first** — explore `docs/vnstock-data/`, `docs/vnstock_ta/`,
   `docs/vnstock_pipeline/` for the relevant page before coding.

Probing the live API for data *shapes* is fine, but it does not replace reading the
docs for the *correct call pattern*. "It runs" ≠ "it's the documented/correct API".

```python
from vnstock_data import show_api, show_doc, Insights, Market, Listing, Trading
show_api(Insights())          # tree of methods
show_doc("Insights.screener.filter")  # exact signature
```

---

## 1. What this app is

A **standalone Panel dashboard** implementing the 3-layer breakout / lướt-sóng (T+2.5)
scoring algorithm. **Current spec: `Stock Trading Spec RevD.md`** (timing-aware; supersedes
`Stock Trading Spec RevC.docx`, kept as historical reference).

- Runs the algorithm **locally** → no per-scan LLM token cost (deliberate; an MCP tool
  would burn tokens on every 5-minute rescan).
- Claude is **optional and manual only**: per-stock drill-down exports a markdown bundle
  (`claude_advisor.build_manual_bundle`: `_SYSTEM` swing-trade framing + `_build_user_text`
  raw technicals, NO 0–100 scores, + a vnstock-MCP enrichment hint) to paste into Claude
  Desktop / Claude Code, where the vnstock MCP can fetch fresh data. The app makes **no**
  Claude API call itself — the earlier automated "analysis per alert → Telegram" path was
  removed: an LLM rephrasing a static indicator snapshot into buy/sell signals, with no
  ability to verify or fetch fresh data, is not a reliable source. The existing MCP server
  in `mcp_server/` is untouched and unrelated.
- Stack (in `C:\Users\tkvmai\.venv`): Panel 1.9 + panel-material-ui, `schedule`,
  `vnstock_data` 3.2.1, `vnstock_ta` 1.0.6, SQLite (stdlib), pyarrow. (No `anthropic` SDK —
  the app no longer calls the Claude API; the SDK may still be installed but is unused.)

Run:
```powershell
& "C:\Users\tkvmai\.venv\Scripts\python.exe" breakout_app\run.py   # http://localhost:5006
```

---

## 2. Architecture

```
schedule loop (daemon thread, started by run.py — NOT by `panel serve app.py`)
 ├─ on startup  → one immediate scan (force) so the dashboard is never stale/empty
 ├─ 08:00 daily  → _morning_warmup: universe + 100d OHLCV + VN-Index + 5d flow
 │                 + STATIC Layer-1 pool (7 EOD filters), THEN run_full_scan() to
 │                 publish the morning Layer-1 screen
 ├─ every 5 min  → run_full_scan() during 09:15–14:45: reuses static pool, applies
 │                 only LIVE Layer-1 filters (#6 intraday-active, #7 ceiling/floor)
 │                 + Layer-2 scoring (price_board snapshot)
 ├─ 15:30 daily  → final EOD scan
 └─ hourly 10:00–15:00 → Telegram alert: top-5 NEW Layer-2 stocks (no per-day repeats)
 (UI updates: store.update() notifies per-session listeners → doc.add_next_tick_callback,
  so background scheduler scans appear in the dashboard; +5s periodic poll as fallback.)
        │
   data/ (fetch + cache + persist)
        │  vnstock_data API ─┬─ screener.filter (universe)   ─┐
        │                    ├─ Market.ohlcv (history)         ├─ parquet cache + SQLite
        │                    ├─ Trading.price_board (live)     │
        │                    └─ Trading.foreign_trade/prop_trade (flow)
        ▼
   engine/ (PURE functions, no I/O — unit-tested with synthetic data)
   Layer1 hard filter + Market Regime Gate
   → Liquidity(0.35) + Momentum(0.30) + Breakout(0.35) = BUY score
        ▼
   store.py (thread-safe shared state)
        ▼
   app.py  Panel dashboard — Tab 1 (Layer 1) + Tab 2 (Layer 2)
```

**Design invariant:** everything in `engine/` is a pure function taking DataFrames →
returning numbers. No network, no globals. That is what makes `tests/test_scoring.py`
deterministic and runnable without API access.

---

## 3. File map

| File | Responsibility |
|------|----------------|
| `config.py` | All weights, thresholds, lookback windows, defaults, paths. **Tune here, not in engine.** |
| `clock.py` | Vietnam trading clock (UTC+7): `minutes_elapsed()` (225-min session), `is_trading_hours()`. |
| `engine/tables.py` | `piecewise()` + every score band from the spec (encoded as ascending `(upper, score)` lists). Handles non-monotonic tables (RSI, return_5d). |
| `engine/indicators.py` | Self-contained `wilder_rsi`, `macd_histogram`, `true_range`, `atr`, `sma`. |
| `engine/layer1.py` | `check_market_regime()`, `compute_layer1_metrics()`, `passes_hard_filter()`. |
| `engine/liquidity.py` | Liquidity score (2A). |
| `engine/momentum.py` | Momentum score (2B): composite, MA, RS, flow, technical. |
| `engine/breakout.py` | Trigger score (RevD 2.2a): pivot, `breakout_age`, freshness bands, age_factor. |
| `engine/setup.py` | Pre-breakout Setup score (RevD 2.2b) + `is_pre_breakout()` state test. |
| `engine/scoring.py` | `score_stock()` orchestrator: state machine + overheat mult → BUY; `rating()`. |
| `analysis/calibrate.py` | Phase 4 offline report: win-rate by band/state, component↔outcome correlation, suggested `W_BUY` (printed only, never writes config). |
| `data/fetchers.py` | All vnstock_data calls + unit normalisation + ThreadPool. |
| `data/cache.py` | Parquet OHLCV bundle per trading date (fast same-day reuse). |
| `data/db.py` | SQLite: `ohlcv_daily`, `scan_snapshots`, `app_state`. |
| `scheduler.py` | `ensure_history()`, `run_full_scan()` orchestrator, `start_scheduler()`. |
| `store.py` | Thread-safe holder of latest result + settings. |
| `app.py` | Panel `MaterialTemplate`, 2 tabs, periodic refresh, drill-down. |
| `run.py` | Launch scheduler + serve dashboard. |
| `tests/test_scoring.py` | Pytest-free self-runner (`python tests/test_scoring.py`). |

---

## 4. vnstock_data API reference (verified against v3.2.1)

> ⚠️ The installed library (3.2.1) sometimes differs from `docs/` (written for a newer
> version). Where they differ, the **installed signature wins** — confirm with `show_doc`.

### 4.1 Units — CRITICAL
- `Market().equity(sym).ohlcv()` returns prices in **THOUSANDS of VND** (VCB close = 61.6).
- `price_board` / `screener.filter` return **FULL VND** (VCB = 61,600).
- `fetchers.fetch_ohlcv()` auto-scales OHLCV ×1000 when `close.median() < 1000`, so the
  whole app works in **full VND**. GTGD = close(VND) × volume(shares) = VND. Thresholds
  (`MIN_PRICE=5000`, `MIN_GTGD20=20e9`) are in full VND, matching the spec.
- VN-Index OHLCV is **not** scaled (points, ~1250). RS/regime use ratios → scale-invariant.

### 4.2 Universe — whole market (do NOT use index membership)
```python
Insights().screener.filter(filters=[...], limit=2000)   # kwarg is `filters=` in 3.2.1
```
- **No `filters`** → arbitrary tiny subset (≈7 rows of junk). Always pass filters.
- Filter the whole market by exchange + liquidity:
  ```python
  filters = [
    {"name": "exchange", "conditionOptions": [{"type": "value", "value": "hsx"}]},  # or "hnx"
    {"name": "adtv", "extraName": "30Days", "conditionOptions": [{"from": 14e9, "to": 1e15}]},
  ]
  ```
- One call per exchange (the `exchange` condition takes a single value). Returns
  `symbol, exchange, avg_value_30d, price, ceiling_price, floor_price, accumulated_volume,
  rsi, macd, ...` for every qualifying stock on that exchange.
- Fallback (`_fallback_universe`): `Listing(source="VCI").symbols_by_exchange()` →
  3371 rows (HSX 658, HNX 314, UPCOM 828, DELISTED 1477); filter `type=="STOCK"` + exchange.
- **Earlier bug:** universe was VN100 + HNX30 (130 names) → missed most of the market.

### 4.3 Live snapshot (every 5 min)
```python
Trading(symbol=batch[0], source="KBS").price_board(symbols_list=batch)  # batch ≤ 50
```
Fields used: `close_price, high_price, low_price, ceiling_price, floor_price,
volume_accumulated (today shares), exchange, foreign_buy_volume, foreign_sell_volume`.
KBS price_board is **realtime** during the session.

### 4.4 Money flow (per stock, EOD — cached daily)
```python
Trading(symbol=sym, source="VCI").foreign_trade(start, end)  # col: fr_net_value_total
Trading(symbol=sym, source="VCI").prop_trade(start, end)     # col: total_trade_net_value
```
- Spec 3.2.4.2 wants the **5-session net excluding today**, normalised by 5-session
  turnover. `_net_5d_excl_today()` filters `trading_date < today`, takes last 5, sums.
- This replaced an earlier 10-day aggregate proxy (`Insights().flow.foreign()` `value_10d`).

### 4.5 Rate limits
- Golden tier ≈ **500 req/min ≈ 8/s**. Keep `ThreadPoolExecutor` workers **≤ 6**
  (pipeline best-practices: `max_workers > 10` → IP block). Current: ohlcv=6, flow=5.
- `vnai` telemetry spawns `git` subprocesses; `fetchers.py` patches `subprocess.Popen`
  to force `stdin=DEVNULL` (prevents hangs under headless/detached launch).

---

## 5. Indicator fidelity (verified vs vnstock_ta / pandas-ta)

| Indicator | Implementation | Verification |
|-----------|----------------|--------------|
| RSI(14) | Wilder via `ewm(alpha=1/14)` | **Exact match** with `vnstock_ta.momentum.rsi` (47.7728 = 47.7728) |
| MACD(12/26/9) | EMA spans, hist = macd − signal | Slightly differs from vnstock_ta (pandas-ta seeds EMA with SMA); `histogram_pct` tiny → same score band |
| ATR | `mean(TrueRange, n)` | **Per spec** (spec defines simple mean); vnstock_ta defaults to Wilder/RMA — intentionally not used |

Indicators are self-implemented (not via `vnstock_ta` instance) to keep `engine/` pure
and tests deterministic. If you ever switch to `vnstock_ta`, keep ATR as simple-mean.

---

## 6. Scoring algorithm → spec mapping

> **RevD (current) — timing-aware.** See `Stock Trading Spec RevD.md` for the full spec.
> RevD rewrote RevC's breakout/timing layer to fix "recommends too late". Everything else
> (liquidity, momentum, RS, flow, A/D, dry-up, base-quality, closing) is unchanged from RevC.

**RevD BUY:**
```
Signal  = Trigger (confirmed breakout)  OR  Setup (pre-breakout), chosen by state
BUY_raw = 0.35·Liquidity + 0.25·Momentum + 0.40·Signal          (config.W_BUY)
BUY     = BUY_raw × overheat_mult × state_mult
```
- **State machine** (`scoring._determine_state`, config `FRESH_MAX_RATIO/AGE`, `PRE_BREAKOUT_*`):
  `BREAKOUT_FRESH` (age≤1 & ratio≤1.04) · `BREAKOUT_LATE` (age≥2 or ratio>1.04) ·
  `PRE_BREAKOUT` (coiling ≤3% below pivot + dry-up + ATR contraction + Stage-2 + RS≥0) ·
  `NONE` (excluded from ranked). `config.STATE_MULT`: fresh 1.0 / pre 0.95 / late 0.6 / none 0.
- **Trigger** (`breakout.py`, `config.W_TRIGGER`) = 0.35·price_fresh + 0.25·volume + 0.20·dry_up
  + 0.10·base + 0.10·closing, **× age_factor** (`tables.age_factor`). `price_fresh`
  (`tables.PRICE_FRESH_BANDS`) rewards a fresh cross and DECAYS with extension (opposite of
  RevC). `breakout_age` = sessions since close first cleared the prior-20d high. RevC's
  `risk_ratio` penalty is **removed** (extension→price_fresh, staleness→age).
- **Setup** (`setup.py`, `config.W_SETUP`) = 0.30·proximity + 0.25·base + 0.20·dry_up
  + 0.15·structure + 0.10·rs — pre-breakout quality, reuses breakout raw ratios + momentum.
- **Overheat mult** (`tables.overheat_mult`) = rsi_mult × ext_mult — makes overbought/extended
  actually cost 25–55% (RevC: ~1.8%). Applied to every state.

--- RevC (historical) ---

`BUY = 0.35·Liquidity + 0.30·Momentum + 0.35·Breakout`.

- **Layer 1 (`engine/layer1.py`)** — split by update frequency (Spec timing table):
  - **Static (7 filters, once/day in `_morning_warmup` → `static_pool`)**: exchange,
    trading status (skipped), ≥60 sessions, price ≥ min, GTGD20 ≥ threshold, CV<200%,
    clean data. `passes_static()` + `static_metrics()`.
  - **Live (2 filters, every 5 min)**: #6 intraday-active ≥30%, #7 ceiling/floor.
    `passes_live()` (also returns intraday_ratio + time_ratio for scoring).
  - **Market regime gate** (once per scan): `vnindex/ma20 < 0.97 and ma5 < ma20` →
    **blocked** (auto-runs skip Layer 2; manual override available); `< 1.00` → **caution**.
  - `passes_hard_filter()` (static+live combined) is kept for unit tests / ad-hoc use.
- **Liquidity (2A)** = 0.55·gtgd20 + 0.30·intraday + 0.15·cv.
- **Momentum (2B)** = 0.30·composite + 0.20·ma + 0.20·rs + 0.20·flow + 0.10·technical.
  - **Spec quirk:** original weights summed to 1.05; user lowered flow 0.25→0.20 → sums to 1.0.
  - composite = multi-TF returns (1d/5d/20d) × consistency multiplier.
  - ma = price_vs_ma20 + price_vs_ma50 + MA20-vs-MA50 alignment + slope (ma20+ma50).
  - rs vs VN-Index (1m/3m) × acceleration multiplier.
  - flow = (0.40·A/D + 0.60·SMF) × convergence multiplier; SMF = 0.6·foreign + 0.4·prop.
  - technical = 0.60·RSI + 0.40·MACD histogram_pct.
- **Breakout (2C)** — gated: if `close < max(close,20)` → 0. Else
  0.30·price + 0.25·volume + 0.20·dry_up + 0.15·base_quality + 0.10·closing,
  × risk_ratio penalty (breakout_ratio × ATR5/close).

All numeric bands live in `engine/tables.py` and exactly mirror the spec tables.

---

## 7. Caching & persistence

- **Parquet** (`data/cache/ohlcv_<date>.parquet`): whole-universe OHLCV for the day;
  reused on restart, **self-healing** (fetches only symbols missing from the bundle).
- **SQLite** (`data/screener.db`):
  - `ohlcv_daily(symbol,date,o,h,l,c,v)` — UPSERT, accumulates history → future backtest.
  - `scan_snapshots(ts,symbol,buy,liq,mom,bo,regime)` — every scan → intraday signal trail.
  - `app_state(key,value)` — restart recovery.
  - `sent_alerts(date,symbol,ts,buy_score)` — Telegram per-day dedup.
  - **`tracked_signals(symbol,reco_date,reco_close,buy_score,state,…)`** (Phase 3) — each
    recommended stock logged once/day at first crossing (INSERT OR IGNORE → reco_close = price
    when first recommended). Only actionable states (FRESH+PRE_BREAKOUT) ≥ `ALERT_MIN_SCORE`.
  - **`signal_outcomes(symbol,reco_date,close_t1..t5,ret_t1..t5,mfe,mae,win_t3,n_forward)`**
    (Phase 3) — forward returns from `ohlcv_daily`. Headline `win_t3 = ret_t3 > 0` (first
    sellable ~T+2.5). Updated by `_update_outcomes()` in the 15:30 `_eod_job` + morning warmup.
  - **`feedback(symbol,reco_date,verdict,note,user_entry,user_exit)`** (Phase 4) — human
    verdict per pick (good/bad/took/skipped/false_breakout/couldnt_buy). `couldnt_buy` is
    excluded from calibration (unexecutable → not the algo's fault).
- `_hist` (in-memory in `scheduler.py`) caches universe/ohlcv/vnindex/flow keyed by a
  `sig = (date, universe_size, exchanges, min_gtgd20)`. Intraday rescans only re-fetch
  `price_board`; OHLCV/flow are reused → fast.

---

## 8. Settings (dashboard → `store` → scheduler)

| Setting | Default | Affects |
|---------|---------|---------|
| `position_size` | 50,000,000 VND | Liquidity safety_ratio |
| `min_score` | 50 | UI filter on Layer-2 table (no rescan) |
| `min_price` | 5,000 VND | Layer-1 filter #4 (Tab 1) |
| `min_gtgd20` | 20e9 VND | Layer-1 #5 **and** universe adtv pre-filter floor (Tab 1) |
| `exchanges` | [HOSE, HNX] | Universe + Layer-1 #1 (Tab 1) |

There is **no `universe_size` setting**. The universe is every HOSE+HNX stock whose
30-day turnover clears the pre-filter floor (`0.7 × min_gtgd20`); its size is
self-adjusting (e.g. ~121 stocks @ 20B, ~87 @ 50B). `config.MAX_UNIVERSE` (800) is
only a non-binding safety ceiling. Changing min_price / min_gtgd20 / exchanges
requires clicking **"Quét ngay"**; changing min_score re-filters instantly.

---

## 9. Run & verify

```powershell
$env:PYTHONIOENCODING="utf-8"
cd "C:\Users\tkvmai\Documents\GitHub\vnstock-agent-guide"

# Engine unit tests (no API)
& "C:\Users\tkvmai\.venv\Scripts\python.exe" breakout_app\tests\test_scoring.py

# One scan end-to-end (force regime ok to exercise scoring in a downtrend)
& "C:\Users\tkvmai\.venv\Scripts\python.exe" -c "import sys;sys.path.insert(0,'breakout_app');import scheduler,store;from engine import layer1;layer1.check_market_regime=lambda s:('ok',1.0,'forced');print(scheduler.run_full_scan().head())"

# Dashboard (restart required after any .py change — autoreload is off)
& "C:\Users\tkvmai\.venv\Scripts\python.exe" breakout_app\run.py
```

Restarting: Ctrl+C the server, re-run `run.py`, hard-refresh the browser (Ctrl+F5).

---

## 10. Known adaptations & open items

- **Trading-status filter (ST/HL/suspended)** skipped — `Listing` exposes no warning flag.
  To add: `Listing(source="VND").all_symbols()` has a `status` column (per docs/02-listing).
- **Momentum weights** normalised by their sum in `momentum.py` (no-op now that they = 1.0).
- **Flow window** uses exactly 5 sessions excluding today (cached daily; spec says flow is EOD).
- Reference alternatives noted in docs but unused: `Reference().equity.list_by_exchange()`,
  `Reference().events.market()` (market holidays — could refine the trading calendar).

### Possible future upgrades (not implemented)
- **WebSocket streaming** (`vnstock_pipeline.stream.WSSClient`) for true realtime intraday
  instead of 5-min REST polling — Golden/Diamond only, sample code in Vnstock account.
- **`vnstock_pipeline.core.Scheduler`** (retry/backoff/rate_limit_wait) instead of the raw
  ThreadPool for more robust whole-market fetching.

---

## 11. Session change log

1. Built engine (pure functions) + tables from spec; 10 self-contained tests pass.
2. Built data layer, scheduler, store, Panel dashboard.
3. User decision: flow weight 0.25 → 0.20 (momentum sums to 1.0).
4. Split UI into **Tab 1 (Layer 1)** — passed + failed-with-reason lists, tunable
   min_price / min_gtgd20 / exchange — and **Tab 2 (Layer 2)** — BUY ranking + drill-down.
5. Read `docs/` (vnstock_ta fully; key vnstock-data + pipeline pages); verified indicators.
6. **Fixed universe bug**: switched from VN100+HNX30 to whole-market `screener.filter`
   (correct kwarg `filters=`); lowered ThreadPool workers ≤6 for rate-limit safety;
   switched flow to exact 5-session per-stock `foreign_trade`/`prop_trade`.
7. **Removed `universe_size` cap** (was a redundant leftover from the VN100 design):
   the liquidity pre-filter already produces the correctly-sized input, so the universe
   is now self-adjusting; only `config.MAX_UNIVERSE` remains as a safety ceiling.
8. **Fixed auto-refresh not firing.** `run.py` serves a shared template object, so a
   module-level `pn.state.add_periodic_callback` never bound to a session → the dashboard
   never updated (no "scanning" text, Layer-1 table stayed empty after a scan). Fix:
   register the periodic refresh inside `pn.state.onload(...)` (per session), and set an
   immediate "Đang quét..." status in the button handler (runs in session context).
   **Gotcha for future UI work:** any periodic/stateful Panel callback must be registered
   per-session (onload), not at module import, when serving a pre-built object.
9. **Manual Layer-2 run.** When regime is `blocked` the auto/scheduled scans run Layer 1
   only (per spec). `run_full_scan(override_regime=True)` forces Layer-2 scoring anyway;
   wired to a "Chạy Layer 2 thủ công" button on Tab 2. The regime banner still shows the
   real downtrend — the override only affects whether scoring runs. Added `[screener ...]`
   progress logs to the server console for operational visibility.
10. **Split Layer 1 static (once/day) vs live (every 5 min)** per the spec's update-
    frequency table. `_morning_warmup` (08:00) computes the static pool once; the 5-min
    job reuses it (ensure_history early-returns on matching sig) and only re-applies the 2
    live filters + scoring. `min_price` now part of the cache sig. Heavy Layer-1 work no
    longer repeats every 5 minutes.
11. **Layer-2 drill-down redesigned into 3 sections** (Thanh khoản / Động lượng / Breakout),
    each listing every sub-component's value + a short Vietnamese interpretation
    (`_render_detail`). `score_liquidity` now also returns raw gtgd20/cv/intraday_ratio for
    display. Breakout shows a "chưa breakout" note when gated.
12. **Auto-run reliability.** `_morning_warmup` now also runs a scan (was building the
    pool only → dashboard never refreshed in the morning); `start_scheduler` does one
    startup scan; `store.update()` notifies per-session listeners so background scheduler
    scans push to the UI. NOTE: auto jobs only run via `run.py` (starts the scheduler) and
    `_intraday_job` only fires 09:15–14:45 on weekdays — outside that window nothing
    auto-runs by design.
13. **Drill-down enrichment.** Liquidity "Hoạt động intraday" now also shows raw
    `GTGD_intraday` (tỷ) + `volume_intraday` (CP). Money-flow SMF row broken out into
    **foreign vs proprietary**: each shows raw 5-session net (tỷ VND) → normalised
    `%GTGD` → sub-score. `momentum._score_flow` returns foreign/prop pct + sub-scores;
    `score_liquidity` returns raw intraday GTGD/volume; scheduler attaches raw 5d net VND.
14. **Drill-down explanations rewritten faithful to Spec RevC.** Each of the 3 sections
    now opens with the spec's core *"Câu hỏi"* (what the component answers), and every
    sub-component meaning uses the spec's own rationale + thresholds (e.g. dry-up is
    counter-intuitive/VCP, MA20-vs-MA50 = dead-cat-bounce guard, RSI 60–70 sweet spot for
    T+2.5, risk_ratio = T+2.5 lock-in). Keep `_render_detail` text sourced from the spec,
    not ad-hoc paraphrase.
15. **Telegram hourly alerts** (`notify.py` + `_alert_job`). Pushes the top-`ALERT_TOP_N`
    Layer-2 stocks (buy_score ≥ `ALERT_MIN_SCORE`) to a Telegram chat hourly
    `ALERT_START_HOUR..ALERT_END_HOUR` on weekdays, **never re-sending a symbol within the
    same day** (`sent_alerts` table, `db.already_sent`/`mark_sent`). **Dedup order matters:
    the top-N is taken FIRST (by score), then already-sent symbols are dropped from that
    window** — so an alert only ever contains genuine top-N stocks. It does NOT back-fill
    empty slots with lower-scored stocks; if all of today's top-N were already sent, the job
    no-ops rather than alerting weaker names. Runs in the scheduler
    (so needs `run.py`). Outbound via Telegram **Bot API** (stdlib urllib) — the app cannot
    use the chat-session Telegram MCP. Credentials from env (TELEGRAM_BOT_TOKEN/
    TELEGRAM_CHAT_ID) or `data/telegram_config.json` (gitignored; template
    `telegram_config.example.json`). No credentials → job no-ops with a log line. **RevD alert
    content** (`_format_alert` + `_timing_note`): each pick shows its state (🟢 Mua ngay /
    🔵 Sắp breakout), BUY+rating, TK/ĐL/Tín hiệu/giá, and a timing line (breakout age + % above
    pivot, or % below pivot for pre-breakout, + RSI). Alerts only FRESH+PRE_BREAKOUT states
    (`_alert_job`). **No Claude analysis** (see entry 16).
16. **Removed: automated per-stock Claude analysis in alerts.** An earlier version called
    the Claude API (`anthropic` SDK) for each alerted stock and pushed the analysis to
    Telegram. Removed by user decision: an LLM rephrasing a static, unverifiable indicator
    snapshot into buy/sell signals — with no realtime data, no MCP, no way to fetch fresh
    context — is not a reliable source. Gone with it: `config.ANALYSIS_*` knobs,
    `ANTHROPIC_CONFIG_PATH`, `data/claude_config*.json`, and the `analyze_stock`/API code in
    `claude_advisor.py`. **Kept:** the manual "Xuất bundle hỏi Claude" button, which exports
    the same prompt to paste into Claude Desktop / Claude Code where the vnstock MCP *can*
    fetch fresh data (`claude_advisor.build_manual_bundle`).
17. **RevD — timing-aware scoring (Phase 2).** Rewrote the breakout/timing layer to fix
    "recommends too late" (full spec: `Stock Trading Spec RevD.md`). New: state machine
    (`BREAKOUT_FRESH`/`PRE_BREAKOUT`/`BREAKOUT_LATE`/`NONE`), `breakout_age` + `age_factor`,
    freshness `price_fresh` bands (reward fresh cross, decay with extension — opposite of
    RevC), `overheat_mult` (real overbought penalty), and a new `engine/setup.py` pre-breakout
    Setup score. `BUY = (0.35·Liq + 0.25·Mom + 0.40·Signal) × overheat × state_mult`. Scheduler
    drops `NONE` from ranked and alerts only `FRESH`+`PRE_BREAKOUT` (de-emphasizes `LATE`). UI:
    "Trạng thái" column + "Tín hiệu" (signal) progress bar + a "Trạng thái & thời điểm" section
    in the drill-down. `config` gains `W_TRIGGER`/`W_SETUP`/`STATE_MULT`/`STATE_LABELS`/
    `FRESH_MAX_*`/`PRE_BREAKOUT_*`; `W_BUY` is now `{liquidity,momentum,signal}`. 14/14 engine
    tests pass; one live scan verified states populate and `NONE` is excluded.
18. **Forward-tracking / validation (Phase 3).** New `tracked_signals` + `signal_outcomes`
    tables (`data/db.py`). `scheduler._record_signals` logs each recommended stock (actionable
    state ≥ `ALERT_MIN_SCORE`) once/day at first crossing (reco_close = price then).
    `_update_outcomes` (in the 15:30 `_eod_job` + morning warmup) computes T+1..T+5 returns from
    the accumulating `ohlcv_daily`; **headline `win_t3 = ret_t3 > 0`** (first sellable ~T+2.5),
    plus MFE/MAE. New dashboard tab **"🎯 Theo dõi — Kiểm chứng"** (`app._refresh_tracking`):
    per-signal returns + verdict (✅/❌/⏳) + aggregate T+3 win-rate. Verified deterministically
    (win/loss/hit-rate correct). Outcome updates use a 14-day window (`open_tracked_signals`).
19. **Feedback + calibration (Phase 4).** New `feedback` table + UI controls on the tracking
    tab (select a row → verdict + note → save; `app._on_save_feedback`, `db.save_feedback`).
    New `analysis/calibrate.py` (run manually): reads resolved signals + feedback
    (`db.load_calibration_data`, excludes `couldnt_buy`), prints T+3 win-rate by score band &
    state, Pearson correlation of each BUY component + diagnostics (breakout_age, rsi) with the
    T+3 return, and a **suggested `W_BUY`** (data-derived, blended 50/50 with current to avoid
    overfit). **Printed only — never writes config** (user edits `W_BUY` manually). Verified on
    20 seeded signals: signal↔ret corr detected, bands monotonic, couldnt_buy excluded, UI save
    works. No new deps (numpy correlation, not scikit-learn). **All 4 RevD phases complete.**
20. **Auto-tuning W_BUY from T+3 outcomes (Phase 4 extension).** Per user request, the app now
    **self-adjusts the 3 top-level BUY weights** automatically from the objective `win_t3`
    outcome (no manual feedback needed). `analysis/learn_weights.py`: point-biserial correlation
    of each component (liquidity/momentum/signal) with `win_t3` → data-driven weights, blended
    from the DEFAULT by `alpha=min(cap, n/scale)`, **each weight clamped within ±0.10 of default**,
    renormalised, written to `data/learned_weights.json`. Guardrails: needs ≥ `LEARN_MIN_SAMPLE`
    (20) resolved signals; always blends from default (bounded, no drift/self-reinforcement);
    excludes `couldnt_buy`. `config.get_w_buy()` returns the effective weights (learned if
    `USE_LEARNED_WEIGHTS` + valid file, else default); `config.reload_learned_weights()` refreshes
    it (called at each scan start + after the learner writes). **Never edits config.py** — delete
    the json or set the toggle False to revert. `scoring.py` uses `config.get_w_buy()`. Runs
    automatically in the 15:30 `_eod_job`; UI panel on the tracking tab shows effective weights +
    "🤖 tự học" status + a "Học lại trọng số ngay" button. Verified: signal↔win corr 0.755 →
    weight 0.40→0.48 (cap respected), sum=1.0, toggle reverts to default, 14/14 engine tests pass.
21. **Unbiased learning from the WHOLE Layer-1 pool + recommendation-quality report (Phase 4b).**
    Fixes selection bias: previously the learner only saw recommended stocks, so it could never
    detect winners the app *failed* to recommend. New `daily_observations` table snapshots EVERY
    Layer-1-passing stock once/day at EOD (incl. `state=NONE`, `is_reco` flag, `close_ref` = EOD
    close as a uniform entry basis) via `run_full_scan(record_obs=True)` in `_eod_job`;
    `_update_observation_outcomes` fills T+3 close-to-close returns. `learn_weights` now learns
    W_BUY from `db.load_learning_data()` (full pool, unbiased) instead of recommended-only.
    `calibrate.py` gains a **recommendation-quality** section (`db.recommendation_quality`):
    win-rate of recommended vs non-recommended + per-state, so you can see if the FRESH/PRE gate
    actually adds edge or is missing winners (report only — gate/threshold changes stay a human/
    spec decision, not auto-tuned). **Weight-cap bugfix:** the ±0.10 guardrail now uses
    single-factor deviation scaling (keeps sum=1 AND the box bound; the old clamp-then-renormalise
    could push a weight past the cap). Verified on 60 seeded observations: signal capped at +0.10,
    sum=1.0, reco win 69% vs non-reco 4% (+65pt edge) reported correctly.
22. **Removed the manual feedback UI section** ("Đánh giá của bạn" on the tracking tab) — made
    redundant by automatic whole-pool T+3 labelling (entry 21): the learner no longer needs
    human verdicts. Removed: FB widgets/handlers/`fb` column in `app.py`. **Kept** the `feedback`
    DB table + `db.save_feedback`/`load_calibration_data`'s `couldnt_buy` filter (harmless;
    existing rows still honoured; can be re-wired later if a manual-override need returns).
23. **Loss-review 09/07/2026 → thrust gate + clean-coil rule + weekend guard** (process: user
    asks to review "Thua" cases → TradingView chart + vnstock data → classify causes in
    `analysis/loss_reviews.md` → only repeating patterns become formula changes, validated on
    stored data). Findings on the 03/07 cohort (9 wins / 4 losses): losers averaged reco-day
    chg +0.16% & closing 45% vs winners +2.91% & 81% — flat/red pivot *touches*, not breakouts
    (BMP/TCX/POW); BMP's failed breakout was re-recommended as PRE_BREAKOUT (78.5) while dying;
    MCH was a climax re-test (watching, 1 case). Changes: **(P1) FRESH thrust gate** —
    `return_1d > FRESH_MIN_RETURN_1D (0)` AND `closing_strength ≥ FRESH_MIN_CLOSING (40)` else
    state=NONE (validated: keeps 6/6 FRESH winners, drops 3/4 FRESH losers); **(P2) PRE_BREAKOUT
    clean coil** — `recent_above == 0` in the last `PRE_BREAKOUT_NO_BREAK_SESSIONS (3)` sessions
    (breakout.py exposes `recent_above`); **(P4) weekend guard** — `_record_signals` +
    `record_obs` skip Sat/Sun (weekend startup scans had recorded duplicate signals dated
    04-05/07 from Friday data; 44 artifact rows deleted, backup `data/screener.db.bak-20260709`).
    15/15 engine tests (new: thrust-gate + failed-coil cases). P3 (market-red day) & P5 (climax)
    left as watch items in loss_reviews.md. **Review registry:** `loss_reviews` DB table
    (`db.mark_loss_reviewed` / `db.unreviewed_losses`) records post-mortemed cases so a new
    "review các mã Thua" session only picks up unreviewed losers (03/07 cohort backfilled).
24. **Missed-winners tab + review workflow (false negatives).** New tab **"📊 Bỏ sót — Toàn
    pool"**: reco-vs-non-reco T+3 quality summary (`db.pool_quality_stats`) + table of
    non-recommended stocks that won ≥ `MISS_MIN_RET_T3` (3%) at T+3 (`db.load_missed_winners`),
    with a derived "vì sao bị loại" column (NONE / LATE / below score threshold) and review
    status. Registry `miss_reviews` table + `db.unreviewed_misses`/`mark_miss_reviewed` —
    "review các mã bỏ sót" only picks up unreviewed cases (mirror of the loss-review flow;
    procedure + conclusion taxonomy in `analysis/miss_reviews.md`). Noise guard: sub-3% drifts
    are not "misses". Data accumulates from the 15:30 EOD snapshot (started 09/07); first
    reviewable cases ~3 sessions later. Gate loosening stays a human/spec decision, validated
    on `daily_observations` first.
25. **Corporate-action–safe outcome basis (PET incident, 09/07).** PET showed a fake −31% T+3:
    it had an ex-date (~2/3 adjustment) and the provider REWRITES OHLCV history with adjusted
    prices on every refetch, while the recorded `reco_close` (live price at recommendation) stays
    unadjusted → mixed bases fabricate huge losses. Fix: outcome returns are now computed
    **entirely within `ohlcv_daily`** — entry basis = `db.close_on(symbol, reco/obs_date)`
    (fallback to the recorded price when absent), so entry and forward closes always share one
    adjustment basis and self-heal on re-adjustment. `reco_close` is still stored/displayed as
    the alert-time price. Applied to both `_update_outcomes` and `_update_observation_outcomes`;
    all open outcomes recomputed (PET 6/7 → +3.51% WIN; cohort 6/7 corrected to 9/15 wins,
    +0.60% avg). **Semantic note:** headline T+3 is now close-to-close (reco-day EOD close →
    T+3 close), not intraday-alert-price → close; intraday-run winners score slightly lower
    (03/07 cohort shifted 9/13 → 6/13). Standardised, robust, and matches the original plan's
    convention.
26. **Dual-horizon validation (T+3 headline + T+5/MFE secondary).** T+3 alone misses the spec's
    swing window (vài phiên → 1-2 tuần) and is noisy near breakeven. Roles fixed up-front (no
    metric shopping): `win_t3` stays the HEADLINE + learner label (T+2.5 lock-in risk);
    `win_t5`/`ret_t5` = swing-window view; `MFE ≥ 3%` (app.MFE_TAKE_PROFIT) = chance-to-take-
    profit flag. Changes: tracking tab shows T+4/T+5 columns + composite verdict
    ("✅ Thắng · T+5 ✅ · 💰"); `daily_observations` extended to 5 forward sessions
    (ret_t5/win_t5 columns, ALTER-TABLE migration in `init_db`; `open_observations` resolves
    to n<5); `pool_quality_stats` + the Bỏ sót tab summary report both horizons. Learner label
    unchanged (revisit after ~1 month of data: compare component correlations vs T+3 vs T+5).
27. **Scheduler-thread hardening (zombie incident 10-11/07).** The `_loop` thread called
    `schedule.run_pending()` unguarded and `_alert_job` had no outer try — one job exception
    killed the scheduler thread FOREVER while Panel kept serving stale UI (symptom: last scan
    10/07 14:40, 15:30 EOD + next-day 08:00 warmup never fired, tracking tab stuck on "⏳ chờ"
    although outcomes were computable). Fix: `run_pending()` wrapped in try/except (logs
    `scheduler ERROR`, loop survives) + `_alert_job` body moved into `_alert_job_inner` behind
    a catch-all. Ops note: outcomes were back-filled manually on 11/07; if the app misses the
    15:30 job, the 08:00 warmup (or a manual `_update_outcomes()`) catches up.
28. **Lateness audit (11/07, `analysis/lateness_audit.py` — re-runnable).** Retrospective
    per-recommendation audit answering "does the app still recommend late?": (1) all 55 FRESH
    signals fired at age 0-1 with run-up mean +0.20% / max +0.83% above pivot — the RevC
    late-by-days problem is gone (age cap is by construction; the tiny run-up is the empirical
    evidence). (2) NEW watch **P8**: 3rd+ consecutive re-recommendation of the same symbol =
    0/5 wins, −2.98% avg (1st: 41%, 2nd: 67%) — candidate alert-level rule (skip/demote after
    ≥2 consecutive reco days), confounded with the 9-10/07 market turn, needs more samples.
    (3) NEW watch **P9**: PRE early-catch is only 4/26 — replay of the day before each first
    cross shows 0 would have qualified as PRE; dominant blockers are the VCP tightness gates
    (narrowing≥0.9: 16/26, dry_up≥0.9: 12/26; 7/26 gapped straight over). Candidate: relax
    those two to score-only — decide AFTER ~2 weeks of `daily_observations` allow backtesting
    noisy-coil vs tight-coil breakout success. Findings registered in loss_reviews.md.
    **Retro extension (same day):** `retro_alert_audit()` reconstructs the RevC-era cohort from
    `sent_alerts` (17/06→02/07, 104 alerts / 45 symbols; alerts pre-date tracked_signals) using
    ohlcv_daily replay. Results: RevC alerted at mean +1.64% / max +6.83% above pivot with 18%
    of breakout alerts at age≥2 — vs RevD +0.20% / 0.83% / none → the lateness complaint is now
    QUANTIFIED and the fix validated. RevC-era T+3 win 32% (−0.86% avg) despite a rising June
    market vs RevD-era ~44%. **P8 correction:** RevC era shows the OPPOSITE repetition effect
    (4th+ re-reco: 55% win) — repetition quality is regime-dependent; P8 demoted to
    watch-with-mixed-evidence, no rule. Deleted 2 junk `2099-01-01` rows from sent_alerts.
29. **P10 — overhead-supply penalty (`overhead_mult`).** Case-review of the 10 worst RevC-era
    losses found 4 were structural RevC flaws (non-breakout alerts — impossible under RevD's
    state machine), 4 were already-fixed lateness/thrust patterns, and exposed ONE genuinely
    missed factor: 6/10 broke their 20d pivot while ≥20% BELOW their ~4-month high (trapped
    sellers above). Validated on all 143 resolved signals: avg T+3 monotonic in distance-to-high
    (<−20%: −2.11% · −20..−10%: −1.18%, win 20% · near-high: −0.34%, win 39%; corr +0.24).
    Fix: `tables.overhead_mult(dist_to_high)` — <−10% → ×0.70, −10..−5% → ×0.90, else ×1.0 —
    multiplied into BUY alongside overheat/state (`breakout.py` computes `dist_to_high` =
    close vs max of fetched history ≈ 4 months; shown in the drill-down timing section).
    Soft penalty, reversible; 16/16 engine tests. The 10 RevC cases registered in loss_reviews.
30. **P7 sector cap in alerts (user-approved 15/07).** The 9-15/07 market correction (VN-Index
    −3.8%/5 sessions) killed the 07-09/07 cohorts; 11/25 losses were brokers — an alert list
    concentrated in one sector dies together. Fix (ALERT layer only): `fetch_universe` now also
    returns `vi_sector` (ICB-2 Vietnamese label from `screener.filter`; fallback None),
    `run_full_scan` attaches it as `sector`, and `_alert_job` picks the top-N via
    `_select_top_diversified` — at most `config.ALERT_MAX_PER_SECTOR` (2) per sector, freed
    slots go to next-best other-sector names, unknown sector never capped, then the existing
    no-backfill dedup applies. Alert lines show the sector. Verified on a replica of the 08/07
    list: old top-5 had 3 brokers → new pick = 2 brokers + 2 banks + 1 real-estate. Scoring,
    dashboard, tracking unchanged. Requires app restart to take effect.
31. **Market Health layer — phase 1, OBSERVE-ONLY (user-approved 15/07).** Answers "can
    corrections be foreseen?": timing no, fragility partially. `engine/market_health.py` (pure):
    health = 0.30·dist + 0.30·breadth + 0.20·canary + 0.20·index — dist = O'Neil distribution
    days (VNINDEX down >0.2% on higher volume, 25 sessions), breadth = % pool above own MA20,
    canary = % of last-2-days recos still ≥ reco-day close (leadership failure = earliest
    signal), index = close/MA20. Wiring: `scheduler._compute_market_health` runs each scan
    (vnindex_full cached in _hist), logs + `store.market_health` + `db.market_health` history
    table; regime banner shows the score. **NO gating** — and the backtest
    (`analysis/market_health_backtest.py`, 23 sessions) says correctly so: the score DID
    collapse through the July correction (46→34→30→19→18) and warned by 09/07, BUT (a) the
    naive dist count saturates at 6-7 even in the June uptrend (needs O'Neil expiry-on-rally
    rules), (b) readings near the top (01/07: 70, 07/07: 58) show canary lags at euphoric tops,
    (c) health<55 days actually had BETTER index T+3 than ≥55 days on this tiny sample.
    Phase 2 (gating/modulating alerts) only after more history + dist-expiry refinement.
    17/17 engine tests.
32. **Full-history backtest — Phase A data foundation (user-approved 15/07).** Goal: replay the
    whole recommendation engine over ~10 years instead of drip-feeding live validation.
    `analysis/fetch_full_history.py` fetched EVERY 3-char stock on HSX+HNX+**DELISTED**
    (survivorship-bias mitigation; bonds/CWs excluded) + VNINDEX: **876/925 symbols ok,
    1.52M rows, 35 MB parquet** (one file per symbol in `data/history/`, full-VND scaled,
    resume-friendly, manifest at `_manifest.json`). Coverage: equities reach back to
    2014-2016 (median start 2016-04; 577 symbols alive mid-2016 → 822 mid-2022); VNINDEX
    2014→now (3,124 sessions); 49 empty (mostly pre-2015 delistings). Practical backtest
    window: **2016→2026**. Known approximations for Phase B (documented up-front): EOD-only
    replay (no intraday volume_ratio/filter#6/first-crossing price → entry = close), no
    foreign/prop flow (score_flow neutral, ~5% of BUY), adjusted prices → use liquidity
    PERCENTILE per date for the universe rather than fixed-VND thresholds; anti-overfit
    protocol: train 2016-21 / validation 2022-23 / holdout 2024-26 (untouched until final).
33. **Backtest Phase B — daily replay runner (`analysis/backtest.py`).** Replays the UNMODIFIED
    `engine/scoring.score_stock` day-by-day over the history store: point-in-time universe =
    top `--top` (120) by GTGD20 computed from data up to that day (incl. delisted symbols),
    CV<200 filter, MIN_BARS 65; EOD approximations per #32 (intraday_ratio pinned at 100 —
    constant across symbols so ranking unaffected; flow=None). Records the WHOLE scored pool
    (state NONE included) with ~37 columns of raw metrics + forward outcomes (ret_t1/2/3/5/10,
    MFE5/MAE5 from the same adjusted series) into `data/backtest/bt_<year>.parquet` —
    resume-per-year (delete a year file to recompute it). Speed ~1.3s/session ≈ 1h for
    2016-2026. Sanity slice (Jan-Feb 2024): 4,560 rows, states 81% NONE / 10% FRESH / 5% LATE
    / 3.4% PRE; recommendations won 52% (+0.52% T+3). `analysis/backtest_report.py` = Phase C
    entry point: by-state/band/year cuts + quick ablations on stored metrics; **holdout
    2024-26 locked behind --unlock-holdout** (single final confirmation only).
34. **Manipulation blacklist for backtest hygiene (user request 15/07).**
    `analysis/manipulation_blacklist.py`: officially-concluded manipulation cases on the VN
    market — FLC group (Trịnh Văn Quyết, sentenced 7/2024: FLC/ROS/AMD/KLF/ART/HAI/GAB),
    Louis Holdings (Đỗ Thành Nhân, 5/2023: TGG/BII + ecosystem APG/AGM/LDP/DDV/SMT windowed
    2021-22), APEC (Nguyễn Đỗ Lăng, prosecuted 6/2023: API/APS/IDJ 2021-22), Trí Việt
    (TVB/TVC 2020-22), and singles KSA/CDO/KVC/MTM/FTM with per-indictment windows.
    **Tag-not-delete** (anti-look-ahead): shell companies excluded whole-life (`window=None`),
    real businesses only during their manipulation window (AGM 2024 stays clean — verified);
    `backtest_report` main results exclude tagged rows but ALWAYS reports the manipulated
    group separately ("live didn't know" view + does the CV cap self-defend?). List is
    editable — add a dict entry and reports update.
35. **Backtest Phase C findings + first validated tunings (15/07).** Report gained per-day
    REGIME conditioning (same formula as the live gate). Key findings: (a) edge is real but
    thin in TRAIN (recos +0.51% T3 vs pool +0.22%), ~zero in VALID 2022-23; (b) regime gate
    validated where it matters — VALID recos: ok +0.10% / caution −0.17% / blocked −0.98%;
    (c) **LATE outperformed FRESH in BOTH periods** (train-ok +1.08% vs +0.35%; valid-ok +0.15%
    vs −0.55%) — momentum continuation works on VN; (d) **PRE is the most robust state** (only
    positive state in validation, 52-54% win everywhere); (e) RSI-overheat & P10-overhead
    effects flip sign between periods → left untouched (don't tune noise); (f) blacklist
    earned its keep: 96 manipulated signals leaked in train with +0.98% avg T3 (pump phase) —
    would have inflated results. **Changes applied (tuned on train, confirmed on valid):**
    P9 relaxation — `PRE_BREAKOUT_DRYUP_MAX`/`NARROWING_MAX` 0.9 → 1.05 (PRE channel more than
    doubles: +3,176 train / +1,113 valid extra signals at 51-54% win, +0.66%/+0.37% T3);
    `STATE_MULT[BREAKOUT_LATE]` 0.60 → 0.85 (kept <1.0: deepest MAE, dies hardest on turns).
    17/17 tests. HOLDOUT still locked.
36. **LATE alerts gated by regime (user chose option 3, 15/07).** `_alert_states(regime)`:
    FRESH+PRE always; **BREAKOUT_LATE only while regime == "ok"** (`ALERT_LATE_IN_OK_REGIME`
    toggle) — matches the backtest exactly (LATE shines only in favourable regimes, worst
    tail on turns). LATE alert lines carry an explicit warning (momentum-continuation, highest
    T+2.5 lock-in risk, small size + tight stop). Consistency: `_record_signals` and the
    daily_observations `is_reco` flag use the same regime-dependent set, so tracking/learning
    stay aligned with what is actually alerted. Verified: states per regime correct, LATE
    alert renders with warning, 17/17 tests.
37. **Live weight-learner DISABLED (19/07) — regime-bias incident.** The EOD auto-tuner had
    drifted W_BUY signal 0.40→0.30 (hit the −0.10 cap) from 367 live observations ALL inside
    the 9-15/07 correction (win 27.5%) with component↔win correlations of ±0.02 — pure noise,
    single-regime sample. `USE_LEARNED_WEIGHTS=False`, learned file kept as
    `data/learned_weights.json.disabled-20260719`; effective W_BUY back to RevD defaults
    (0.35/0.25/0.40), verified. Re-enable only after retraining on the 10y backtest store
    (train/validation protocol) — awaiting user's detailed requirements for that work.
38. **Full calibration campaign (W1-W5, 19/07) — RESULT: KEEP EVERYTHING.** Infrastructure:
    backtest store re-generated with 59 raw-metric columns (W1); `analysis/tuner.py` shadow
    scorer reproduces the engine EXACTLY (parity: 100% state match, |Δbuy| p95 = 0.003 on the
    full 10y store) + live-rule simulator + pre-committed objective retT5 − 0.3·|MAE5| on
    simulated top-5 picks. Disciplined search (2 rounds × 11 weight sets × 24 band tables,
    block-bootstrap noise floor σ=0.107, quantile-edge band refits, trial log
    `tuning_trials.jsonl`): **all 11 weight sets' best variants landed at +0.003..+0.045 ≪ σ**
    — the objective surface is FLAT within ±0.15 of current weights (exact values barely
    matter; the edge lives in the state machine/gates, not weight fine-tuning). All band
    refits failed too (several data-refit bands were WORSE than the heuristics, e.g.
    overheat-RSI refit −0.26/−0.54). The single σ-passing change (B_OVERHEAD refit, +0.158
    train) **FAILED the validation gate** (VALID −1.115 → −1.727) — overfit, rejected;
    consistent with #35's "overhead flips sign between periods". Per pre-committed protocol:
    **no parameter adopted; current heuristics now carry empirical legitimacy** (70 variants
    surveyed, none robustly better). Strategic implication: under the risk-penalized metric
    the objective is negative in validation for EVERY configuration tried → the binding
    constraint is WHEN to trade (regime/Market-Health), not micro-weights. Artifacts:
    `data/backtest/tuned_params.json`, `tuning_trials.jsonl`. HOLDOUT remains sealed.
39. **Market Health Phase 2 — measurement fix + gating WIRED (19/07).** (1) O'Neil expiry
    added to `count_distribution_days`: a distribution day is dropped once the index closes
    ≥5% above that day's close (fixes the phase-1 saturation bug; avg count 10y = 4.0 now;
    test added). (2) `analysis/mh_phase2.py` computed daily health 2016-2026 (fixed counter +
    breadth from the close matrix + canary from simulated picks) and tested 3 gating variants
    × 8 thresholds on the pre-committed objective: best = **GRAD X=55** (health<55 → only
    BUY≥65; health<40 → halt): TRAIN −0.019→+0.018, **VALIDATION gate PASS −1.115→−1.009**
    (threshold chosen on train only). Effect size is modest and train gain is below the weight-
    campaign σ — the accepted evidence is the consistent same-direction improvement on
    untouched validation. (3) Wired (fully reversible, `MH_GATE_ENABLED`): `_mh_mode`/`_mh_pass`
    in scheduler — health computed BEFORE the scoring loop; applied consistently to alerts
    (halt skips the job; selective raises the alert bar to `MH_GATE_STRONG_SCORE`, message
    carries a ⚕️ note), `_record_signals`, and daily-observation `is_reco`; banner shows the
    active mode. Daily health history persisted (`market_health_daily.parquet` for the 10y
    series). 17/17 tests + gate-logic unit checks.
40. **Auto-learner retired for good + live-learning repurposed (19/07).** The W_BUY learner's
    premise is empirically dead (#38: 233k-sample campaign found a FLAT objective surface —
    a drip-fed learner can only learn noise, as the #37 incident showed). Removed the
    `learn_and_save()` call from `_eod_job`; `learn_weights.py` marked DEPRECATED (kept as
    record). Live data's legitimate roles now: **(A) Drift Alarm** — compare rolling live
    win-rate vs backtest expectation bands, alarm → human re-calibration (pending, needs ≥150
    resolved live signals post-20/07); **(B) calibrate the two band groups the backtest is
    blind to** — intraday activity & foreign/prop flow: `daily_observations` now records
    `intraday_ratio`, `foreign_net_pct`, `prop_net_pct` (migration added; accumulation clock
    started 19/07; needs ~15k rows ≈ 6-12 months). **Self-reminding**: `_milestone_reminders()`
    in the EOD job checks both thresholds and sends a ONE-TIME Telegram prompt when reached
    (dedup flags in app_state). Claude memory note also written (project-pending-milestones).
41. **Obs blackout in blocked regime — fixed (30/07).** Health check found the app healthy
    through the 21-29/07 crash week (EOD fired 15:30 daily, health stuck at 12/100, zero
    signals/alerts = correct ⛔ behavior), BUT `daily_observations` recorded NOTHING for 8
    sessions: the whole scoring loop is skipped when regime=blocked, and obs_rows only exist
    inside it — i.e., the unbiased-learning/Hướng-B accumulation stopped exactly during the
    bear phase, the most valuable data for validation. Fix: `score_for_obs = record_obs and
    weekday<5` lets the EOD scan SCORE the pool even in blocked purely for observation;
    `results` (ranked/dashboard/alerts) still excluded in blocked (spec behavior unchanged)
    and obs `is_reco` is now explicitly False in blocked. The 21-29/07 gap itself is not
    backfilled (would need per-day live fields; all-NONE rows of a broken market, low value).
    Also confirmed: `scan_snapshots` stopping 20/07 is benign (save skips empty ranked);
    weekend market_health rows are Friday duplicates (harmless).

42. **VNINDEX refetched every scan — intraday regime lag fixed (30/07).** `ensure_history`
    only fetches VNINDEX once per day (cached in `_hist`), so the regime gate and market
    health ran all day on the morning bar — observed 20/07: index dropped −1.8% intraday
    while regime stayed `caution` on stale data. Fix in `run_full_scan`: after
    `ensure_history`, refetch VNINDEX via `fetchers.fetch_vnindex()` each scan (1 cheap
    call / 5 min), overwrite both `vnindex_close` (regime gate) and `vnindex_full`
    (market health dist-days/breadth context) plus the `_hist` cache; on network error,
    log and fall back to the cached series. Side benefit: market health now also sees the
    live forming bar instead of the morning snapshot.

43. **Learned-weights UI panel removed (30/07).** The auto-learner was retired 19/07
    (EOD call removed, weights file disabled) but `app.py` still showed the "⚖️ Trọng số
    tự học" section with a stale "chạy tự động mỗi phiên EOD" description and a working
    "Học lại trọng số ngay" button that would have re-created `learned_weights.json`.
    Removed: `learned_status` pane, `relearn_button`, `_render_learned_status`,
    `_on_relearn`. Replaced with a one-line static note in the tracking tab: fixed
    W_BUY 0.35/0.25/0.40, validated on the 10y backtest, Drift Alarm supersedes learning.

44. **MARKET_HEALTH.md — dedicated reference doc (user request 03/08).** Full Vietnamese
    write-up of the Market Health layer in one place: formula + weights, per-component
    scoring tables straight from `engine/market_health.py` (dist w/ O'Neil rally-expiry,
    breadth, canary, index), worked example, gating table + config knobs, phase-1/2
    evidence, the three recorded weaknesses (optimistic missing-data defaults, canary lag
    at euphoric tops, train effect below bootstrap noise), and storage/wiring notes.
    Spec RevD §2.8 now links to it.

45. **Cash-dividend return mismeasurement — fixed + backfilled (03/08, per
    FIX-cash-dividend-returns.md).** VCI back-adjusts splits/stock dividends but NOT cash
    dividends, so every ret_t1..t5/mfe/mae/win_t3 crossing a cash ex-date understated the
    return by exactly the dividend yield (holder does receive the cash). Distinct from the
    PET/close_on fix (that was basis mismatch; here both ends share a basis but the dividend
    value left the price). Implementation: `cash_dividends` table + `upsert_cash_dividends`
    + `dividends_between` (VN rule: entitled iff exright_date > entry_date, <= exit_date) +
    `_div_adj` unit guard (yield >30% of entry → refuse adjustment loudly; Quote() returns
    thousands-VND, events() returns VND — verified MBB 1000đ matches ohlcv_daily units);
    `forward_closes` now returns [(date, close)]; ret math adjusted in `upsert_outcome` and
    `update_observation_outcome` (stored close_t* stay raw); `fetchers.fetch_cash_dividends`
    (events() full history, 6 workers) + `scheduler._refresh_dividends` once/day in EOD job
    (app_state guard). Backfill: calendar loaded for all 104 signal/obs symbols (298 DIV
    rows), ALL 92 signals + 820 observations recomputed — 6 signals corrected (MBB 06/07
    +1.11→+5.22, 07/07 −0.44→+3.59 win 0→1, 08/07 −2.20→+1.80 win 0→1, VCG 09/07
    −4.56→−0.22; DPM ±0.04 drift traced to provider re-adjustment, not this fix), 38
    observations (4 win flips). §5 checks 5/5: MBB regression, no clean-row changes,
    ex-date boundary (buy 08/07 gets 1000đ, buy 09/07 gets 0), max yield 4.34% ≤ cap,
    negative test returns exact old values. Registry hygiene: MBB 07+08/07 loss_reviews
    causes rewritten `đo_sai_cổ_tức` (they were WINS investigated as losses), VCG annotated,
    P11 evidence corrected (29/06 −3.65% is mechanical dividend subtraction; real
    dividend-hunter exit evidence is intraday: open +5.3% → close 0.13% off low). Backtest
    store (§6b) deliberately NOT reprocessed — distortion cancels between recos and pool
    (§2.4), edge measurement unaffected. DB backup: screener.db.bak-20260803. P11 blind
    spot (ex-date proximity warning) remains a separate pending item (needs 3rd case).

46. **Run-up transparency warning on alerts (user feedback 05/08: "ORS tăng quá rồi").**
    Evidence check on the 10y store first: 12,611 FRESH/PRE ≥50 signals bucketed by
    pre-signal 5-session return show expectancy does NOT fall with run-up (10-15% bucket
    is the BEST: win 53%, +0.82% T+3 — consistent with LATE>FRESH momentum continuation),
    so no gate/penalty; but MAE5 deepens monotonically (−1.53% at ≤0% → −2.64% at 10-15%;
    >15% is rare n=15 AND negative). Fix = transparency only: `_runup_note` appended to
    `_timing_note` — ≥`ALERT_RUNUP_WARN_5D` (10%) warns "kỳ vọng không giảm nhưng MAE
    ~−2.6% vs −1.6% → giảm size"; ≥`ALERT_RUNUP_STRONG_5D` (15%) warns "vùng hiếm kỳ vọng
    âm → cân nhắc bỏ qua". Uses `mom_return_5d` already in the score dict. Same session
    also answered "HCM lên đỉnh rồi": HCM is −1.1% from its 52w high after a year-long
    triple-tested ceiling 25.5-26.3k — near-high is the system's best cohort (143-signal
    audit: monotonic in dist-to-high), reco was PRE at 25,450 before the +2.2% intraday
    move. 17/17 tests; render verified for both thresholds.

47. **FTD (Follow-Through Day) — study + observe-only detector (05/08).** Motivated by the
    20-31/07 no-reco window review ("nên bắt đáy V?" → no; FTD = disciplined alternative).
    Study `analysis/ftd_study.py` on the 10y store (pre-declared gate: FTD-window signals'
    objective ≥ regime-ok baseline on BOTH periods): 6 FTDs 2016-23, all at real turning
    points (01/06/18, 19/06/20 fail, 03/08/20, 03/02/21, 25/05/22, 25/11/22 — the bear
    bottom); FTD-window FRESH/PRE signals beat baseline (train obj +0.57 vs −0.07, n=27;
    valid +2.49 vs −0.74, n=3) BUT median lag FTD→regime-ok is only ~2 sessions (MA20
    crashes with price so close/MA20 recovers fast) → total added value is a few signals
    per year on tiny samples → NOT wired into the gate. Shipped observe-only instead:
    `engine/ftd.py` (pure state machine; the study's one bug — rally counter never exiting
    correction when regime recovers without FTD, causing 0 detections — is fixed in both),
    scheduler computes per scan (vnindex refetch bumped to days=300 for the trailing peak),
    banner line in app.py (ftd_window/rally/correction phases), one-time Telegram per FTD
    (app_state flag), store key `ftd`. Parity 5/5 vs study on historical cuts. Retro-check:
    FTD fired 30/07/2026 (rally day 6, +2.35%) — the exact day DCL's suppressed PRE won
    +10.3%, two sessions before regime ok. Decision gate: revisit after 3-5 live FTDs.
    17/17 tests.

## Telegram alert setup (user-supplied credentials)

1. Create a bot with @BotFather → get the **bot token**.
2. Get your **chat_id** (message the bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `chat.id`).
3. `copy data\telegram_config.example.json data\telegram_config.json` and fill both fields
   (or set env vars TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID). **`chat_id` may be a single id
   or a list** to broadcast to several chats — `send_telegram` posts to all and returns
   True if ≥1 delivered. Each chat must have started the bot first (groups: add the bot,
   chat.id is negative). Env TELEGRAM_CHAT_ID can be comma-separated.
   - **Bugfix (multi-chat):** `send_telegram` must materialise the per-chat sends into a
     list *before* `any()` — a lazy `any(_send_one(...) for ...)` short-circuits on the
     first success and silently skips the remaining chats (symptom: only the first chat_id
     ever received alerts).
4. Restart `run.py`. Tune cadence/threshold in `config.py` (ALERT_* ).
   Note: alerts only fire when Layer 2 has results — in a `blocked` downtrend the auto
   scans produce no ranking, so nothing is sent unless a manual Layer-2 run populated it.

## Money-flow interpretation (how to judge "strong flow")

Reference the **normalised % (net / 5-session GTGD)**, NOT absolute VND — 26 tỷ net is
huge for a 30 tỷ/day stock but negligible for HPG (~600 tỷ/day). Thresholds (Spec 3.2.4.2):
foreign >+5% very strong · +2..5% clear · −0.5..+0.5% neutral · <−2% heavy selling
(proprietary uses lower bands: >+3% strong). Then cross-confirm with **A/D ratio** via the
**convergence multiplier**: high %flow AND high A/D → ×1.20 (most reliable); foreign buying
but weak A/D (price down on volume) → ×0.85 (accumulation-on-weakness / distribution warn).
Window = last 5 sessions excluding today.
