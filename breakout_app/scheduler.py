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
_last_live = {}      # snapshot price-board gần nhất {sym: {close, volume}} (screen dòng tiền EOD)


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
        # 1 call rẻ mỗi 5 phút; lỗi mạng → dùng cache cũ. days=300 để FTD detector
        # có đỉnh chạy dài hơn (study dùng 250 phiên).
        try:
            vn_fresh = fetchers.fetch_vnindex(days=300)
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
        # Snapshot live gần nhất cho screen dòng tiền EOD (ohlcv_daily chỉ có nến
        # hôm nay vào warmup sáng HÔM SAU — không đọc được volume hôm nay từ db).
        global _last_live
        _last_live = {s: {"close": v.get("close"), "volume": v.get("volume")}
                      for s, v in live_map.items()}
        minutes = clock.minutes_elapsed()

        # Kênh quan sát dòng tiền NN live — TÍNH mỗi scan (gửi thuộc job giờ)
        try:
            _sm_live_scan(live_map, minutes)
        except Exception as e:
            _log(f"sm-live: bỏ qua ({type(e).__name__}: {e})")

        today = date.today()

        # Market Health TRƯỚC vòng chấm điểm (Phase 2): mode gate ảnh hưởng is_reco/
        # record; điểm vẫn publish ở store như cũ.
        try:
            mh = _compute_market_health(ohlcv, live_map, vnindex_full, today.isoformat())
        except Exception as e:
            mh = None
            _log(f"market health: bỏ qua ({type(e).__name__}: {e})")
        mh_mode = _mh_mode(mh)

        # FTD observe-only (05/08): chỉ hiển thị + log + Telegram 1 lần/sự kiện,
        # KHÔNG đụng gate/alert — chờ 3-5 FTD live rồi mới quyết (analysis/ftd_study.py).
        ftd_info = None
        try:
            from engine import ftd as ftd_mod
            ftd_info = ftd_mod.ftd_state(vnindex_full)
            if ftd_info and ftd_info.get("ftd"):
                f = ftd_info["ftd"]
                _log(f"FTD 🔔 {f['date']} (ngày rally {f['day_no']}, +{f['gain']}%) — "
                     f"cửa sổ đang mở (observe-only)")
                flag = f"ftd_notified_{f['date']}"
                if not db.get_state(flag):
                    db.set_state(flag, "1")
                    notify.send_telegram(
                        f"🔔 <b>Follow-Through Day</b> (quan sát — không phải khuyến nghị)\n"
                        f"VN-Index có FTD ngày {f['date']} (ngày rally thứ {f['day_no']}, "
                        f"+{f['gain']}% trên volume cao hơn) — lịch sử 10 năm: tín hiệu "
                        f"breakout ngay sau FTD tốt hơn baseline nhưng mẫu mỏng; app vẫn "
                        f"chờ regime/health mở như thường lệ.")
        except Exception as e:
            _log(f"ftd: bỏ qua ({type(e).__name__}: {e})")

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
                res["flow_fr_daily"] = fl.get("foreign_daily_5")   # từng phiên, cho alert
                res["flow_pr_daily"] = fl.get("prop_daily_5")
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

        # Confluence dòng tiền ngoại cho alert breakout (backtest #52: "vượt đỉnh +
        # smart money" là bucket cân bằng nhất) — NN live từ price board, chiếu cả
        # phiên trên nền GTGD5; chỉ tính khi phiên đã trôi đủ để phép chiếu có nghĩa.
        if results and minutes >= 30:
            try:
                sm_base = _sm_base(today.isoformat())
                tr = min(1.0, minutes / 225.0)
                for res in results:
                    b = sm_base.get(res["symbol"])
                    lv = live_map.get(res["symbol"]) or {}
                    fb, fs, cl = (lv.get("foreign_buy_volume"),
                                  lv.get("foreign_sell_volume"), lv.get("close"))
                    if b and cl and fb is not None and fs is not None:
                        res["foreign_live_pct"] = round((fb - fs) * cl / tr / b[1] * 100, 1)
            except Exception as e:
                _log(f"foreign-confluence: bỏ qua ({type(e).__name__}: {e})")

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
        if ftd_info is not None:
            store.update(ftd=ftd_info)
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


def _mcdx_banker(closes) -> float:
    """MCDX Banker (Mango2Juice): clamp(1.5 × (RSI50 − 50), 0, 20). RSI theo Wilder
    RMA như Pine ta.rsi (seed SMA n, rồi (prev·(n−1)+x)/n). Thuần giá — KHÔNG phải
    dòng tiền (study #57) nhưng là thước đà trung hạn; users quen dùng nên hiển thị."""
    n = 50
    c = [float(x) for x in closes if x is not None and x == x]
    if len(c) < n + 2:
        return None
    ups, dns = [], []
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        ups.append(max(d, 0.0)); dns.append(max(-d, 0.0))
    au, ad = sum(ups[:n]) / n, sum(dns[:n]) / n
    for u, dd in zip(ups[n:], dns[n:]):
        au = (au * (n - 1) + u) / n
        ad = (ad * (n - 1) + dd) / n
    rsi = 100.0 if ad == 0 else 100 - 100 / (1 + au / ad)
    return round(max(0.0, min(20.0, 1.5 * (rsi - 50))), 2)


