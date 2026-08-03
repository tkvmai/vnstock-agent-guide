"""Orchestration + scheduling.

`run_full_scan` is the core: it assembles data, applies Layer-1 filters and the
market regime gate, scores survivors, and publishes the ranked result to the
store. The schedule loop runs an EOD history warm-up once a day and a light
intraday refresh every 5 minutes during trading hours.
"""

import threading
import time
from datetime import date, datetime

import pandas as pd
import schedule

import config
import clock
import store
import notify
from data import db, cache, fetchers
from engine import layer1, scoring, market_health


def _log(msg: str):
    """Timestamped progress line to the server console (operational visibility)."""
    print(f"[screener {datetime.now():%H:%M:%S}] {msg}", flush=True)

# Module-level history cache (OHLCV doesn't change intraday — fetch once/day).
_hist = {
    "sig": None,            # (date, universe_size, exchanges) signature
    "date": None,
    "universe": pd.DataFrame(),
    "ohlcv": {},            # symbol -> full-history DataFrame (incl. today if present)
    "vnindex": pd.Series(dtype=float),
    "flow": {},             # symbol -> {foreign_net_5d, prop_net_5d} (EOD, daily)
    "static_pool": {},      # symbol -> static Layer-1 result (computed once/day)
}
_hist_lock = threading.RLock()


# ── History warm-up (expensive, once per day) ────────────────────────────────────
def ensure_history(exchanges=None, min_gtgd20: float = None, min_price: float = None,
                   force: bool = False):
    """Fetch (or reuse) the universe + OHLCV history + VN-Index for today.

    The universe (server-side liquidity pre-filter) is rebuilt whenever
    exchanges/min_gtgd20 change so settings take effect immediately; the
    expensive OHLCV pull is cached by date and self-heals for new symbols.
    Its size is determined entirely by the liquidity pre-filter — there is no
    separate top-N cap.
    """
    today = date.today().isoformat()
    exchanges = list(exchanges or config.DEFAULT_EXCHANGES)
    exact_gtgd20 = min_gtgd20 if min_gtgd20 is not None else config.MIN_GTGD20
    min_price = config.MIN_PRICE if min_price is None else min_price
    sig = (today, tuple(sorted(e.upper() for e in exchanges)), exact_gtgd20, min_price)
    with _hist_lock:
        if not force and _hist.get("sig") == sig and _hist["ohlcv"] and _hist.get("static_pool"):
            return
        same_day = _hist.get("date") == today
        ohlcv = dict(_hist["ohlcv"]) if same_day else {}
        vnindex_close = _hist.get("vnindex") if same_day else None
        flow = dict(_hist["flow"]) if (same_day and _hist.get("flow")) else {}
    db.init_db()

    _log("fetching universe…")
    universe = fetchers.fetch_universe(exchanges, min_gtgd20=exact_gtgd20)
    symbols = universe["symbol"].tolist()
    _log(f"universe = {len(symbols)} symbols")

    tag = today
    if not ohlcv and cache.has_bundle(tag) and not force:
        bundle = cache.load_ohlcv_bundle(tag)
        ohlcv = {s: g.drop(columns="symbol").reset_index(drop=True)
                 for s, g in bundle.groupby("symbol")}

    # Self-healing: fetch any universe symbols missing from the cache.
    missing = [s for s in symbols if s not in ohlcv]
    if missing or force:
        _log(f"fetching OHLCV for {len(symbols if force else missing)} symbols…")
        fetched = fetchers.fetch_ohlcv_batch(symbols if force else missing)
        for s, df in fetched.items():
            db.upsert_ohlcv(s, df)
            ohlcv[s] = df
        frames = [df.assign(symbol=s) for s, df in ohlcv.items()]
        if frames:
            cache.save_ohlcv_bundle(tag, pd.concat(frames, ignore_index=True))
    _log(f"OHLCV ready ({len(ohlcv)} cached)")

    with _hist_lock:
        vnindex_full = _hist.get("vnindex_full") if _hist.get("date") == today else None
    if vnindex_close is None or len(vnindex_close) == 0 or force or vnindex_full is None:
        vnindex_full = fetchers.fetch_vnindex()
        vnindex_close = (vnindex_full["close"].reset_index(drop=True)
                         if not vnindex_full.empty else pd.Series(dtype=float))

    # Money flow (EOD, excludes today) — fetch missing symbols only, cache daily.
    missing_flow = [s for s in symbols if s not in flow]
    if missing_flow or force:
        _log(f"fetching flow for {len(symbols if force else missing_flow)} symbols…")
        flow.update(fetchers.fetch_flow_per_stock(symbols if force else missing_flow))
    _log(f"flow ready ({len(flow)} cached)")

    # Static Layer-1 screen (7 EOD filters) — computed ONCE/day. Live filters
    # (#6 intraday-active, #7 ceiling/floor) are applied per-scan in run_full_scan.
    exch_map = dict(zip(universe["symbol"], universe["exchange"]))
    today_date = date.today()
    static_pool = {}
    for sym in symbols:
        df = ohlcv.get(sym)
        if df is None or df.empty:
            static_pool[sym] = {"passed": False, "reason": "Thiếu dữ liệu giá",
                                "gtgd20": None, "cv": None}
            continue
        hist = df[df["time"].dt.date < today_date].reset_index(drop=True)
        last_close = float(df["close"].iloc[-1])
        sm = layer1.static_metrics(hist) if len(hist) >= config.LOOKBACK_GTGD else {"gtgd20": 0.0, "cv": float("inf")}
        passed, reason, sm = layer1.passes_static(
            hist, last_close, exchange=exch_map.get(sym), min_price=min_price,
            min_gtgd20=exact_gtgd20, allowed_exchanges=exchanges, metrics=sm)
        static_pool[sym] = {"passed": passed, "reason": reason,
                            "gtgd20": sm.get("gtgd20"), "cv": sm.get("cv")}
    n_static = sum(1 for v in static_pool.values() if v["passed"])
    _log(f"static Layer-1 pool: {n_static}/{len(symbols)} passed (once/day)")

    with _hist_lock:
        _hist.update({"sig": sig, "date": today, "universe": universe, "ohlcv": ohlcv,
                      "vnindex": vnindex_close, "vnindex_full": vnindex_full,
                      "flow": flow, "static_pool": static_pool})


