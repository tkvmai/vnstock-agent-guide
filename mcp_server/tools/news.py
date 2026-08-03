"""News tools: fetch articles, search, read full text, trending, archive.

Fetch engines (vnstock_news 2.2.x), measured 2026-08-03 on this machine:

  Crawler.get_articles_from_feed()   RSS metadata only. ~1.3s for 20 articles.
                                     Returns ONLY url/title/description/publish_time
                                     — `content` is empty.
  AsyncBatchCrawler.fetch_articles_async()
                                     Full article text. 1.9s for 10 dddn articles
                                     (0.19s/article) vs 19.4s for the same 10 via
                                     the synchronous BatchCrawler — 10.2x faster.
                                     Accepts an RSS *or* sitemap URL as `sources`
                                     and returns content either way.
  Crawler.get_article_details(url)   One article, full text. ~0.2s.

Why this file changed on 2026-08-03
-----------------------------------
1. The sitemap path used the SYNCHRONOUS BatchCrawler at ~1.9s/article. That is
   why seven sources (dddn, baodautu, nhandan, nguoiquansat, 24h, plo, ktsg)
   were effectively unusable and why dddn — a finance source — had to be cut
   from the defaults. Switched to AsyncBatchCrawler: same data, ~10x faster.

2. search_news matched title + description only, and RSS carries no body text.
   Measured over 60 cafef/vietstock articles: searching title+description found
   0 hits for "VCB", 0 for "HPG", 0 for "FPT"; searching the body found 2, 1
   and 2. "lãi suất" went 1 -> 11. A ticker essentially never appears in a
   headline, so ticker search — the single most important query for a trading
   agent — was structurally broken. `deep=True` now enriches candidates with
   body text before matching.

3. No time filtering existed at all. The library's own `within` / `time_frame`
   do filter (verified: within="1d" cut 40 rows to 33 and moved the floor), but
   they compare a tz-naive UTC "now" against publish times that are VN-local,
   so a 1h window returned ~3.4h of articles. We therefore filter ourselves on
   the already-normalized Asia/Ho_Chi_Minh timestamp and never pass `within`
   down to the library.

4. get_news_sources() was a hand-maintained 21-row table that would silently
   drift from the library. It is now derived from list_supported_sites().

Retained from the previous version: deadline-aware partial results, the
15-minute in-process frame cache, cross-source dedup, VN-local time
normalization, and running in-process rather than through the shared vnstock
worker (see server.py).
"""

import sys
import os
import re
import asyncio
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as _FutureTimeout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.df_converter import to_claude_text
from utils.error_handler import handle_vnstock_error

_SOURCES_MAP = None
_RSS_SOURCES = None
_BATCH_SOURCES = None

# Wall-clock budget (seconds) a multi-source fetch may spend before returning
# what it has. Must stay well under the MCP client's request timeout: a call
# that overruns it tears down the HTTP session and kills every other in-flight
# tool call on that session.
_DEFAULT_BUDGET = 20.0

# Deep (full-text) fetches are per-article, so they get their own slice of the
# budget rather than being allowed to consume all of it.
_DEEP_FRACTION = 0.6

_CACHE_TTL = 900.0
_cache = {}
_cache_lock = threading.Lock()

# Article bodies are expensive and change never, so they get a much longer TTL
# than headline lists.
_BODY_TTL = 86400.0

# Disk cache. The in-memory frame cache above dies with the process, so every
# server restart used to re-pay for every fetch. vnstock_news ships a sqlite
# Cache; EnhancedNewsCrawler's built-in one writes 'vnnews_cache.db' into the
# process CWD (verified) and exposes no way to redirect it, so we drive the
# Cache class directly with an ABSOLUTE db path instead — verified to leave the
# server directory clean.
_DISK = None
_disk_lock = threading.Lock()
_DISK_BROKEN = False

_POOL = None
_pool_lock = threading.Lock()

# Written by AsyncBatchCrawler/EnhancedNewsCrawler; keep it out of the server cwd.
_TMP = os.path.join(tempfile.gettempdir(), "vnnews")
os.makedirs(_TMP, exist_ok=True)


def _get_pool():
    global _POOL
    with _pool_lock:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=12, thread_name_prefix="vnnews")
        return _POOL


# Working RSS feeds for sources whose SITES_CONFIG ships without one (each
# verified live). NOT overridable: dddn (feed redirects to a 404 page),
# baodautu (feed is a valid but empty XML shell) — both stay on sitemap, which
# is now cheap enough to be usable.
_RSS_OVERRIDES = {
    "cafef": {
        "urls": ["https://cafef.vn/thi-truong-chung-khoan.rss",
                 "https://cafef.vn/index.rss"],
    },
    "vneconomy": {
        "urls": ["https://vneconomy.vn/chung-khoan.rss",
                 "https://vneconomy.vn/rss/home.rss"],
    },
    "tienphong": {
        "urls": ["https://tienphong.vn/rss/home.rss"],
        # tienphong's <link> resolves to an empty <atom:link/>; the real URL is
        # in <guid>.
        "mapping": {"link": "guid", "title": "title",
                    "description": "description", "publish_time": "pubDate"},
    },
}


def _get_source_metadata():
    """Return (sources_map, rss_sources, batch_sources), built on first call."""
    global _SOURCES_MAP, _RSS_SOURCES, _BATCH_SOURCES
    if _SOURCES_MAP is not None:
        return _SOURCES_MAP, _RSS_SOURCES, _BATCH_SOURCES
    try:
        from vnstock_news import Crawler, SITES_CONFIG
        for site, rss_cfg in _RSS_OVERRIDES.items():
            if site in SITES_CONFIG:
                SITES_CONFIG[site].setdefault("rss", {}).update(rss_cfg)
        sources_map, rss, batch = {}, set(), set()
        for site in SITES_CONFIG.keys():
            try:
                c = Crawler(site_name=site)
                if c.rss_urls:
                    sources_map[site] = c.rss_urls[0]
                    rss.add(site)
                elif c.sitemap_url:
                    sources_map[site] = c.sitemap_url
                    batch.add(site)
            except Exception:
                pass
        _SOURCES_MAP, _RSS_SOURCES, _BATCH_SOURCES = sources_map, rss, batch
    except Exception:
        _SOURCES_MAP, _RSS_SOURCES, _BATCH_SOURCES = {}, set(), set()
    return _SOURCES_MAP, _RSS_SOURCES, _BATCH_SOURCES