def _long_closes(con, sym: str, today_iso: str, n: int = 300) -> list:
    """Close TRƯỚC hôm nay, cũ→mới, ≥ n bars, trên MỘT hệ điều chỉnh giá.

    Bẫy dữ liệu (phát hiện 22/08, HCM −13.7% sau sự kiện quyền giữa 07/2026): provider
    hồi tố giá khi có chia tách/quyền, nên (a) kho data/history (fetch 15/07) và (b) các
    dòng ohlcv_daily cũ hơn cửa sổ refresh 100 ngày còn ở HỆ CŨ, trong khi dòng db gần
    đây là hệ mới → ghép thô tạo bậc nhảy giả, RSI50 sai. Cách làm: chỉ lấy db trong
    cửa sổ refresh (hệ hiện tại), rồi ghép history đã RESCALE theo hệ số trùng khớp
    (median db/hist trên các ngày chung). RSI bất biến theo tỷ lệ nên chuỗi ghép sạch.
    Cần dài vì RSI50 kiểu Wilder warm-up chậm (70 bars lệch TV ~3 điểm Banker)."""
    from datetime import timedelta
    win_lo = (date.today() - timedelta(days=config.FETCH_DAYS + 5)).isoformat()
    rows = con.execute("SELECT date, close FROM ohlcv_daily WHERE symbol=? AND date<? "
                       "AND date>=? ORDER BY date", (sym, today_iso, win_lo)).fetchall()
    if not rows:
        return []
    db_map = {d: c for d, c in rows}
    if len(rows) >= n:
        return [c for _, c in rows[-n:]]
    try:
        import os
        hp = os.path.join(config.DATA_DIR, "history", f"{sym}.parquet")
        if os.path.exists(hp):
            h = pd.read_parquet(hp, columns=["time", "close"])
            h["d"] = pd.to_datetime(h["time"]).dt.strftime("%Y-%m-%d")
            h = h.sort_values("d")
            common = h[h["d"].isin(db_map)]
            factor = 1.0
            if len(common) >= 5:
                ratios = [db_map[d] / c for d, c in zip(common["d"], common["close"]) if c]
                ratios.sort()
                factor = ratios[len(ratios) // 2]          # median, bền với 1-2 ngày lỗi
            older = h[h["d"] < rows[0][0]]["close"].tolist()
            need = n - len(rows)
            return [float(x) * factor for x in older[-need:]] + [c for _, c in rows]
    except Exception:
        pass
    return [c for _, c in rows]


def _sm_base(today_iso: str) -> dict:
    """Nền lịch sử cho screen dòng tiền {sym: (vol_ma20, gtgd5, closes_prior)} —
    cache 1 lần/ngày; closes_prior (≤120 phiên, cũ→mới) dùng tính MCDX Banker."""
    with _hist_lock:
        cached = _hist.get("sm_base")
        if cached and cached[0] == today_iso:
            return cached[1]
        pool = [s for s, v in (_hist.get("static_pool") or {}).items() if v.get("passed")]
    import sqlite3
    con = sqlite3.connect(config.DB_PATH)
    base = {}
    for sym in pool:
        prior = con.execute(
            "SELECT close, volume FROM ohlcv_daily WHERE symbol=? AND date<? "
            "ORDER BY date DESC LIMIT 20", (sym, today_iso)).fetchall()
        if len(prior) < 20:
            continue
        vol_ma20 = sum(r[1] for r in prior[:20]) / 20
        gtgd5 = sum(r[0] * r[1] for r in prior[:5]) / 5
        if vol_ma20 and gtgd5:
            base[sym] = (vol_ma20, gtgd5, _long_closes(con, sym, today_iso))
    con.close()
    with _hist_lock:
        _hist["sm_base"] = (today_iso, base)
    return base


def _sm_live_scan(live_map: dict, minutes: float):
    """TÍNH dòng tiền NN live MỖI SCAN 5' (như Layer-2) → store cho tab 💰.

    Chuẩn hóa "so với cùng khoảng thời gian" (time_ratio): volume lũy kế / (MA20 ×
    tỷ lệ phiên đã trôi); NN cũng chiếu theo cùng cách. Tự doanh không có intraday.
    KHÔNG gửi Telegram ở đây — việc gửi thuộc job giờ (_sm_hourly_alert)."""
    if not (config.SM_SCREEN_ENABLED and config.SM_INTRADAY_ENABLED):
        return
    if date.today().weekday() >= 5 or minutes < 15:
        return
    today_iso = date.today().isoformat()
    base = _sm_base(today_iso)
    if not base:
        return
    tr = min(1.0, max(minutes, 1.0) / 225.0)
    with _hist_lock:
        flow5 = dict(_hist.get("flow") or {})      # 5 phiên trước (warmup sáng)
    rows = []
    for sym, (vol_ma20, gtgd5, closes_prior) in base.items():
        lv = live_map.get(sym) or {}
        close, vol = lv.get("close"), lv.get("volume")
        fb, fs = lv.get("foreign_buy_volume"), lv.get("foreign_sell_volume")
        if not close or not vol or fb is None or fs is None:
            continue
        vol_ratio = vol / tr / vol_ma20            # so với cùng khoảng thời gian
        fr_net = (fb - fs) * close                 # ≈ ròng lũy kế (giá hiện tại)
        fr_pct_proj = fr_net / tr / gtgd5 * 100    # chiếu cả phiên, cùng chuẩn
        banker = _mcdx_banker(closes_prior + [close]) if config.SM_MCDX_ENABLED else None
        qualifies = (vol_ratio >= config.SM_VOL_RATIO_MIN
                     and fr_pct_proj >= config.SM_FOREIGN_PCT_MIN)
        qualifies_mcdx = (config.SM_MCDX_ENABLED and banker is not None
                          and vol_ratio >= config.SM_VOL_RATIO_MIN
                          and banker > config.SM_MCDX_BANKER_MIN)
        if vol_ratio >= config.SM_VOL_RATIO_MIN or fr_pct_proj >= config.SM_FOREIGN_PCT_MIN:
            f5 = flow5.get(sym) or {}
            fn5, pn5 = f5.get("foreign_net_5d"), f5.get("prop_net_5d")
            rows.append({"symbol": sym, "close": close, "vol_ratio": round(vol_ratio, 2),
                         "foreign_net": fr_net, "foreign_pct_proj": round(fr_pct_proj, 1),
                         "qualifies": qualifies, "banker": banker,
                         "qualifies_mcdx": qualifies_mcdx,
                         "foreign_net_5d": fn5, "prop_net_5d": pn5,
                         "fr_daily": f5.get("foreign_daily_5"), "pr_daily": f5.get("prop_daily_5"),
                         "fr5_pct": (fn5 / (gtgd5 * 5) * 100) if fn5 is not None else None,
                         "pr5_pct": (pn5 / (gtgd5 * 5) * 100) if pn5 is not None else None})
    rows.sort(key=lambda r: -r["foreign_pct_proj"])
    store.update(smart_money_live={"ts": clock.now_vn().strftime("%H:%M:%S"),
                                   "minutes": round(minutes), "rows": rows[:30]})


def _sm_hourly_alert():
    """GỬI Telegram dòng tiền NN theo GIỜ (như kênh breakout): mã đạt CẢ volume lẫn
    NN (đã chuẩn hóa cùng-khoảng-thời-gian), mỗi mã 1 lần/ngày. Tự doanh: bản EOD."""
    try:
        if not (config.SM_SCREEN_ENABLED and config.SM_INTRADAY_ENABLED):
            return
        now = clock.now_vn()
        if now.weekday() >= 5:
            return
        s = store.get().get("smart_money_live") or {}
        rows = s.get("rows") or []
        if s.get("minutes", 0) < config.SM_INTRADAY_MIN_MINUTES:
            return
        today_iso = date.today().isoformat()
        sent = db.sm_intraday_sent(today_iso)
        hits = [r for r in rows if r["qualifies"] and r["symbol"] not in sent]
        hits_mcdx = [r for r in rows if r.get("qualifies_mcdx")
                     and f"{r['symbol']}#mcdx" not in sent and r["symbol"] not in sent]
        if not hits and not hits_mcdx:
            return
        lines = [f"💰 <b>Dòng tiền trong phiên</b> — {now.strftime('%H:%M %d/%m')} (chưa nhắn hôm nay)",
                 "<i>Quan sát — KHÔNG phải khuyến nghị. Volume chuẩn hóa theo cùng khoảng "
                 "thời gian phiên; tự doanh chỉ có ở bản tin EOD 15:30.</i>"]
        def _bk(r):
            b = r.get("banker")
            return f" · MCDX Banker {b:.0f}/20" if b is not None else ""
        if hits:
            lines += ["", "<b>Khối ngoại đang gom</b> (volume + khối ngoại ≥ ngưỡng):"]
            for r in hits[:config.SM_TOP_N]:
                lines.append(f"• <b>{r['symbol']}</b> — giá {r['close']:,.0f} · "
                             f"vol {r['vol_ratio']:.1f}× cùng giờ · "
                             f"Khối ngoại ≈{r['foreign_net']/1e9:+.1f} tỷ "
                             f"(chiếu {r['foreign_pct_proj']:+.1f}%){_bk(r)}")
                f5 = _flow5_line(r.get("fr_daily"), r.get("pr_daily"),
                                 r.get("fr5_pct"), r.get("pr5_pct"), prefix="  ")
                if f5:
                    lines.append(f5.lstrip("\n"))
        only_mcdx = [r for r in hits_mcdx if r not in hits]
        if only_mcdx:
            lines += ["", f"📕 <b>MCDX</b> — volume tăng + Banker &gt; {config.SM_MCDX_BANKER_MIN:.0f}:"]
            for r in only_mcdx[:config.SM_TOP_N]:
                lines.append(f"• <b>{r['symbol']}</b> — giá {r['close']:,.0f} · "
                             f"vol {r['vol_ratio']:.1f}× cùng giờ · "
                             f"MCDX Banker <b>{r['banker']:.0f}/20</b> · "
                             f"Khối ngoại ≈{r['foreign_net']/1e9:+.1f} tỷ "
                             f"(chiếu {r['foreign_pct_proj']:+.1f}%)")
        notify.send_telegram("\n".join(lines))
        db.mark_sm_intraday(today_iso, [r["symbol"] for r in hits]
                            + [f"{r['symbol']}#mcdx" for r in only_mcdx])
        _log(f"sm-hourly: nhắn {len(hits)} mã dòng tiền + {len(only_mcdx)} mã MCDX")
    except Exception as e:
        _log(f"sm-hourly ERROR: {type(e).__name__}: {e}")


def _smart_money_screen(final_for: str = None) -> bool:
    """Screen 'Dòng tiền thông minh' — kênh quan sát, KHÔNG khuyến nghị. Hai chế độ:

    • EOD-live (15:30, final_for=None): volume & KHỐI NGOẠI cả phiên lấy từ price board
      (live, chính xác tới 15:30); TỰ DOANH chưa có (VCI công bố số ngày D muộn — kiểm
      21/08: 23:00 vẫn chưa có) → ghi '—'. Lưu db (ngày D) + Telegram.
    • Sáng D+1 (final_for=D, gọi từ _sm_morning_final): bản ĐẦY ĐỦ cho phiên D với khối
      ngoại + tự doanh chính thức từ API → GHI ĐÈ dòng D trong db, Telegram bản đầy đủ.
      Trả False nếu API chưa có số ngày D (caller thử lại sau).
    Phát hiện bug 21/08: bản 15:30 cũ lấy NN/TD từ API `_1d` → None toàn bộ → không lưu,
    không nhắn, không log. Giờ mỗi nhánh thoát sớm đều log lý do."""
    if not config.SM_SCREEN_ENABLED:
        return True
    import sqlite3
    today_iso = date.today().isoformat()
    ref_iso = final_for or today_iso
    mode = "final" if final_for else "eod"
    with _hist_lock:
        pool = [s for s, v in (_hist.get("static_pool") or {}).items() if v.get("passed")]
    live = dict(_last_live)
    if not pool:
        _log(f"smart-money[{mode}]: bỏ qua — chưa có static_pool"); return False
    if mode == "eod":
        if date.today().weekday() >= 5:
            return True
        if not live:
            _log("smart-money[eod]: bỏ qua — chưa có snapshot price board"); return False
    con = sqlite3.connect(config.DB_PATH)
    cand = {}
    for sym in pool:
        if mode == "eod":
            lv = live.get(sym) or {}
            close_t, vol_t = lv.get("close"), lv.get("volume")
            fb, fs = lv.get("foreign_buy_volume"), lv.get("foreign_sell_volume")
            fr_live = (fb - fs) * close_t if (fb is not None and fs is not None and close_t) else None
        else:
            row = con.execute("SELECT close, volume FROM ohlcv_daily WHERE symbol=? AND date=?",
                              (sym, ref_iso)).fetchone()
            if not row:
                continue
            close_t, vol_t, fr_live = row[0], row[1], None
        if not close_t or not vol_t:
            continue
        prior = con.execute(
            "SELECT close, volume FROM ohlcv_daily WHERE symbol=? AND date<? "
            "ORDER BY date DESC LIMIT 20", (sym, ref_iso)).fetchall()
        if len(prior) < 20:
            continue
        vol_ma20 = sum(r[1] for r in prior) / 20
        gtgd5 = sum(r[0] * r[1] for r in prior[:5]) / 5
        if not vol_ma20 or not gtgd5:
            continue
        vr = vol_t / vol_ma20
        if vr >= config.SM_VOL_RATIO_MIN:
            closes_prior = _long_closes(con, sym, ref_iso)
            cand[sym] = {"close": close_t, "vol_ratio": vr, "gtgd5": gtgd5, "fr_live": fr_live,
                         "banker": (_mcdx_banker(closes_prior + [close_t])
                                    if config.SM_MCDX_ENABLED else None)}
    reco_on = {r[0] for r in con.execute(
        "SELECT symbol FROM tracked_signals WHERE reco_date=?", (ref_iso,))}
    con.close()
    if not cand:
        _log(f"smart-money[{mode}] {ref_iso}: không mã nào đạt điều kiện volume"); return True
    ref_date = date.fromisoformat(ref_iso)
    flow = fetchers.fetch_flow_per_stock(sorted(cand), on_date=(ref_date if mode == "final" else None))
    if mode == "final" and not any((flow.get(s) or {}).get("foreign_net_1d") is not None for s in cand):
        _log(f"smart-money[final] {ref_iso}: API chưa công bố NN/TD ngày {ref_iso} — thử lại sau")
        return False
    rows, rows_mcdx = [], []
    for sym, m in cand.items():
        fl = flow.get(sym) or {}
        if mode == "eod":
            fn = m["fr_live"]                       # khối ngoại cả phiên từ bảng giá
            pn = None                               # tự doanh: sáng mai
        else:
            fn, pn = fl.get("foreign_net_1d"), fl.get("prop_net_1d")
        fp = fn / m["gtgd5"] * 100 if fn is not None else None
        pp = pn / m["gtgd5"] * 100 if pn is not None else None
        flow_ok = ((fp is not None and fp >= config.SM_FOREIGN_PCT_MIN)
                   or (pp is not None and pp >= config.SM_PROP_PCT_MIN))
        mcdx_ok = (config.SM_MCDX_ENABLED and m.get("banker") is not None
                   and m["banker"] > config.SM_MCDX_BANKER_MIN)
        if flow_ok or mcdx_ok:
            fn5, pn5 = fl.get("foreign_net_5d"), fl.get("prop_net_5d")
            (rows if flow_ok else rows_mcdx).append(
                        {"symbol": sym, "close": m["close"], "vol_ratio": m["vol_ratio"],
                         "foreign_net": fn, "foreign_pct": fp, "prop_net": pn,
                         "prop_pct": pp, "is_breakout_reco": int(sym in reco_on),
                         "banker": m.get("banker"),
                         "foreign_net_5d": fn5, "prop_net_5d": pn5,
                         "fr_daily": fl.get("foreign_daily_5"), "pr_daily": fl.get("prop_daily_5"),
                         "fr5_pct": (fn5 / (m["gtgd5"] * 5) * 100) if fn5 is not None else None,
                         "pr5_pct": (pn5 / (m["gtgd5"] * 5) * 100) if pn5 is not None else None})
    rows.sort(key=lambda r: -max(r["foreign_pct"] or -999, r["prop_pct"] or -999))
    rows = rows[:config.SM_TOP_N]
    rows_mcdx.sort(key=lambda r: -(r["banker"] or 0))
    rows_mcdx = rows_mcdx[:config.SM_TOP_N]
    db.save_smart_money_screen(ref_iso, rows + rows_mcdx)
    _log(f"smart-money[{mode}] {ref_iso}: {len(rows)} mã dòng tiền + {len(rows_mcdx)} mã MCDX "
         f"(pool volume-ok: {len(cand)})")
    if mode == "final":
        db.set_state(f"sm_final_{ref_iso}", "1")
    if not rows and not rows_mcdx:
        return True
    def _b(v):
        return f"{v/1e9:+.1f} tỷ" if v is not None else "—"
    def _p(v):
        return f"{v:+.1f}%" if v is not None else "—"
    def _bk(r):
        b = r.get("banker")
        return f" · MCDX Banker {b:.0f}/20" if b is not None else ""
    d_lbl = ref_date.strftime("%d/%m")
    if mode == "eod":
        lines = [f"💰 <b>Dòng tiền thông minh</b> — EOD {d_lbl}",
                 "<i>Quan sát — KHÔNG phải khuyến nghị. Volume ≥1.5× TB20 + khối ngoại gom "
                 "mạnh (số cả phiên từ bảng giá). Tự doanh: VCI công bố sáng mai → bản đầy "
                 "đủ gửi lúc đó.</i>", ""]
    else:
        lines = [f"💰 <b>Dòng tiền thông minh — phiên {d_lbl} (BẢN ĐẦY ĐỦ)</b>",
                 "<i>Số chính thức khối ngoại + tự doanh vừa công bố. Quan sát — KHÔNG phải "
                 "khuyến nghị; nhắc: mua theo hôm sau mất phần lớn edge T+3 (backtest).</i>", ""]
    for i, r in enumerate(rows, 1):
        tag = " 🚀" if r["is_breakout_reco"] else ""
        lines.append(f"{i}. <b>{r['symbol']}</b>{tag} — giá {r['close']:,.0f} · "
                     f"vol {r['vol_ratio']:.1f}×{_bk(r)}")
        lines.append(f"   Khối ngoại {_b(r['foreign_net'])} ({_p(r['foreign_pct'])}) · "
                     f"Tự doanh {_b(r['prop_net'])} ({_p(r['prop_pct'])})")
        f5 = _flow5_line(r.get("fr_daily"), r.get("pr_daily"),
                         r.get("fr5_pct"), r.get("pr5_pct"))
        if f5:
            lines.append(f5.lstrip(chr(10)))
    if rows_mcdx:
        lines += ["", f"📕 <b>MCDX</b> — volume tăng + Banker &gt; {config.SM_MCDX_BANKER_MIN:.0f} "
                      "<i>(dòng tiền dưới ngưỡng):</i>"]
        for r in rows_mcdx:
            tag = " 🚀" if r["is_breakout_reco"] else ""
            lines.append(f"• <b>{r['symbol']}</b>{tag} — giá {r['close']:,.0f} · "
                         f"vol {r['vol_ratio']:.1f}× · MCDX Banker <b>{r['banker']:.0f}/20</b> · "
                         f"Khối ngoại {_b(r['foreign_net'])} ({_p(r['foreign_pct'])}) · "
                         f"Tự doanh {_b(r['prop_net'])} ({_p(r['prop_pct'])})")
    lines.append("")
    lines.append("<i>% = ròng / nền GTGD 5 phiên · 🚀 = cùng ngày được kênh breakout KN</i>")
    notify.send_telegram(chr(10).join(lines))
    return True


def _sm_morning_final():
    """Sáng D+1 (08:45 & 11:45 thử lại): bản ĐẦY ĐỦ dòng tiền cho phiên gần nhất D
    (VCI công bố NN/TD ngày D muộn, không kịp 15:30). Chạy 1 lần/D (cờ app_state)."""
    try:
        # KHÔNG guard cuối tuần: số phiên thứ Sáu công bố sáng thứ Bảy (phát hiện 22/08);
        # D = phiên gần nhất < hôm nay, cờ sm_final_D chống lặp nên Chủ nhật tự bỏ qua.
        if not config.SM_SCREEN_ENABLED:
            return
        import sqlite3
        con = sqlite3.connect(config.DB_PATH)
        row = con.execute("SELECT MAX(date) FROM ohlcv_daily WHERE date<?",
                          (date.today().isoformat(),)).fetchone()
        con.close()
        if not row or not row[0]:
            return
        d = row[0]
        if db.get_state(f"sm_final_{d}"):
            return
        ok = _smart_money_screen(final_for=d)
        _log(f"sm-morning-final {d}: {'xong' if ok else 'chưa có dữ liệu, sẽ thử lại'}")
    except Exception as e:
        _log(f"sm-morning-final ERROR: {type(e).__name__}: {e}")


def _latest_pypi(pkg: str):
    """Version mới nhất trên PyPI (None nếu lỗi mạng)."""
    import json as _json
    import urllib.request
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=15) as r:
            return _json.load(r)["info"]["version"]
    except Exception:
        return None