# ── Core scan ──────────────────────────────────────────────────────────────────────
def run_full_scan(position_size: int = None, force_history: bool = False,
                  override_regime: bool = False, record_obs: bool = False) -> pd.DataFrame:
    """Run one complete screen and publish the ranked result to the store.

    override_regime=True forces Layer-2 scoring even when the market regime gate
    is 'blocked' (manual run; the regime banner still reflects the real downtrend).
    record_obs=True snapshots the WHOLE Layer-1 pool (incl. non-recommended, state
    NONE) into daily_observations for unbiased learning — used by the EOD job only.
    """
    settings = store.get_settings()
    position_size = position_size or settings["position_size"]
    store.update_settings(position_size=position_size)

    exchanges = settings.get("exchanges") or config.DEFAULT_EXCHANGES
    min_price = settings.get("min_price")
    min_gtgd20 = settings.get("min_gtgd20")

    store.update(status="scanning", error=None)
    try:
        config.reload_learned_weights()   # pick up any auto-tuned W_BUY
        _log("scan start (preparing history…)")
        ensure_history(exchanges, min_gtgd20=min_gtgd20, min_price=min_price, force=force_history)
        with _hist_lock:
            universe = _hist["universe"]
            ohlcv = _hist["ohlcv"]
            vnindex_close = _hist["vnindex"]
            vnindex_full = _hist.get("vnindex_full")
            flow_map = dict(_hist.get("flow") or {})
            static_pool = dict(_hist.get("static_pool") or {})
        _log(f"history ready: universe={len(universe)} static_pass="
             f"{sum(1 for v in static_pool.values() if v['passed'])}")

        # Refetch VNINDEX MỖI SCAN (fix 30/07): regime gate + market health chạy trên
        # index LIVE thay vì cache buổi sáng — hết trễ intraday (đã thấy 20/07: index
        # rơi −1.8% trong phiên mà regime vẫn 'caution' theo số liệu 8h sáng).
        # 1 call rẻ mỗi 5 phút; lỗi mạng → dùng cache cũ.
        try:
            vn_fresh = fetchers.fetch_vnindex()
            if vn_fresh is not None and not vn_fresh.empty:
                vnindex_full = vn_fresh
                vnindex_close = vn_fresh["close"].reset_index(drop=True)
                with _hist_lock:
                    _hist["vnindex"] = vnindex_close
                    _hist["vnindex_full"] = vnindex_full
        except Exception as e:
            _log(f"vnindex refetch lỗi ({type(e).__name__}) — dùng cache")

        # Market regime gate
        regime, ratio, msg = layer1.check_market_regime(vnindex_close)
        store.update(regime=regime, regime_ratio=ratio, regime_msg=msg)
        vnindex_hist = pd.DataFrame({"close": vnindex_close}) if len(vnindex_close) else None

        symbols = universe["symbol"].tolist()
        exch_map = dict(zip(universe["symbol"], universe["exchange"]))
        sector_map = (dict(zip(universe["symbol"], universe["vi_sector"]))
                      if "vi_sector" in universe.columns else {})

        # Live snapshot (money flow + static Layer-1 are cached from ensure_history)
        _log("fetching price_board…")
        live_map = fetchers.fetch_price_board(symbols)
        _log(f"price_board: {len(live_map)} symbols")
        minutes = clock.minutes_elapsed()

        today = date.today()

        # Market Health TRƯỚC vòng chấm điểm (Phase 2): mode gate ảnh hưởng is_reco/
        # record; điểm vẫn publish ở store như cũ.
        try:
            mh = _compute_market_health(ohlcv, live_map, vnindex_full, today.isoformat())
        except Exception as e:
            mh = None
            _log(f"market health: bỏ qua ({type(e).__name__}: {e})")
        mh_mode = _mh_mode(mh)
        l1_rows, results, obs_rows = [], [], []
        for sym in symbols:
            exch = exch_map.get(sym)
            sp = static_pool.get(sym, {"passed": False, "reason": "Không có trong pool",
                                       "gtgd20": None, "cv": None})
            base = {"symbol": sym, "exchange": exch, "close": None,
                    "gtgd20_b": (sp["gtgd20"] / 1e9 if sp.get("gtgd20") else None),
                    "cv": sp.get("cv"), "intraday_ratio": None}

            # Static filters decided once/day — fail short-circuits here
            if not sp["passed"]:
                l1_rows.append({**base, "passed": False, "reason": sp["reason"]})
                continue

            live_raw = live_map.get(sym)
            if live_raw is None or not live_raw.get("close"):
                l1_rows.append({**base, "passed": False, "reason": "Thiếu dữ liệu live"})
                continue

            live = dict(live_raw)
            live["minutes_elapsed"] = minutes
            base["close"] = live.get("close")

            # Live filters #6 (intraday-active) + #7 (ceiling/floor) — every 5 min
            live_ok, reason, intraday_ratio, time_ratio = layer1.passes_live(sp, live)
            base["intraday_ratio"] = intraday_ratio
            l1_rows.append({**base, "passed": live_ok, "reason": reason})

            # Layer 2 scoring when regime is not blocked, OR forced manually, OR the
            # EOD observation snapshot (fix 30/07: obs stopped accumulating for 8
            # blocked sessions 21-29/07 — measurement must run even khi đứng ngoài;
            # ranked/alerts vẫn bị chặn như spec ở dưới).
            score_for_obs = record_obs and today.weekday() < 5
            if live_ok and (regime != "blocked" or override_regime or score_for_obs):
                df = ohlcv.get(sym)
                hist = df[df["time"].dt.date < today].reset_index(drop=True)
                vol_intraday = live.get("volume") or 0
                metrics = {"gtgd20": sp["gtgd20"], "cv": sp["cv"],
                           "intraday_ratio": intraday_ratio, "time_ratio": time_ratio,
                           "volume_intraday": vol_intraday,
                           "gtgd_intraday": (live.get("close") or 0) * vol_intraday}
                flow = _flow_pct(flow_map.get(sym), hist)
                res = scoring.score_stock(sym, hist, live, vnindex_hist, flow,
                                          position_size=position_size, metrics=metrics)
                res["exchange"] = exch
                res["sector"] = sector_map.get(sym)
                res["rating"] = scoring.rating(res["buy_score"])
                # Raw 5-session net VND (for drill-down display)
                fl = flow_map.get(sym) or {}
                res["mom_foreign_net_5d"] = fl.get("foreign_net_5d")
                res["mom_prop_net_5d"] = fl.get("prop_net_5d")
                # Whole-pool observation (incl. NONE) for unbiased learning / reco-quality eval.
                # is_reco = "live có alert mã này không" → blocked luôn False.
                is_reco = (regime != "blocked"
                           and res.get("state") in _alert_states(regime)
                           and res["buy_score"] >= config.ALERT_MIN_SCORE
                           and _mh_pass(mh_mode, res["buy_score"]))
                obs_rows.append({"symbol": sym, "state": res.get("state"),
                                 "buy_score": res["buy_score"], "liquidity": res["liquidity"],
                                 "momentum": res["momentum"], "signal": res.get("signal"),
                                 "close": res["close"], "is_reco": is_reco,
                                 # Hướng B: metric backtest mù — tích lũy để calibrate sau
                                 "intraday_ratio": res.get("liq_intraday_ratio"),
                                 "foreign_net_pct": res.get("mom_foreign_net_pct"),
                                 "prop_net_pct": res.get("mom_prop_net_pct")})
                # RevD: NONE bị loại; và khi blocked (chấm chỉ để ghi obs) KHÔNG publish
                # vào ranked — dashboard/alert giữ nguyên hành vi spec.
                if res.get("state") != "NONE" and (regime != "blocked" or override_regime):
                    results.append(res)

        layer1_df = pd.DataFrame(l1_rows)
        passed_count = int(layer1_df["passed"].sum()) if not layer1_df.empty else 0

        ranked = pd.DataFrame(results)
        if not ranked.empty:
            ranked = ranked.sort_values("buy_score", ascending=False).reset_index(drop=True)
            ranked.insert(0, "rank", ranked.index + 1)

        ts = datetime.now()
        store.update(layer1=layer1_df, ranked=ranked, last_scan=ts, status="idle",
                     universe_total=len(symbols), universe_passed=passed_count)
        db.save_snapshots(ts.isoformat(timespec="seconds"), ranked, regime)
        _record_signals(ranked, today.isoformat(), regime, mh_mode)
        # Market Health (Phase 2: gate ACTIVE — mode ảnh hưởng alert/tracking/is_reco)
        if mh:
            store.update(market_health={**mh, "mode": mh_mode})
            db.save_market_health(today.isoformat(), mh)
            _log(f"market health: {mh['health']}/100 {mh['label']} · mode={mh_mode} "
                 f"(phân phối {mh['dist_days']} · breadth {mh['breadth_pct']}% · "
                 f"canary {mh['canary_pct']}% · index {mh['index_ratio']})")
        if record_obs and obs_rows and today.weekday() < 5:   # no weekend artifacts
            n_obs = db.record_observations(today.isoformat(), obs_rows)
            _log(f"observations: +{n_obs} mã pool ghi nhận EOD ({today.isoformat()})")
        _log(f"scan done: regime={regime}{' (L2 forced)' if override_regime else ''} "
             f"passed_L1={passed_count}/{len(symbols)} scored={len(ranked)}")
        return ranked
    except Exception as e:
        _log(f"scan ERROR: {type(e).__name__}: {e}")
        store.update(status="error", error=str(e))
        raise


