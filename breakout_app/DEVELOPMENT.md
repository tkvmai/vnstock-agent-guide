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