def _latest_vnstocks_index(pkg: str):
    """Version mới nhất trên kho riêng vnstocks.com (PEP503 simple index)."""
    import re as _re
    import urllib.request
    try:
        with urllib.request.urlopen(f"https://vnstocks.com/api/simple/{pkg}/", timeout=15) as r:
            html = r.read().decode("utf-8", "replace")
        vers = _re.findall(rf"{pkg.replace('-', '[-_]')}-(\d+(?:\.\d+)+)", html)
        if not vers:
            return None
        return max(vers, key=lambda v: tuple(int(x) for x in v.split(".")))
    except Exception:
        return None


def _lib_update_check():
    """Thứ Hai hàng tuần: so version thư viện đang cài với bản mới nhất → Telegram
    MỘT LẦN cho mỗi tổ hợp mới. CHỈ NHẮC, không bao giờ tự cập nhật (version ghim
    là chủ đích — nâng cấp qua requirements.txt/push hoặc sponsored_install.py)."""
    try:
        if not config.LIB_UPDATE_CHECK_ENABLED:
            return
        from importlib.metadata import version as _v
        checks = []
        # vnstock_data không có trên index pip (chỉ tarball qua installer) → check
        # trả None và bị bỏ qua; bản mới của nó tự đến khi chạy sponsored_install.py.
        for pkg, fetch in (("vnstock", _latest_pypi),
                           ("vnstock_data", _latest_vnstocks_index),
                           ("vnii", _latest_vnstocks_index)):
            try:
                cur = _v(pkg)
            except Exception:
                continue
            latest = fetch(pkg)
            if latest and latest != cur:
                try:
                    newer = (tuple(int(x) for x in latest.split("."))
                             > tuple(int(x) for x in cur.split(".")))
                except ValueError:
                    newer = True
                if newer:
                    checks.append((pkg, cur, latest))
        if not checks:
            _log("lib-update: tất cả thư viện đang ở bản mới nhất")
            return
        sig = ";".join(f"{p}:{l}" for p, _, l in checks)
        if db.get_state("lib_update_notified") == sig:
            return                                   # tổ hợp này đã nhắc rồi
        lines = ["🔄 <b>Có bản cập nhật thư viện vnstock</b> (app KHÔNG tự cập nhật)", ""]
        for pkg, cur, latest in checks:
            lines.append(f"• {pkg}: {cur} → <b>{latest}</b>")
        lines += ["", "<i>Nâng cấp có chủ đích: gói public sửa requirements.txt rồi push "
                      "(auto-deploy lo phần còn lại); gói sponsored chạy sponsored_install.py "
                      "trên server. Nên thử ở máy dev + chạy test trước khi push.</i>"]
        notify.send_telegram(chr(10).join(lines))
        db.set_state("lib_update_notified", sig)
        _log(f"lib-update: đã nhắc {sig}")
    except Exception as e:
        _log(f"lib-update ERROR: {type(e).__name__}: {e}")