# Finance-first defaults. All four are RSS, so a default call is metadata-only
# and near-instant; ask for depth explicitly when you need body text.
FINANCE_SOURCES = ["vietstock", "cafebiz", "cafef", "vneconomy"]

# Sources reached by crawling a sitemap. With the async engine these cost
# roughly 0.2-1s per article (site-dependent) instead of the ~1.9s/article the
# old synchronous path paid, so they are usable now — but they are still the
# expensive half, and dddn/baodautu are the two finance ones worth naming.
SITEMAP_SOURCES = frozenset({
    "dddn", "baodautu", "nhandan", "nguoiquansat", "24h", "plo", "ktsg",
})

# Verified 2026-07-21: this feed returns 0 rows. Kept selectable, kept out of
# every default.
_EMPTY_SOURCES = frozenset({"thoibaotaichinhvietnam"})


def _to_df(raw):
    import pandas as pd
    if isinstance(raw, pd.DataFrame):
        return raw
    if isinstance(raw, list) and raw:
        return pd.DataFrame(raw)
    return pd.DataFrame()


def _select_cols(df, with_content=False):
    preferred = ["publish_time", "title", "short_description", "description",
                 "category", "tags", "author", "view_counts", "url", "feed_source"]
    if with_content:
        preferred.insert(3, "content")
    cols = [c for c in preferred if c in df.columns]
    return df[cols] if cols else df


def _normalize_times(df):
    """
    Normalize publish_time to 'YYYY-MM-DD HH:MM' in Asia/Ho_Chi_Minh. Sources
    mix RFC-2822 with +07:00 offsets, GMT-suffixed strings and naive VN-local
    strings; the result is consistent AND lexically sortable. Pandas parses
    'GMT'/'UTC'-suffixed strings as NAIVE, so treating every naive value as
    VN-local would shift those by 7 hours — check the marker explicitly.
    """
    import pandas as pd

    if "publish_time" not in df.columns:
        return df

    def norm(val):
        raw = str(val) if val is not None else ""
        ts = pd.to_datetime(raw, errors="coerce")
        if pd.isna(ts):
            return None
        if ts.tzinfo is None:
            tz = "UTC" if re.search(r"\b(GMT|UTC)\b", raw) else "Asia/Ho_Chi_Minh"
            ts = ts.tz_localize(tz)
        return ts.tz_convert("Asia/Ho_Chi_Minh").strftime("%Y-%m-%d %H:%M")

    df = df.copy()
    df["publish_time"] = df["publish_time"].map(norm)
    return df


def _parse_within(within):
    """'30m' / '6h' / '2d' / '1w' -> timedelta. None on anything unparseable."""
    from datetime import timedelta
    if not within:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*([mhdw])\s*", str(within).lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return {"m": timedelta(minutes=n), "h": timedelta(hours=n),
            "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]


def _filter_time(df, within=None, since=None):
    """
    Filter on the normalized Asia/Ho_Chi_Minh publish_time.

    Done here rather than via the library's `within`/`time_frame`: those compare
    a tz-naive UTC 'now' against VN-local publish times, so a 1h window measured
    ~3.4h of articles. Rows with an unparseable timestamp are KEPT — dropping
    them would silently discard articles whose feed simply omits a date.

    Returns (df, note).
    """
    import pandas as pd
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if df.empty or "publish_time" not in df.columns:
        return df, ""
    if not within and not since:
        return df, ""

    now_vn = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).replace(tzinfo=None)
    floor = None
    label = ""

    if within:
        delta = _parse_within(within)
        if delta is None:
            return df, (f"\n⚠ Không hiểu `within='{within}'` — bỏ qua bộ lọc thời gian. "
                        "Dùng dạng 30m / 6h / 2d / 1w.")
        floor = now_vn - delta
        label = f"trong {within} gần nhất"
    if since:
        ts = pd.to_datetime(str(since), errors="coerce")
        if pd.isna(ts):
            return df, (f"\n⚠ Không hiểu `since='{since}'` — bỏ qua bộ lọc thời gian. "
                        "Dùng YYYY-MM-DD hoặc 'YYYY-MM-DD HH:MM'.")
        ts = ts.tz_localize(None) if ts.tzinfo else ts
        floor = ts if floor is None else max(floor, ts)
        label = (label + " và " if label else "") + f"từ {since}"

    parsed = pd.to_datetime(df["publish_time"], errors="coerce")
    keep = parsed.isna() | (parsed >= floor)
    out = df[keep]
    undated = int(parsed.isna().sum())
    note = (f"\nLọc thời gian: {label} — còn {len(out)}/{len(df)} bài"
            f" (mốc {floor:%Y-%m-%d %H:%M} giờ VN)")
    if undated:
        note += f"; {undated} bài không có ngày hợp lệ nên được giữ lại"
    return out, note


def _dedupe(df):
    """
    Drop syndicated duplicates: first by url, then by title — cafef and cafebiz
    are both VCCorp and run the same story under different URLs. Null/empty
    titles are exempt from the title pass so they don't collapse into one row.
    """
    if "url" in df.columns:
        df = df.drop_duplicates(subset=["url"])
    if "title" in df.columns:
        titled = df["title"].notna() & df["title"].astype(str).str.strip().ne("")
        dup = df.loc[titled].duplicated(subset=["title"], keep="first")
        df = df.drop(index=dup[dup].index)
    return df