def _record_signals(ranked, reco_date: str, regime: str = "ok", mh_mode: str = "normal"):
    """Phase 3: log each stock the app RECOMMENDS today (alertable states ≥ threshold,
    qua đèn vàng MH) into `tracked_signals` — INSERT OR IGNORE, first crossing wins."""
    if date.today().weekday() >= 5:      # weekend scans replay Friday's data → junk signals
        return
    if ranked is None or ranked.empty or "state" not in ranked.columns:
        return
    rec = ranked[(ranked["state"].isin(_alert_states(regime)))
                 & (ranked["buy_score"] >= config.ALERT_MIN_SCORE)
                 & (ranked["buy_score"].map(lambda b: _mh_pass(mh_mode, b)))]
    if rec.empty:
        return
    rows = [{
        "symbol": r["symbol"], "close": r["close"], "buy_score": r["buy_score"],
        "state": r["state"], "breakout_ratio": r.get("bo_breakout_ratio"),
        "breakout_age": r.get("breakout_age"), "rsi": r.get("mom_rsi"),
        "liquidity": r["liquidity"], "momentum": r["momentum"], "signal": r.get("signal"),
    } for _, r in rec.iterrows()]
    n = db.record_tracked_signals(reco_date, rows)
    if n:
        _log(f"tracking: +{n} tín hiệu mới ghi nhận ({reco_date})")