def _refresh_dividends():
    """Nạp lịch cổ tức tiền mặt MỘT LẦN/NGÀY (events() trả cả lịch sử nên đủ).

    Cần cho phép đo outcome: VCI không hồi tố cổ tức tiền mặt vào giá, nên ret_t*
    phải cộng lại cổ tức khi lệnh đi qua ngày GDKHQ (FIX-cash-dividend-returns.md).
    Universe + mọi mã đang có tín hiệu/quan sát mở (mã có thể đã rời universe)."""
    today_iso = date.today().isoformat()
    if db.get_state("dividend_calendar_date") == today_iso:
        return
    from datetime import timedelta
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    with _hist_lock:
        symbols = {s for s in _hist.get("ohlcv", {})}
    symbols |= {s for s, *_ in db.open_tracked_signals(cutoff)}
    symbols |= {s for s, *_ in db.open_observations(cutoff)}
    if not symbols:
        return
    try:
        rows = fetchers.fetch_cash_dividends(sorted(symbols))
        n = db.upsert_cash_dividends(rows)
        db.set_state("dividend_calendar_date", today_iso)
        _log(f"dividend calendar: {n} dòng DIV cho {len(symbols)} mã")
    except Exception as e:
        _log(f"dividend calendar lỗi ({type(e).__name__}) — thử lại ngày mai")


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