def _async_fetch(site, source_url, limit):
    """
    AsyncBatchCrawler in a private event loop. Works for an RSS *or* a sitemap
    URL and returns full body text either way (verified on both). Runs in a
    worker thread, so it must create and close its own loop.
    """
    from vnstock_news import AsyncBatchCrawler

    async def run():
        ac = AsyncBatchCrawler(
            site_name=site,
            max_concurrency=10,
            temp_file=os.path.join(_TMP, f"async_{site}.csv"),
        )
        # `within` is deliberately not passed — see _filter_time.
        return await ac.fetch_articles_async(sources=[source_url], top_n=limit)

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return _to_df(loop.run_until_complete(run()))
    finally:
        try:
            loop.close()
        finally:
            asyncio.set_event_loop(None)


def _fetch_single(source: str, limit: int, deep: bool = False):
    """
    One source.

    deep=False on an RSS source -> metadata only, cheapest path (~1.3s/20).
    Everything else goes through the async engine, which returns body text.
    """
    from vnstock_news import Crawler

    sources_map, rss_sources, _ = _get_source_metadata()
    url = sources_map.get(source)

    if source in rss_sources and not deep:
        c = Crawler(site_name=source)
        return _to_df(c.get_articles_from_feed(limit_per_feed=limit))

    if not url:
        return _to_df(None)
    return _async_fetch(source, url, limit)


def _disk_cache():
    """Persistent sqlite cache, or None if unavailable. Never raises."""
    global _DISK, _DISK_BROKEN
    if _DISK_BROKEN:
        return None
    with _disk_lock:
        if _DISK is None:
            try:
                from vnstock_news.utils.cache import Cache
                _DISK = Cache(
                    cache_type="sqlite",
                    cache_dir=_TMP,
                    db_file=os.path.join(_TMP, "vnnews_cache.db"),  # absolute
                    ttl=int(_BODY_TTL),
                )
            except Exception:
                _DISK_BROKEN = True
                return None
        return _DISK


def _disk_get(key):
    c = _disk_cache()
    if c is None:
        return None
    try:
        return c.get(key)
    except Exception:
        return None


def _disk_put(key, value, ttl=None):
    c = _disk_cache()
    if c is None:
        return
    try:
        c.set(key, value, ttl=int(ttl) if ttl else None)
    except Exception:
        pass


def _cache_key(source, deep):
    return f"{source}::{'deep' if deep else 'meta'}"


def _cache_get(source: str, limit: int, deep: bool = False):
    import pandas as pd

    key = _cache_key(source, deep)
    with _cache_lock:
        entry = _cache.get(key)
    if entry is not None:
        fetched_at, cached_limit, df = entry
        if time.time() - fetched_at <= _CACHE_TTL and not (
                cached_limit < limit and len(df) < limit):
            return df.head(limit).copy()

    # Fall through to disk — survives a server restart.
    payload = _disk_get("frame::" + key)
    if isinstance(payload, dict) and payload.get("rows"):
        if payload.get("limit", 0) >= limit or len(payload["rows"]) >= limit:
            df = pd.DataFrame(payload["rows"])
            if not df.empty:
                with _cache_lock:
                    _cache[key] = (time.time(), payload.get("limit", limit), df)
                return df.head(limit).copy()
    return None


def _cache_put(source: str, limit: int, df, deep: bool = False):
    if df is None or df.empty:
        return
    key = _cache_key(source, deep)
    with _cache_lock:
        prev = _cache.get(key)
        if prev is not None:
            prev_at, prev_limit, _ = prev
            if time.time() - prev_at <= _CACHE_TTL and prev_limit > limit:
                return
        _cache[key] = (time.time(), limit, df)
    try:
        _disk_put("frame::" + key,
                  {"limit": limit, "rows": df.to_dict("records")},
                  ttl=_CACHE_TTL)
    except Exception:
        pass


def _fetch_and_cache(source: str, limit: int, deep: bool = False):
    df = _to_df(_fetch_single(source, limit, deep=deep))
    if not df.empty:
        df = _normalize_times(df).assign(feed_source=source)
        _cache_put(source, limit, df, deep=deep)
    return df


def _fetch_many(sources, per_source: int, budget: float, started=None, deep=False):
    """
    Fetch several sources concurrently, returning whatever is ready by `budget`.

    Returns (df_all, done, pending, cached, failed). `pending` names sources
    still fetching — they keep running and populate the cache, so an immediate
    retry gets them.
    """
    import pandas as pd

    frames, done, cached, pending = [], [], [], []
    failed = {}
    futures = {}
    pool = _get_pool()

    for src in sources:
        hit = _cache_get(src, per_source, deep=deep)
        if hit is not None:
            frames.append(hit)
            cached.append(src)
            done.append(src)
        else:
            futures[pool.submit(_fetch_and_cache, src, per_source, deep)] = src

    if futures:
        remaining = budget if started is None else budget - (time.monotonic() - started)
        remaining = max(1.0, remaining)
        deadline = time.monotonic() + remaining
        try:
            for fut in as_completed(futures, timeout=remaining):
                src = futures[fut]
                try:
                    df = fut.result()
                    if isinstance(df, pd.DataFrame) and not df.empty:
                        frames.append(df)
                        done.append(src)
                    else:
                        failed[src] = "returned 0 articles"
                except Exception as e:
                    failed[src] = f"{type(e).__name__}: {e}"
                if time.monotonic() >= deadline:
                    break
        except _FutureTimeout:
            pass
        settled = set(done) | set(failed)
        pending = [s for f, s in futures.items() if s not in settled]

    df_all = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    df_all = _dedupe(df_all) if not df_all.empty else df_all
    return df_all, done, pending, cached, failed