def _update_outcomes():
    """Phase 3: refresh forward returns (T+1..T+5) for open tracked signals from the
    accumulating `ohlcv_daily`. Headline metric = T+3 (first sellable under T+2.5)."""
    from datetime import timedelta
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    updated = 0
    for symbol, reco_date, reco_close in db.open_tracked_signals(cutoff):
        closes = db.forward_closes(symbol, reco_date, 5)
        if not closes:
            continue
        # Entry basis = stored close of reco_date (same adjustment basis as the
        # forward closes) — NOT the live reco price (corporate-action safety).
        entry = db.close_on(symbol, reco_date) or reco_close
        db.upsert_outcome(symbol, reco_date, closes, entry)
        updated += 1
    if updated:
        _log(f"tracking: cập nhật outcome cho {updated} tín hiệu")


def _update_observation_outcomes():
    """Phase 4b: refresh T+3 outcomes for the whole-pool daily observations."""
    from datetime import timedelta
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    updated = 0
    for symbol, obs_date, close_ref in db.open_observations(cutoff):
        closes = db.forward_closes(symbol, obs_date, 5)
        if not closes:
            continue
        entry = db.close_on(symbol, obs_date) or close_ref   # adjustment-safe basis
        db.update_observation_outcome(symbol, obs_date, closes, entry)
        updated += 1
    if updated:
        _log(f"observations: cập nhật outcome cho {updated} mã pool")