def _update_sm_outcomes():
    """Đo T+1..T+5 cho các mã lọt screen Dòng tiền (đánh giá hiệu quả kênh).

    Tái dùng signal_outcomes — outcome là hàm của (symbol, ngày), trùng mã-ngày với
    kênh breakout thì kết quả y hệt (idempotent). Các JOIN của kênh breakout đều đi
    từ tracked_signals nên không bị lẫn dòng SM."""
    from datetime import timedelta
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    updated = 0
    for symbol, d, close_ref in db.open_smart_money(cutoff):
        closes = db.forward_closes(symbol, d, 5)
        if not closes:
            continue
        entry = db.close_on(symbol, d) or close_ref
        db.upsert_outcome(symbol, d, closes, entry)
        updated += 1
    if updated:
        _log(f"sm-tracking: cập nhật outcome cho {updated} mã dòng tiền")


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


def _runup_note(r) -> str:
    """Cảnh báo minh bạch khi mã đã chạy dài 5 phiên trước tín hiệu (05/08).

    Backtest 12,611 tín hiệu: kỳ vọng KHÔNG giảm theo run-up (không chặn/hạ điểm)
    nhưng MAE sâu dần đơn điệu — user tự cân size theo khẩu vị rung lắc T+2.5."""
    run5 = r.get("mom_return_5d")
    if run5 is None or run5 != run5:
        return ""
    if run5 >= config.ALERT_RUNUP_STRONG_5D:
        return (f"\n   ⚠️ <i>Đã chạy +{run5:.1f}%/5 phiên — vùng hiếm, lịch sử kỳ vọng ÂM "
                f"và rung lắc rất sâu (MAE ~−4.8%): cân nhắc bỏ qua hoặc size tối thiểu</i>")
    if run5 >= config.ALERT_RUNUP_WARN_5D:
        return (f"\n   ⚠️ <i>Đã chạy +{run5:.1f}%/5 phiên — kỳ vọng không giảm (backtest) "
                f"nhưng rung lắc T+2.5 sâu hơn (MAE ~−2.6% vs −1.6% bình thường) "
                f"→ cân nhắc giảm size</i>")
    return ""