def _clean_body(text):
    """
    Strip navigation cruft from an article body.

    Raw extraction leaves things like '[TIN MỚI](javascript:; "TIN MỚI")' inline,
    which an LLM will happily read as article content. ContentCleaner (shipped
    with vnstock_news) handles the bulk; the javascript-link pattern is removed
    explicitly because it survives it.
    """
    if not text:
        return text
    try:
        from vnstock_news import ContentCleaner
        cleaned = ContentCleaner().clean(text) if hasattr(ContentCleaner, "clean") \
            else ContentCleaner().clean_content(text)
        if isinstance(cleaned, str) and len(cleaned) >= 100:
            text = cleaned
    except Exception:
        pass
    text = re.sub(r"\[([^\]]*)\]\(javascript:[^)]*\)", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _article_details(url, site=None):
    """
    One article's full text. ~0.2s cold, instant when cached.

    A published article does not change, so bodies are cached on disk for a day
    — this is what makes a repeated `deep=True` search and any follow-up
    get_article() call free.
    """
    from vnstock_news import Crawler

    key = "body::" + str(url)
    hit = _disk_get(key)
    if isinstance(hit, dict) and len(str(hit.get("content") or "")) >= 200:
        return hit

    try:
        c = Crawler(site_name=site) if site else Crawler(site_name="vnexpress")
        d = c.get_article_details(url)
        if not isinstance(d, dict):
            return {}
        if len(str(d.get("content") or "")) >= 200:
            # Keep only what is JSON-round-trippable; drop anything exotic.
            slim = {k: (str(v) if v is not None else None)
                    for k, v in d.items()
                    if k in ("title", "content", "author", "publish_time",
                             "category", "tags", "view_counts", "url")}
            _disk_put(key, slim, ttl=_BODY_TTL)
        return d
    except Exception:
        return {}


def _enrich_content(df, budget, max_articles=200):
    """
    Fill in body text for rows that lack it, concurrently.

    Bounded by the caller's remaining budget, NOT by a small article cap. An
    earlier version capped this at 40 articles and that silently destroyed
    recall: enriching 55 of 100 articles found 2 'VCB' hits, enriching only the
    first 40 of the same 100 found none. A ticker appears in a handful of bodies
    out of a hundred, so any cap below the fetched set turns a real hit into a
    false 'no news'. Bodies are disk-cached for a day, so the second call over
    the same window is nearly free.

    Returns (df, note) — the note says how many rows actually gained text,
    because a partial enrich changes what a keyword search can possibly find and
    that must never be silent.
    """
    if df.empty or "url" not in df.columns:
        return df, ""

    df = df.copy()
    if "content" not in df.columns:
        df["content"] = ""
    have = df["content"].astype(str).str.len() >= 200
    todo = df.index[~have][:max_articles]
    if len(todo) == 0:
        return df, ""

    pool = _get_pool()
    futures = {}
    for i in todo:
        site = df.at[i, "feed_source"] if "feed_source" in df.columns else None
        futures[pool.submit(_article_details, df.at[i, "url"], site)] = i

    filled = 0
    deadline = time.monotonic() + max(1.0, budget)
    try:
        for fut in as_completed(futures, timeout=max(1.0, budget)):
            i = futures[fut]
            try:
                d = fut.result()
                body = str(d.get("content") or "")
                if len(body) >= 200:
                    df.at[i, "content"] = body
                    filled += 1
                    for extra in ("tags", "author", "category"):
                        val = d.get(extra)
                        if val and (extra not in df.columns or not str(df.at[i, extra] or "").strip()):
                            df.at[i, extra] = val
            except Exception:
                pass
            if time.monotonic() >= deadline:
                break
    except _FutureTimeout:
        pass

    total_with_text = int((df["content"].astype(str).str.len() >= 200).sum())
    note = (f"\nĐọc toàn văn: {total_with_text}/{len(df)} bài có nội dung "
            f"(vừa tải thêm {filled})")
    if len(todo) > filled:
        note += (
            f"; **{len(todo) - filled} bài chưa kịp đọc trong ngân sách "
            f"{budget:.0f}s — chưa quét được nội dung của chúng, nên KHÔNG kết luận "
            "'không có tin'. Gọi lại (bài đã đọc được cache 24h nên lần sau nhanh) "
            "hoặc nâng max_seconds / giảm limit_per_source.**"
        )
    return df, note


def _slow_source_warning(sources, budget, deep):
    """Warn when the request is likely to outrun its budget."""
    slow = [s for s in sources if s in SITEMAP_SOURCES]
    bits = []
    if slow:
        bits.append(
            f"\n⚠ Nguồn sitemap ({', '.join(slow)}) phải crawl từng bài (~0.2-1s/bài tùy báo). "
            "Giảm limit_per_source hoặc nâng max_seconds nếu thấy báo 'còn đang tải'."
        )
    if deep and budget < 15:
        bits.append(
            f"\n⚠ deep=True đọc toàn văn từng bài (~0.2s/bài) nhưng max_seconds chỉ {budget:.0f}s — "
            "nhiều bài sẽ không kịp tải và kết quả tìm kiếm sẽ thiếu. Nâng max_seconds lên ≥20s."
        )
    empty = [s for s in sources if s in _EMPTY_SOURCES]
    if empty:
        bits.append(f"\n⚠ Nguồn {', '.join(empty)} đã kiểm chứng trả 0 bài — chỉ tốn một slot.")
    return "".join(bits)


def _coverage_note(done, pending, cached, failed):
    parts = [f"Sources fetched: {', '.join(done) if done else 'none'}"]
    if cached:
        parts.append(f"from cache: {', '.join(cached)}")
    if failed:
        parts.append("**failed: "
                     + "; ".join(f"{s} ({err})" for s, err in failed.items()) + "**")
    if pending:
        parts.append(
            f"**still loading: {', '.join(pending)}** — they finish in the "
            f"background, so run the same call again shortly for full coverage"
        )
    return " | ".join(parts)


def _keyword_mask(df, keyword: str, search_content: bool):
    """
    Case-insensitive substring match, except for ticker-shaped queries ('CTS',
    'VIX'), which are matched on word boundaries so a 3-letter code doesn't
    match the middle of an unrelated Vietnamese word.
    """
    import pandas as pd

    kw = keyword.strip()
    ticker_like = kw.isalpha() and kw.isupper() and 2 <= len(kw) <= 5
    pattern = rf"\b{re.escape(kw)}\b" if ticker_like else re.escape(kw)

    cols = ["title", "description", "short_description", "tags", "category"]
    if search_content:
        cols.append("content")

    mask = pd.Series(False, index=df.index)
    for col in cols:
        if col in df.columns:
            mask |= df[col].astype(str).str.contains(
                pattern, case=False, na=False, regex=True)
    return mask


# ─────────────────────────────────────────────────────────────────────────────
# Tools
# ─────────────────────────────────────────────────────────────────────────────

def get_news(source: str = "cafef", limit: int = 10,
             deep: bool = False, within: str = None, since: str = None) -> str:
    """
    Get recent news articles from a Vietnamese news source.

    Args:
        source: News source name. Call get_news_sources() for the live list.
        limit: Number of articles (default 10, max 50).
        deep: Fetch full article body text. RSS carries only title +
              description; set this to read the actual articles. Costs roughly
              0.2s per article.
        within: Keep only articles newer than this age — '30m', '6h', '2d',
              '1w'. Applied on Asia/Ho_Chi_Minh publish time.
        since: Keep only articles published on/after this date — 'YYYY-MM-DD'
              or 'YYYY-MM-DD HH:MM' (VN time). Combines with `within`.
    """
    source = source.lower().strip()
    limit = max(1, min(limit, 50))
    sources_map, rss_sources, _ = _get_source_metadata()
    if source not in set(sources_map.keys()):
        return f"[Unknown source '{source}'. Supported: {', '.join(sorted(sources_map))}]"

    try:
        df = _cache_get(source, limit, deep=deep)
        served_from_cache = df is not None
        if df is None:
            df = _fetch_and_cache(source, limit, deep=deep)
        if df.empty:
            return f"No articles found from '{source}'."

        df, tnote = _filter_time(df, within=within, since=since)
        if df.empty:
            return (f"## News: {source}\n{tnote.strip()}\n\n"
                    "Không còn bài nào trong khoảng thời gian đã chọn. "
                    "Nới `within`/`since`, hoặc tăng `limit` để lấy sâu hơn.")

        has_content = ("content" in df.columns
                       and (df["content"].astype(str).str.len() >= 200).any())
        out = _select_cols(df, with_content=has_content).head(limit)
        strategy = "RSS" if (source in rss_sources and not deep) else "Async full-text"
        cache_note = " (cached)" if served_from_cache else ""
        return (
            f"## News: {source} ({strategy}, {len(out)} articles{cache_note})"
            f"{tnote}\n\n"
            + to_claude_text(out, mode="json", max_rows=limit)
        )
    except ImportError:
        return "[vnstock_news not installed. Run: pip install vnstock_news]"
    except Exception as e:
        return handle_vnstock_error(e, "get_news")


def get_article(url: str, source: str = None) -> str:
    """
    Read one article in full: body text, author, tags, category, publish time.

    Use after get_news / search_news returns a headline worth reading. Takes
    roughly 0.2 seconds.

    Args:
        url: Full article URL, as returned by the other news tools.
        source: Source name the URL came from (e.g. 'cafef'). Optional — it
              selects the right parser rules and improves extraction on sites
              with unusual layouts.
    """
    if not url or not str(url).strip().startswith("http"):
        return "[url must be a full http(s) article URL]"
    url = str(url).strip()
    site = (source or "").lower().strip() or None
    if site:
        sources_map, _, _ = _get_source_metadata()
        if site not in sources_map:
            site = None

    try:
        if site is None:
            sources_map, _, _ = _get_source_metadata()
            from urllib.parse import urlparse
            host = urlparse(url).netloc.lower().replace("www.", "")
            for name, feed in sources_map.items():
                try:
                    if urlparse(feed).netloc.lower().replace("www.", "") == host:
                        site = name
                        break
                except Exception:
                    pass

        t0 = time.time()
        d = _article_details(url, site)
        elapsed = time.time() - t0
        if not d:
            return (f"[Không đọc được bài: {url}]\n"
                    "Trang có thể đã đổi layout, yêu cầu đăng nhập, hoặc chặn crawler. "
                    "Thử lại với `source` đúng tên báo.")

        body = str(d.get("content") or "")
        if len(body) < 200:
            return (f"[Bài đọc được nhưng gần như không có nội dung ({len(body)} ký tự): {url}]\n"
                    "Thường là trang video/ảnh hoặc bài chỉ có tiêu đề. "
                    "Đừng suy luận nội dung từ tiêu đề.")

        meta = []
        for k, label in [("title", "Tiêu đề"), ("author", "Tác giả"),
                         ("publish_time", "Thời gian"), ("category", "Chuyên mục"),
                         ("tags", "Tags"), ("view_counts", "Lượt xem")]:
            v = d.get(k)
            if v is not None and str(v).strip() and str(v).lower() != "nan":
                meta.append(f"**{label}**: {v}")

        body = _clean_body(body)
        return (
            f"## Bài viết ({site or 'auto'}, {len(body):,} ký tự, {elapsed:.1f}s)\n"
            f"{url}\n\n" + "\n".join(meta) + "\n\n---\n\n" + body
        )
    except ImportError:
        return "[vnstock_news not installed. Run: pip install vnstock_news]"
    except Exception as e:
        return handle_vnstock_error(e, "get_article")


def search_news(
    keyword: str,
    sources: list = None,
    limit_per_source: int = 20,
    max_seconds: float = _DEFAULT_BUDGET,
    deep: bool = False,
    within: str = None,
    since: str = None,
) -> str:
    """
    Search articles matching a keyword across Vietnamese news sources.

    IMPORTANT for ticker searches. RSS feeds carry only the headline and a short
    summary, and a stock code almost never appears there — measured over 60
    cafef/vietstock articles, matching headline+summary found 0 hits for 'VCB',
    'HPG' and 'FPT', while matching the article body found 2, 1 and 2. **Pass
    deep=True when searching for a ticker**, or the answer will be a false
    'no news'.

    Fetches sources concurrently and returns whatever completed within
    `max_seconds`, naming any source still loading. Results are cached 15
    minutes, so a retry for full coverage is instant.

    Args:
        keyword: Search term — 'lãi suất', 'VCB', 'bất động sản'. An all-caps
              2-5 letter word is treated as a ticker and matched on word
              boundaries.
        sources: Source names (default: vietstock, cafebiz, cafef, vneconomy).
        limit_per_source: Articles fetched per source before filtering
              (default 20, max 50).
        max_seconds: Wall-clock budget before returning partial results
              (default 20, max 120). Use >= 20 with deep=True. A call much over
              ~30s risks the MCP client's request timeout, which kills every
              other in-flight call on the same session.
        deep: Read article bodies before matching. Required for ticker searches.
        within: Only articles newer than '30m' / '6h' / '2d' / '1w'.
        since: Only articles on/after 'YYYY-MM-DD' (VN time).
    """
    _started = time.monotonic()
    if not keyword or not keyword.strip():
        return "[keyword cannot be empty]"

    keyword = keyword.strip()
    sources_map, _, _ = _get_source_metadata()
    all_sources = set(sources_map.keys())
    sources = [s.lower().strip() for s in (sources or FINANCE_SOURCES)
               if s.lower().strip() in all_sources][:10]
    if not sources:
        return f"[No valid sources. Supported: {', '.join(sorted(all_sources))}]"
    limit_per_source = max(5, min(int(limit_per_source), 50))
    budget = max(5.0, min(float(max_seconds), 120.0))

    ticker_like = keyword.isalpha() and keyword.isupper() and 2 <= len(keyword) <= 5

    try:
        fetch_budget = budget * (1 - _DEEP_FRACTION) if deep else budget
        df_all, done, pending, cached, failed = _fetch_many(
            sources, limit_per_source, fetch_budget, started=_started, deep=False)

        coverage = (_coverage_note(done, pending, cached, failed)
                    + _slow_source_warning(sources, budget, deep))

        if df_all.empty:
            return (f"No articles fetched yet for '{keyword}'.\n{coverage}\n\n"
                    "Nothing came back inside the time budget. Retry — the "
                    "fetches kept running and should now be cached.")

        df_all, tnote = _filter_time(df_all, within=within, since=since)
        if df_all.empty:
            return (f"## Search: '{keyword}' — 0 results\n{coverage}{tnote}\n\n"
                    "Không còn bài nào sau khi lọc thời gian.")

        enrich_note = ""
        if deep:
            spent = time.monotonic() - _started
            enrich_note_budget = max(2.0, budget - spent)
            df_all, enrich_note = _enrich_content(df_all, enrich_note_budget)

        result = df_all[_keyword_mask(df_all, keyword, search_content=deep)]
        has_content = ("content" in df_all.columns
                       and (df_all["content"].astype(str).str.len() >= 200).any())
        result = _select_cols(result, with_content=False)

        if result.empty:
            hint = ""
            if ticker_like and not deep:
                hint = ("\n\n**Đây là tìm kiếm theo mã chứng khoán mà chưa bật `deep=True`.** "
                        "Mã hầu như không xuất hiện trong tiêu đề RSS — đã đo: tìm tiêu đề+tóm tắt "
                        "cho 0 kết quả với VCB/HPG/FPT, tìm cả thân bài cho 2/1/2. "
                        "**Gọi lại với deep=True trước khi kết luận là không có tin.**")
            elif not deep:
                hint = ("\n\nChưa đọc thân bài. Thử `deep=True`, hoặc nới `limit_per_source`, "
                        "thêm nguồn, hoặc tìm từ khóa rộng hơn.")
            else:
                hint = ("\n\nĐã tìm cả thân bài. Tin có thể nằm ngoài cửa sổ đã lấy — "
                        "tăng `limit_per_source`, thêm nguồn, hoặc nới `within`/`since`.")
            return (f"## Search: '{keyword}' — 0 results\n"
                    f"Scanned {len(df_all)} articles"
                    f"{' (with body text)' if deep else ' (headline + summary only)'}.\n"
                    f"{coverage}{tnote}{enrich_note}{hint}")

        if "publish_time" in result.columns:
            result = result.sort_values("publish_time", ascending=False)

        scope = "tiêu đề + tóm tắt + thân bài" if deep else "tiêu đề + tóm tắt"
        return (
            f"## Search: '{keyword}' — {len(result)} results "
            f"(quét {len(df_all)} bài, phạm vi: {scope})\n"
            f"{coverage}{tnote}{enrich_note}\n\n"
            + to_claude_text(result, mode="json", max_rows=50)
            + ("\n\n*Dùng get_article(url) để đọc toàn văn một bài.*" if not has_content else "")
        )

    except ImportError:
        return "[vnstock_news not installed. Run: pip install vnstock_news]"
    except Exception as e:
        return handle_vnstock_error(e, "search_news")


def get_trending_keywords(
    sources: list = None,
    limit_per_source: int = 30,
    top_n: int = 30,
    ngram_range: list = None,
    max_seconds: float = _DEFAULT_BUDGET,
    within: str = None,
    since: str = None,
) -> str:
    """
    Extract trending multi-word phrases from recent financial news.

    Same deadline + cache behaviour as search_news: returns what completed
    within `max_seconds` and names any source still loading.

    Args:
        sources: Sources to analyze (default: the four finance RSS sources).
        limit_per_source: Articles per source (default 30, max 50).
        top_n: Top N phrases to return (default 30, max 100).
        ngram_range: N-gram sizes, default [2, 3, 4].
        max_seconds: Wall-clock budget before returning partial results.
        within: Only articles newer than '30m' / '6h' / '2d' / '1w'. Use this to
              compare what the market is talking about today versus this week.
        since: Only articles on/after 'YYYY-MM-DD' (VN time).
    """
    _started = time.monotonic()
    sources_map, _, _ = _get_source_metadata()
    all_sources = set(sources_map.keys())
    sources = [s.lower().strip() for s in (sources or FINANCE_SOURCES)
               if s.lower().strip() in all_sources][:8]
    if not sources:
        return f"[No valid sources. Supported: {', '.join(sorted(all_sources))}]"
    limit_per_source = max(5, min(int(limit_per_source), 50))
    top_n = max(5, min(int(top_n), 100))
    ngram_range = ngram_range or [2, 3, 4]
    budget = max(5.0, min(float(max_seconds), 120.0))

    try:
        import pandas as pd

        try:
            import vnstock_news as _vnn
            from vnstock_news.trending.analyzer import TrendingAnalyzer
            _sw_path = os.path.join(os.path.dirname(_vnn.__file__),
                                    "config", "vietnamese-stopwords.txt")
            analyzer = TrendingAnalyzer(
                stop_words_file=_sw_path if os.path.exists(_sw_path) else None,
                min_token_length=3,
            )
            use_analyzer = True
        except ImportError:
            use_analyzer = False

        df_all, done, pending, cached, failed = _fetch_many(
            sources, limit_per_source, budget, started=_started, deep=False)
        coverage = (_coverage_note(done, pending, cached, failed)
                    + _slow_source_warning(sources, budget, False))

        if df_all.empty:
            return f"No articles fetched.\n{coverage}"

        df_all, tnote = _filter_time(df_all, within=within, since=since)
        if df_all.empty:
            return (f"## Trending Topics\n{coverage}{tnote}\n\n"
                    "Không còn bài nào sau khi lọc thời gian.")

        total_articles = len(df_all)
        all_texts = []
        for col in ["title", "short_description", "description", "tags"]:
            if col in df_all.columns:
                texts = df_all[col].dropna().astype(str).tolist()
                all_texts.extend(texts)
                if use_analyzer:
                    for text in texts:
                        analyzer.update_trends(text, ngram_range=ngram_range)

        if not all_texts:
            return f"No text content extracted from {total_articles} articles.\n{coverage}"

        if use_analyzer:
            trends = analyzer.get_top_trends(top_n=top_n)
            result_df = pd.DataFrame(
                sorted(trends.items(), key=lambda x: x[1], reverse=True),
                columns=["phrase", "count"],
            )
        else:
            from collections import Counter
            counter = Counter()
            for text in all_texts:
                tokens = [t.lower() for t in re.findall(r"[A-ZÀ-Ỹa-zà-ỹ]{3,}", text)]
                for n in ngram_range:
                    for i in range(len(tokens) - n + 1):
                        counter[" ".join(tokens[i:i + n])] += 1
            result_df = pd.DataFrame(counter.most_common(top_n), columns=["phrase", "count"])

        return (
            f"## Trending Topics (n-gram {ngram_range})\n"
            f"Articles: {total_articles} | Top {top_n}\n{coverage}{tnote}\n\n"
            + to_claude_text(result_df, mode="table", max_rows=top_n)
        )

    except ImportError:
        return "[vnstock_news not installed. Run: pip install vnstock_news]"
    except Exception as e:
        return handle_vnstock_error(e, "get_trending_keywords")


def get_news_archive(source: str, month: str = None, limit: int = 30,
                     keyword: str = None) -> str:
    """
    Fetch OLDER articles from a source's archive, for event studies — what was
    written around a past earnings release, policy change or price shock.

    Coverage is uneven and cannot be promised: a monthly archive only exists if
    the publisher ships month-partitioned sitemaps. Verified 2026-08-03 —
    plo's documented pattern returned 0 articles. When the monthly archive is
    empty this falls back to the source's current sitemap and says so, which
    only reaches back as far as that sitemap does (often days, not months).

    Args:
        source: Source name, e.g. 'cafef', 'plo', 'tuoitre'.
        month: Archive month as 'YYYY-MM'. Omit for the current sitemap.
        limit: Articles to fetch (default 30, max 100). Each costs ~0.2-1s.
        keyword: Optional filter applied to title, summary AND body text.
    """
    source = (source or "").lower().strip()
    sources_map, _, _ = _get_source_metadata()
    if source not in sources_map:
        return f"[Unknown source '{source}'. Supported: {', '.join(sorted(sources_map))}]"
    limit = max(1, min(int(limit), 100))

    if month and not re.fullmatch(r"\d{4}-\d{2}", str(month).strip()):
        return "[month must be 'YYYY-MM', e.g. '2026-06']"

    try:
        from vnstock_news import Crawler
        c = Crawler(site_name=source)
        base = c.sitemap_url or sources_map[source]
        if isinstance(base, (list, tuple)):
            base = base[0] if base else None
        if not base:
            return f"[No sitemap configured for '{source}'.]"

        tried, df, used = [], None, None
        if month:
            from urllib.parse import urlparse
            host = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
            y, mm = month.split("-")
            # Publishers are inconsistent about zero-padding: plo's live sitemap
            # is 'news-2026-8.xml', not 'news-2026-08.xml'. Derive candidates
            # from the source's OWN current sitemap first — substituting the
            # month into the real URL beats guessing a path layout.
            variants = {f"{y}-{mm}", f"{y}-{int(mm)}"}
            cands = []
            for v in variants:
                sub = re.sub(r"\d{4}-\d{1,2}(?=\.xml$)", v, base)
                if sub != base:
                    cands.append(sub)
            for v in variants:
                cands += [f"{host}/sitemaps/news-{v}.xml",
                          f"{host}/sitemap-{v}.xml",
                          f"{host}/sitemaps/sitemap-{v}.xml"]
            seen = set()
            for cand in [c for c in cands if not (c in seen or seen.add(c))]:
                tried.append(cand)
                try:
                    got = _async_fetch(source, cand, limit)
                    if got is not None and not got.empty:
                        df, used = got, cand
                        break
                except Exception:
                    pass

        fell_back = False
        if df is None or df.empty:
            fell_back = bool(month)
            df = _async_fetch(source, base, limit)
            used = base

        if df is None or df.empty:
            return (f"## Archive: {source}\nKhông lấy được bài nào.\n"
                    + (f"Đã thử: {', '.join(tried)}\n" if tried else "")
                    + f"Sitemap hiện hành: {base}")

        df = _normalize_times(df).assign(feed_source=source)
        df = _dedupe(df)

        note = f"Sitemap dùng: {used}"
        if fell_back:
            note += (f"\n⚠ **Không có kho lưu trữ theo tháng cho `{source}` với `{month}`** "
                     f"(đã thử {len(tried)} mẫu URL). Đã lùi về sitemap hiện hành, "
                     "nên khoảng thời gian dưới đây là **tin gần đây, KHÔNG phải "
                     f"tháng {month}** — đừng dùng làm bằng chứng cho giai đoạn đó.")

        if keyword and keyword.strip():
            before = len(df)
            df = df[_keyword_mask(df, keyword.strip(), search_content=True)]
            note += f"\nLọc từ khóa '{keyword}': {len(df)}/{before} bài (có quét thân bài)"
            if df.empty:
                return f"## Archive: {source}\n{note}\n\nKhông có bài nào khớp."

        rng = ""
        if "publish_time" in df.columns and df["publish_time"].notna().any():
            rng = f" | {df['publish_time'].min()} → {df['publish_time'].max()}"

        has_content = ("content" in df.columns
                       and (df["content"].astype(str).str.len() >= 200).any())
        out = _select_cols(df, with_content=False).head(limit)
        return (f"## Archive: {source} ({len(out)} bài{rng})\n{note}\n\n"
                + to_claude_text(out, mode="json", max_rows=limit)
                + ("\n\n*Các bài này đã có toàn văn — dùng get_article(url) để đọc.*"
                   if has_content else ""))

    except ImportError:
        return "[vnstock_news not installed. Run: pip install vnstock_news]"
    except Exception as e:
        return handle_vnstock_error(e, "get_news_archive")


def get_news_sources() -> str:
    """
    List every supported Vietnamese news source with its fetch strategy.

    Derived live from the installed vnstock_news (list_supported_sites + each
    site's resolved feed), not a hand-maintained table, so it cannot drift from
    the library.
    """
    import pandas as pd

    try:
        from vnstock_news import list_supported_sites
        sites = list_supported_sites()
        names = [s["name"] if isinstance(s, dict) else str(s) for s in sites]
        domains = {s["name"]: s.get("domain", "") for s in sites if isinstance(s, dict)}
    except Exception:
        sources_map, _, _ = _get_source_metadata()
        names = sorted(sources_map)
        domains = {}

    sources_map, rss_sources, batch_sources = _get_source_metadata()

    rows = []
    for n in names:
        if n in rss_sources:
            strategy, cost = "RSS", "~1.3s / 20 bài (chỉ tiêu đề + tóm tắt)"
        elif n in batch_sources:
            strategy, cost = "Sitemap", "~0.2-1s mỗi bài (có toàn văn)"
        else:
            strategy, cost = "unavailable", "không phân giải được feed"
        if n in _EMPTY_SOURCES:
            cost = "trả 0 bài (đã kiểm chứng)"
        rows.append((n, domains.get(n, ""), strategy, cost))

    df = pd.DataFrame(rows, columns=["source_name", "domain", "fetch_strategy", "cost"])
    n_rss = len(rss_sources)
    n_sitemap = len(batch_sources)

    return (
        f"## Nguồn tin được hỗ trợ ({len(rows)} nguồn — {n_rss} RSS, {n_sitemap} sitemap)\n\n"
        "**RSS** trả nhanh nhưng CHỈ có tiêu đề + tóm tắt, `content` rỗng.\n"
        "**Sitemap** crawl từng bài nên chậm hơn, đổi lại có **toàn văn**.\n\n"
        "Muốn thân bài từ nguồn RSS thì đặt `deep=True` (get_news / search_news) "
        "hoặc gọi `get_article(url)` cho từng bài.\n\n"
        "**Tìm theo mã chứng khoán bắt buộc dùng `deep=True`** — mã hầu như không "
        "nằm trong tiêu đề. Đã đo trên 60 bài: tìm tiêu đề+tóm tắt cho 0 kết quả với "
        "VCB/HPG/FPT; tìm cả thân bài cho 2/1/2.\n\n"
        f"Mặc định của search/trending: {', '.join(FINANCE_SOURCES)} — đều là RSS.\n"
        "Lọc thời gian: `within` ('30m','6h','2d','1w') và `since` ('YYYY-MM-DD'), "
        "tính theo giờ Việt Nam.\n"
        "Tin cũ hơn: `get_news_archive(source, month='YYYY-MM')` — độ phủ không đồng đều.\n\n"
        + to_claude_text(df, mode="table", max_rows=30)
    )