def _milestone_reminders():
    """Nhắc MỘT LẦN qua Telegram khi dữ liệu live tích lũy đủ cho các việc đã hoãn
    (quyết định 19/07 — DEVELOPMENT #40). Cờ chống lặp lưu app_state."""
    import sqlite3
    milestones = [
        ("reminder_huong_A_drift_alarm",
         "SELECT COUNT(*) FROM tracked_signals t JOIN signal_outcomes o "
         "ON o.symbol=t.symbol AND o.reco_date=t.reco_date "
         "WHERE t.reco_date >= '2026-07-20' AND o.win_t3 IS NOT NULL",
         150,
         "🔔 Breakout Screener: đã đủ ≥150 tín hiệu live có kết quả T+3 (sau 20/07) — "
         "đến lúc triển khai HƯỚNG A: Drift Alarm (so rolling win-rate live với dải "
         "kỳ vọng backtest, báo động khi lệch). Nhắc Claude: 'triển khai drift alarm'."),
        ("reminder_huong_B_band_calib",
         "SELECT COUNT(*) FROM daily_observations WHERE foreign_net_pct IS NOT NULL "
         "AND win_t3 IS NOT NULL",
         15000,
         "🔔 Breakout Screener: daily_observations đã tích lũy ≥15,000 dòng có dữ liệu "
         "flow + kết quả T+3 — đủ để calibrate band INTRADAY/FLOW (Hướng B, phần backtest "
         "mù). Nhắc Claude: 'calibrate band intraday/flow từ dữ liệu live'."),
    ]
    try:
        c = sqlite3.connect(config.DB_PATH)
        for flag, sql, threshold, msg in milestones:
            if db.get_state(flag):
                continue
            n = c.execute(sql).fetchone()[0]
            if n >= threshold:
                db.set_state(flag, {"reached": str(date.today()), "count": int(n)})
                _log(f"MILESTONE: {flag} đạt ({n} ≥ {threshold}) — đã gửi nhắc")
                notify.send_telegram(msg + f"\n(hiện có {n:,} mẫu)")
        c.close()
    except Exception as e:
        _log(f"milestone check bỏ qua ({type(e).__name__})")


def _flow_pct(net: dict, hist: pd.DataFrame):
    """Spec 3.2.4.2 net-% = 5-session net value / 5-session turnover × 100."""
    if not net:
        return None
    g = hist["close"].iloc[-config.LOOKBACK_FLOW:] * hist["volume"].iloc[-config.LOOKBACK_FLOW:]
    gtgd_5d = float(g.sum())
    if not gtgd_5d:
        return None
    out = {}
    if net.get("foreign_net_5d") is not None:
        out["foreign_net_pct"] = net["foreign_net_5d"] / gtgd_5d * 100
    if net.get("prop_net_5d") is not None:
        out["prop_net_pct"] = net["prop_net_5d"] / gtgd_5d * 100
    return out or None