def _flow5_line(fr_daily, pr_daily, fp5=None, pp5=None, prefix="   💵 ") -> str:
    """Dòng ngữ cảnh dòng tiền 5 PHIÊN TRƯỚC, liệt kê TỪNG PHIÊN cũ→mới (tỷ VND),
    kèm tổng % trên GTGD 5 phiên. Luôn hiện cả khối ngoại lẫn tự doanh."""
    def _seq(vals):
        if not vals:
            return "—"
        return " · ".join(f"{v/1e9:+.1f}" for v in vals)
    def _p(v):
        return f" (Σ {v:+.1f}%)" if v is not None and v == v else ""
    if not fr_daily and not pr_daily:
        return ""
    return (f"{chr(10)}{prefix}<i>5 phiên trước (cũ→mới, tỷ): Khối ngoại {_seq(fr_daily)}{_p(fp5)}"
            f" | Tự doanh {_seq(pr_daily)}{_p(pp5)}</i>")


def _foreign_note(r) -> str:
    """Dòng xác nhận dòng tiền ngoại (backtest #52: smart money làm confluence cho
    breakout tốt hơn làm tín hiệu độc lập). Chỉ hiện khi NN chiếu ≥ ngưỡng screen."""
    fl = r.get("foreign_live_pct")
    if fl is None or fl != fl or fl < config.SM_FOREIGN_PCT_MIN:
        return ""
    strong = " MẠNH" if fl >= 10 else ""
    return (f"\n   💰 <i>Khối ngoại xác nhận{strong}: mua ròng ≈{fl:+.1f}% nền GTGD "
            f"5 phiên (chiếu cả phiên)</i>")


