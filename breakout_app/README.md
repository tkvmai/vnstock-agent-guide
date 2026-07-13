# Breakout Screener — Vietnam (Stock Trading Spec RevC)

Standalone app that screens HOSE + HNX stocks for short-term **breakout / lướt sóng (T+2.5)**
setups using the 3-layer scoring algorithm from `Stock Trading Spec RevC`. Runs the
algorithm **locally** (no per-query token cost) and presents results in a Panel dashboard.
Claude is optional — drill into a single stock and export a bundle to analyse in Claude Desktop.

## Run

```powershell
& "C:\Users\tkvmai\.venv\Scripts\python.exe" breakout_app\run.py
# → http://localhost:5006  (auto-opens browser, starts the background scheduler)
```

Options: `--port 5010`, `--no-show` (don't open browser), `--no-scheduler` (UI only).

## How it works

```
Scheduler ──┬─ EOD warm-up (09:00, 1×/day): universe + 65d OHLCV + VN-Index → cache (parquet + SQLite)
            ├─ Intraday (every 5 min, 9:15–14:45): price_board snapshot + recompute live components
            └─ Flow refresh + EOD snapshot
                         │
                    Engine (pure functions)
       Layer 1 Hard Filter + Market Regime Gate
       → Liquidity (0.35) + Momentum (0.30) + Breakout (0.35) = BUY Score
                         │
                  Shared store → Panel dashboard (2 pages)
```

**Dashboard pages:**
- **Layer 1 — Lọc thô**: stocks that passed the hard filter (with GTGD20/CV/intraday metrics)
  + an optional list of rejected stocks *with the reason*. Tunable: **min price, min GTGD20,
  exchange selection** (HOSE/HNX) — change then click *Quét ngay*.
- **Layer 2 — Chấm điểm**: BUY-score ranking of the survivors + per-stock drill-down and
  "export bundle for Claude". Tunable: position size, min BUY score.

- **Data source**: `vnstock_data` (Golden tier). Universe = **whole HOSE+HNX market**,
  pre-filtered server-side by liquidity via `Insights().screener.filter(filters=[exchange, adtv])`
  (not just index members), then exact GTGD20 applied per-stock in Layer 1.
- **Units**: OHLCV is normalised to full VND (the API returns thousands); turnover (GTGD)
  and all thresholds are in VND, matching the spec.
- **Store**: SQLite (`data/screener.db`) accumulates daily OHLCV + 5-min score snapshots
  for restart recovery and future backtesting.

## Layout

| File | Role |
|------|------|
| `config.py` | All weights, thresholds, lookback windows |
| `engine/tables.py` | `piecewise()` + every scoring band from the spec |
| `engine/{layer1,liquidity,momentum,breakout,scoring}.py` | Pure scoring functions |
| `engine/indicators.py` | Self-contained RSI / MACD / ATR |
| `data/{universe→fetchers,cache,db}.py` | vnstock fetch + parquet cache + SQLite |
| `scheduler.py` | `run_full_scan()` orchestrator + schedule loop |
| `store.py` | Thread-safe shared state |
| `app.py` / `run.py` | Panel dashboard / launcher |

## Tests

```powershell
& "C:\Users\tkvmai\.venv\Scripts\python.exe" breakout_app\tests\test_scoring.py
```

## Known adaptations from the spec

- **Momentum weights**: the spec summed to 1.05 (0.30+0.20+0.20+0.25+0.10); flow lowered
  from 0.25 → 0.20 so weights sum to exactly 1.00.
- **Trading-status filter** (ST/HL/suspended) is skipped when the listing API exposes no
  warning flag.

## Indicator fidelity (verified against `vnstock_ta` / pandas-ta)

- **RSI(14)**: self-implemented Wilder RSI matches `vnstock_ta.momentum.rsi` **exactly**.
- **MACD(12/26/9)**: histogram follows the spec formula; differs slightly from
  `vnstock_ta` only in EMA seeding (`histogram_pct` is tiny and lands in the same score band).
- **ATR**: implemented as `mean(TrueRange, n)` per the spec's explicit definition
  (`vnstock_ta.atr` defaults to Wilder/RMA smoothing — intentionally not used here).
- **Smart-money flow**: uses each stock's exact **5-session net** (excluding today) from
  `Trading(source='VCI').foreign_trade()` + `prop_trade()`, normalised by 5-session turnover
  — matching Spec 3.2.4.2. Fetched once daily in the warm-up (flow is EOD data).