_REGIME_LABEL = {"ok": "🟢 UPTREND", "caution": "🟡 CAUTION", "blocked": "🔴 DOWNTREND"}


def _compute_market_health(ohlcv: dict, live_map: dict, vnindex_df, today_iso: str):
    """Điểm Sức khỏe thị trường (observe-only). Trả dict từ market_health.score_...

    - dist_days: từ VN-Index OHLCV (cần cột volume).
    - breadth: % mã pool có giá hiện tại > MA20 (lịch sử EOD + close live hôm nay).
    - canary: % tín hiệu KN 1-2 phiên gần nhất còn ≥ close ngày KN (giá live)."""
    import pandas as pd
    closes = {}
    for sym, df in ohlcv.items():
        live = live_map.get(sym) or {}
        cl = live.get("close")
        if df is None or df.empty or not cl:
            continue
        hist_close = df[df["time"].dt.strftime("%Y-%m-%d") < today_iso]["close"]
        closes[sym] = pd.concat([hist_close, pd.Series([float(cl)])], ignore_index=True)
    breadth = market_health.breadth_above_ma20(closes)

    dist = market_health.count_distribution_days(vnindex_df) if vnindex_df is not None else 0

    canary = None
    entries = db.recent_reco_entries(today_iso, n_days=2)
    ok = tot = 0
    for sym, rd in entries:
        live = live_map.get(sym) or {}
        cl = live.get("close")
        entry = db.close_on(sym, rd)
        if not cl or not entry:
            continue
        tot += 1
        if float(cl) >= float(entry):
            ok += 1
    if tot >= 3:                        # cần tối thiểu vài tín hiệu mới có nghĩa
        canary = ok / tot * 100

    ratio = float("nan")
    if vnindex_df is not None and len(vnindex_df) >= 20:
        ma20 = float(vnindex_df["close"].iloc[-20:].mean())
        if ma20:
            ratio = float(vnindex_df["close"].iloc[-1]) / ma20
    return market_health.score_market_health(dist, breadth, canary, ratio)


def _mh_mode(mh) -> str:
    """Chế độ đèn vàng theo điểm Sức khỏe thị trường (Phase 2, backtest-passed):
    normal / selective (chỉ mã BUY ≥ MH_GATE_STRONG_SCORE) / halt (ngừng KN mới)."""
    if not config.MH_GATE_ENABLED or not mh:
        return "normal"
    h = mh.get("health")
    if h is None:
        return "normal"
    if h < config.MH_GATE_HARD:
        return "halt"
    if h < config.MH_GATE_SOFT:
        return "selective"
    return "normal"


def _mh_pass(mode: str, buy_score: float) -> bool:
    """Một mã có qua được đèn vàng không (dùng chung cho alert + tracking + is_reco)."""
    if mode == "halt":
        return False
    if mode == "selective":
        return buy_score >= config.MH_GATE_STRONG_SCORE
    return True


def _alert_states(regime: str) -> list:
    """Các state được phép alert theo regime (backtest 10y + quyết định 15/07):
    LATE chỉ trong regime 'ok' — nơi duy nhất nó tỏa sáng; caution/blocked thì không."""
    states = ["BREAKOUT_FRESH", "PRE_BREAKOUT"]
    if config.ALERT_LATE_IN_OK_REGIME and regime == "ok":
        states.append("BREAKOUT_LATE")
    return states


def _select_top_diversified(df, top_n: int, max_per_sector: int):
    """Pick the top-N by score with at most ``max_per_sector`` per vi_sector (P7).

    Iterates in ranked order; a stock whose sector quota is full is skipped and its
    slot goes to the next-best stock from another sector. Stocks with unknown
    sector are never capped (can't distinguish them)."""
    picked, count = [], {}
    for idx, r in df.iterrows():
        sec = r.get("sector")
        if sec and count.get(sec, 0) >= max_per_sector:
            continue
        picked.append(idx)
        if sec:
            count[sec] = count.get(sec, 0) + 1
        if len(picked) >= top_n:
            break
    return df.loc[picked]