def _timing_note(r) -> str:
    """One-line RevD timing context for the alert (why it's actionable now)."""
    state = r.get("state")
    rsi = r.get("mom_rsi")
    rsi_txt = f" · RSI {rsi:.0f}" if rsi is not None and rsi == rsi else ""
    runup = _runup_note(r) + _foreign_note(r)
    if state == "PRE_BREAKOUT":
        dist = r.get("setup_dist_below_pivot")
        d = f"cách đỉnh {dist:.1f}%" if dist is not None and dist == dist else "sát đỉnh"
        return f"↳ {d} — coiling, chờ vượt cản{rsi_txt}{runup}"
    if state in ("BREAKOUT_FRESH", "BREAKOUT_LATE"):
        age = r.get("breakout_age")
        ratio = r.get("bo_breakout_ratio")
        age_txt = f"vừa vượt hôm nay" if age == 0 else f"đã vượt {age} phiên trước"
        r_txt = f" (+{(ratio - 1) * 100:.1f}% trên đỉnh)" if ratio and ratio == ratio else ""
        warn = ("\n   ⚠️ <i>Muộn — momentum tiếp diễn: chỉ hợp lệ khi thị trường thuận; "
                "rủi ro khóa T+2.5 cao nhất, cân nhắc vị thế nhỏ + stop chặt</i>"
                if state == "BREAKOUT_LATE" else "")
        return f"↳ {age_txt}{r_txt}{rsi_txt}{runup}{warn}"
    base = f"↳ {rsi_txt.lstrip(' ·')}" if rsi_txt else ""
    return f"{base}{runup}" if (base or runup) else ""


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
        f5 = _flow5_line(r.get("flow_fr_daily"), r.get("flow_pr_daily"),
                         r.get("mom_foreign_net_pct"), r.get("mom_prop_net_pct"))
        if f5:
            lines.append(f5.lstrip("\n"))
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
        _smart_money_screen()              # kênh quan sát dòng tiền (Telegram + db)
        _refresh_dividends()               # cash-dividend calendar (once/day)
        _update_outcomes()                 # recommended signals (tracked_signals)
        _update_sm_outcomes()              # smart-money screen picks (hiệu quả kênh 💰)
        _update_observation_outcomes()     # whole pool (daily_observations)
        # (Auto-learner W_BUY đã KHAI TỬ 19/07 — tiền đề bị bác bởi chiến dịch
        #  calibrate 233k mẫu: mặt objective phẳng quanh trọng số hiện tại, learner
        #  nhỏ giọt chỉ có thể học nhiễu. Xem DEVELOPMENT #37/#38/#40.)
        _milestone_reminders()
    except Exception as e:
        _log(f"EOD job ERROR: {type(e).__name__}: {e}")


def start_scheduler(initial_scan: bool = True):
    """Launch the background schedule loop in a daemon thread.

    08:00 — heavy data fetch + static Layer-1 pool, then a scan (once/day).
    Every 5 min (09:15–14:45) — live filters (#6/#7) + Layer-2 scoring.
    15:30 — final EOD scan.
    On startup — one immediate scan so the dashboard is never stale/empty.
    """
    schedule.every().day.at("08:00").do(_morning_warmup)
    # Bản đầy đủ dòng tiền phiên hôm trước (NN+TD chính thức) — 2 lượt đề phòng API trễ
    schedule.every().day.at("08:45").do(_sm_morning_final)
    schedule.every().day.at("11:45").do(_sm_morning_final)
    # Nhắc cập nhật thư viện — thứ Hai hàng tuần, chỉ thông báo (xem _lib_update_check)
    schedule.every().monday.at("08:15").do(_lib_update_check)
    schedule.every(5).minutes.do(_intraday_job)
    schedule.every().day.at("15:30").do(_eod_job)
    # Hourly Telegram alert of top new Layer-2 stocks, ALERT_START_HOUR..END_HOUR.
    for h in range(config.ALERT_START_HOUR, config.ALERT_END_HOUR + 1):
        schedule.every().day.at(f"{h:02d}:00").do(_alert_job)
        # Dòng tiền NN theo giờ (lệch 5' sau breakout để 2 tin không dính nhau)
        schedule.every().day.at(f"{h:02d}:05").do(_sm_hourly_alert)

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