def _timing_note(r) -> str:
    """One-line RevD timing context for the alert (why it's actionable now)."""
    state = r.get("state")
    rsi = r.get("mom_rsi")
    rsi_txt = f" · RSI {rsi:.0f}" if rsi is not None and rsi == rsi else ""
    if state == "PRE_BREAKOUT":
        dist = r.get("setup_dist_below_pivot")
        d = f"cách đỉnh {dist:.1f}%" if dist is not None and dist == dist else "sát đỉnh"
        return f"↳ {d} — coiling, chờ vượt cản{rsi_txt}"
    if state in ("BREAKOUT_FRESH", "BREAKOUT_LATE"):
        age = r.get("breakout_age")
        ratio = r.get("bo_breakout_ratio")
        age_txt = f"vừa vượt hôm nay" if age == 0 else f"đã vượt {age} phiên trước"
        r_txt = f" (+{(ratio - 1) * 100:.1f}% trên đỉnh)" if ratio and ratio == ratio else ""
        warn = ("\n   ⚠️ <i>Muộn — momentum tiếp diễn: chỉ hợp lệ khi thị trường thuận; "
                "rủi ro khóa T+2.5 cao nhất, cân nhắc vị thế nhỏ + stop chặt</i>"
                if state == "BREAKOUT_LATE" else "")
        return f"↳ {age_txt}{r_txt}{rsi_txt}{warn}"
    return f"↳ {rsi_txt.lstrip(' ·')}" if rsi_txt else ""


def _format_alert(df, regime: str) -> str:
    ts = clock.now_vn().strftime("%H:%M %d/%m")
    lines = [f"🚀 <b>Breakout Screener</b> — {ts}",
             f"Thị trường: {_REGIME_LABEL.get(regime, regime)}", "",
             "<b>Top mã khuyến nghị mới (chưa gửi hôm nay):</b>"]
    for i, (_, r) in enumerate(df.iterrows(), 1):
        state = r.get("state_label", "")
        sector = r.get("sector")
        exch = f"{r['exchange']} · {sector}" if sector else r["exchange"]
        lines.append("")
        lines.append(f"{i}. {state} <b>{r['symbol']}</b> ({exch}) — "
                     f"BUY {r['buy_score']:.1f} ({r['rating']})")
        lines.append(f"   TK {r['liquidity']:.0f} · ĐL {r['momentum']:.0f} · "
                     f"Tín hiệu {r.get('signal', 0):.0f} · giá {r['close']:,.0f}")
        note = _timing_note(r)
        if note:
            lines.append(f"   {note}")
    lines.append("")
    lines.append("<i>🟢 Mua ngay = breakout mới · 🔵 Sắp breakout = vào sớm</i>")
    return "\n".join(lines)


def _alert_job():
    """Hourly Telegram push of the top new Layer-2 stocks (no repeats per day)."""
    try:
        _alert_job_inner()
    except Exception as e:
        _log(f"alert ERROR: {type(e).__name__}: {e}")


def _alert_job_inner():
    now = clock.now_vn()
    if now.weekday() >= 5:                                   # weekend
        return
    if not (config.ALERT_START_HOUR <= now.hour <= config.ALERT_END_HOUR):
        return
    if not notify.is_configured():
        _log("alert: Telegram chưa cấu hình (data/telegram_config.json) — bỏ qua")
        return

    s = store.get()
    ranked = s["ranked"]
    regime = s["regime"]
    mh = s.get("market_health") or {}
    mh_mode = _mh_mode(mh)
    if mh_mode == "halt":
        _log(f"alert: Sức khỏe TT {mh.get('health')}/100 < {config.MH_GATE_HARD} "
             "— tạm ngừng khuyến nghị mới (đèn vàng Phase 2)")
        return
    if ranked is None or ranked.empty:
        _log("alert: chưa có kết quả Layer 2 — bỏ qua")
        return

    today = date.today().isoformat()
    sent = db.already_sent(today)
    # RevD: alert FRESH + PRE luôn; LATE chỉ khi regime 'ok' (_alert_states). Take the
    # top-N by score FIRST — with the P7 sector cap so one hot sector can't fill the
    # whole list — then drop any already sent today (don't back-fill with weaker names).
    actionable = ranked[ranked.get("state", "").isin(_alert_states(regime))] \
        if "state" in ranked.columns else ranked
    min_buy = (config.MH_GATE_STRONG_SCORE if mh_mode == "selective"
               else config.ALERT_MIN_SCORE)
    eligible = actionable[actionable["buy_score"] >= min_buy]
    top = _select_top_diversified(eligible, config.ALERT_TOP_N, config.ALERT_MAX_PER_SECTOR)
    fresh = top[~top["symbol"].isin(sent)]
    if fresh.empty:
        _log("alert: top-N đều đã gửi hoặc dưới ngưỡng — bỏ qua")
        return

    msg = _format_alert(fresh, regime)
    if mh_mode == "selective":
        msg += (f"\n<i>⚕️ Sức khỏe TT {mh.get('health')}/100 — chế độ CHỌN LỌC: "
                f"chỉ gửi mã BUY ≥ {config.MH_GATE_STRONG_SCORE:.0f}</i>")
    if not notify.send_telegram(msg):
        _log("alert: gửi Telegram thất bại (kiểm tra token/chat_id)")
        return
    db.mark_sent(today, list(zip(fresh["symbol"], fresh["buy_score"])))
    _log(f"alert sent: {fresh['symbol'].tolist()}")


# ── Scheduled jobs ───────────────────────────────────────────────────────────────
def _intraday_job():
    if clock.is_trading_hours():
        try:
            run_full_scan()
        except Exception:
            pass


def _morning_warmup():
    """Once/day: rebuild data + static Layer-1 pool (heavy), then publish a scan so
    the dashboard shows the morning Layer-1 screen. Also refresh tracking outcomes
    now that yesterday's EOD close is available."""
    try:
        s = store.get_settings()
        _log("morning warm-up: building static Layer-1 pool…")
        ensure_history(s.get("exchanges"), min_gtgd20=s.get("min_gtgd20"),
                       min_price=s.get("min_price"), force=True)
        run_full_scan()  # publish Layer-1 result for the day
        _update_outcomes()
        _update_observation_outcomes()
    except Exception:
        pass


def _eod_job():
    """15:30: final scan (upserts EOD close + snapshots the whole pool), update T+3
    outcomes (recommended + full pool), then auto-tune W_BUY from the full pool."""
    try:
        run_full_scan(record_obs=True)     # snapshot whole Layer-1 pool at EOD
        _update_outcomes()                 # recommended signals (tracked_signals)
        _update_observation_outcomes()     # whole pool (daily_observations)
        # (Auto-learner W_BUY đã KHAI TỬ 19/07 — tiền đề bị bác bởi chiến dịch
        #  calibrate 233k mẫu: mặt objective phẳng quanh trọng số hiện tại, learner
        #  nhỏ giọt chỉ có thể học nhiễu. Xem DEVELOPMENT #37/#38/#40.)
        _milestone_reminders()
    except Exception:
        pass


def start_scheduler(initial_scan: bool = True):
    """Launch the background schedule loop in a daemon thread.

    08:00 — heavy data fetch + static Layer-1 pool, then a scan (once/day).
    Every 5 min (09:15–14:45) — live filters (#6/#7) + Layer-2 scoring.
    15:30 — final EOD scan.
    On startup — one immediate scan so the dashboard is never stale/empty.
    """
    schedule.every().day.at("08:00").do(_morning_warmup)
    schedule.every(5).minutes.do(_intraday_job)
    schedule.every().day.at("15:30").do(_eod_job)
    # Hourly Telegram alert of top new Layer-2 stocks, ALERT_START_HOUR..END_HOUR.
    for h in range(config.ALERT_START_HOUR, config.ALERT_END_HOUR + 1):
        schedule.every().day.at(f"{h:02d}:00").do(_alert_job)

    def _loop():
        if initial_scan:
            try:
                _log("startup scan…")
                run_full_scan(force_history=True)
            except Exception:
                pass
        while True:
            # One misbehaving job must NEVER kill the scheduler thread: an uncaught
            # exception here stops ALL future jobs while the web UI keeps serving
            # stale data (zombie state observed 10-11/07/2026).
            try:
                schedule.run_pending()
            except Exception as e:
                _log(f"scheduler ERROR (job bị bỏ qua, vòng lặp vẫn chạy): "
                     f"{type(e).__name__}: {e}")
            time.sleep(20)

    t = threading.Thread(target=_loop, daemon=True, name="screener-scheduler")
    t.start()
    return t
