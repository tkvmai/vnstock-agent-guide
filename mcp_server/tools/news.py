"""News tools: fetch articles, search, trending analysis from Vietnamese news sources.

Fetch strategy (vnstock_news v2.2.1):
  Single source  → Crawler.get_articles_from_feed()  for RSS sources (fast, real-time)
                   BatchCrawler.fetch_articles()      for sitemap-only sources
  Multi-source   → AsyncBatchCrawler.fetch_articles_async(sources=[url1, url2, ...])
                   Concurrent fetch, 3-5x faster than sequential BatchCrawler
"""

import sys
import os
import asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.df_converter import to_claude_text
from utils.error_handler import handle_vnstock_error

# Lazy-initialized source metadata — built on first tool call, not at import time
_SOURCES_MAP = None
_RSS_SOURCES = None
_BATCH_SOURCES = None


def _get_source_metadata():
    """Return (sources_map, rss_sources, batch_sources), building on first call."""
    global _SOURCES_MAP, _RSS_SOURCES, _BATCH_SOURCES
    if _SOURCES_MAP is not None:
        return _SOURCES_MAP, _RSS_SOURCES, _BATCH_SOURCES
    try:
        from vnstock_news import Crawler, SITES_CONFIG
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

FINANCE_SOURCES = ["cafef", "vneconomy", "cafebiz", "vietstock", "dddn", "thoibaotaichinhvietnam"]


def _to_df(raw):

    import pandas as pd
    if isinstance(raw, pd.DataFrame):
        return raw
    if isinstance(raw, list) and raw:
        return pd.DataFrame(raw)
    return pd.DataFrame()


def _select_cols(df):
    preferred = ["publish_time", "title", "short_description", "description",
                 "category", "tags", "author", "url", "feed_source"]
    cols = [c for c in preferred if c in df.columns]
    return df[cols] if cols else df


def _run_async(coro):
    """Run a coroutine safely whether or not an event loop is running."""
    try:
        loop = asyncio.get_running_loop()
        # Already inside a running loop (e.g. FastMCP async context)
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        return asyncio.run(coro)


async def _async_fetch_multi(sources_list: list, top_n: int):
    """Fetch each source concurrently with its OWN AsyncBatchCrawler, then concat.

    Workaround for a vnstock_news bug: passing MIXED RSS+sitemap sources to a single
    ``fetch_articles_async(sources=[...])`` yields a merged feeder containing both
    'pubdate' and 'publish_time'; the library renames both to the same column name,
    so ``feeder[col].isnull().all()`` returns a Series and ``filter_feeder`` raises
    "The truth value of a Series is ambiguous" (async_batch.py:68). One crawler per
    source keeps the columns homogeneous. Same concurrency via asyncio.gather.
    """
    import pandas as pd
    from vnstock_news import AsyncBatchCrawler
    sources_map, _, _ = _get_source_metadata()
    urls = {s: sources_map[s] for s in sources_list if s in sources_map}
    if not urls:
        return pd.DataFrame()
    per_source = max(1, top_n // len(urls))

    async def _one(name: str, url: str):
        try:
            abc = AsyncBatchCrawler(site_name=name, max_concurrency=5)
            df = await abc.fetch_articles_async(sources=[url], top_n=per_source)
            if isinstance(df, pd.DataFrame) and not df.empty:
                df = df.assign(feed_source=name)
            return df
        except Exception:
            return pd.DataFrame()   # one bad source must not sink the whole search

    results = await asyncio.gather(*[_one(n, u) for n, u in urls.items()])
    frames = [r for r in results if isinstance(r, pd.DataFrame) and not r.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _fetch_single(source: str, limit: int):
    """Fetch one source: RSS via Crawler, sitemap via BatchCrawler."""
    from vnstock_news import Crawler, BatchCrawler
    _, rss_sources, _ = _get_source_metadata()
    if source in rss_sources:
        c = Crawler(site_name=source)
        return _to_df(c.get_articles_from_feed(limit_per_feed=limit))
    else:
        bc = BatchCrawler(site_name=source, request_delay=0.8)
        return _to_df(bc.fetch_articles(limit=limit))


def get_news(source: str = "cafef", limit: int = 10) -> str:
    """
    Get recent news articles from a Vietnamese news source.

    Args:
        source: News source. Options:
            Finance (sitemap/batch): cafef, vneconomy, dddn, baodautu, tienphong
            Finance (RSS):           cafebiz, vietstock, thoibaotaichinhvietnam, petrotimes
            General (RSS):           vnexpress, dantri, tuoitre, thanhnien, nld, znews,
                                     vietnamnet, petrotimes
            General (sitemap/batch): 24h, nhandan, nguoiquansat, plo, ktsg
        limit: Number of articles (default 10, max 50)
    """
    source = source.lower().strip()
    limit = max(1, min(limit, 50))
    sources_map, rss_sources, _ = _get_source_metadata()
    all_sources = set(sources_map.keys())
    if source not in all_sources:
        return f"[Unknown source '{source}'. Supported: {', '.join(sorted(all_sources))}]"

    try:
        df = _fetch_single(source, limit)
        if df.empty:
            return f"No articles found from '{source}'."
        df = _select_cols(df).head(limit)
        strategy = "RSS" if source in rss_sources else "Sitemap/Batch"
        return (
            f"## News: {source} ({strategy}, {len(df)} articles)\n\n"
            + to_claude_text(df, mode="json", max_rows=limit)
        )
    except ImportError:
        return "[vnstock_news not installed. Run: pip install vnstock_news]"
    except Exception as e:
        return handle_vnstock_error(e, "get_news")


def search_news(
    keyword: str,
    sources: list = None,
    limit_per_source: int = 20,
) -> str:
    """
    Search for articles matching a keyword across multiple Vietnamese news sources.
    Uses AsyncBatchCrawler to fetch all sources concurrently (3-5x faster than sequential).
    Matches keyword against title, description, short_description, tags, category.

    Args:
        keyword: Search term e.g. 'lãi suất', 'VCB', 'bất động sản', 'xuất khẩu'
        sources: Source names to search (default: 6 finance sources).
                 Any of: cafef, vneconomy, cafebiz, vietstock, dddn,
                 thoibaotaichinhvietnam, vnexpress, dantri, tuoitre, thanhnien,
                 nld, vietnamnet, znews, petrotimes, 24h, nhandan, nguoiquansat,
                 baodautu, tienphong, plo, ktsg
        limit_per_source: Articles to fetch per source before filtering (default 20, max 50)
    """
    if not keyword or not keyword.strip():
        return "[keyword cannot be empty]"

    keyword = keyword.strip()
    kw_lower = keyword.lower()
    sources_map, _, _ = _get_source_metadata()
    all_sources = set(sources_map.keys())
    sources = [s.lower().strip() for s in (sources or FINANCE_SOURCES)
               if s.lower().strip() in all_sources][:10]
    limit_per_source = max(5, min(limit_per_source, 50))
    total_n = limit_per_source * len(sources)

    try:
        import pandas as pd

        # Concurrent fetch across all sources
        df_all = _run_async(_async_fetch_multi(sources, top_n=total_n))
        df_all = _to_df(df_all)

        if df_all.empty:
            return f"No articles fetched from {sources}."

        # Filter by keyword
        mask = pd.Series(False, index=df_all.index)
        for col in ["title", "description", "short_description", "tags", "category"]:
            if col in df_all.columns:
                mask |= df_all[col].astype(str).str.lower().str.contains(kw_lower, na=False)

        result = _select_cols(df_all[mask])
        if result.empty:
            return (
                f"No articles matching '{keyword}' in {len(df_all)} fetched articles "
                f"from {sources}."
            )

        if "publish_time" in result.columns:
            result = result.sort_values("publish_time", ascending=False)

        return (
            f"## Search: '{keyword}' — {len(result)} results "
            f"(from {len(df_all)} articles across {len(sources)} sources)\n\n"
            + to_claude_text(result, mode="json", max_rows=50)
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
) -> str:
    """
    Extract trending multi-word phrases from recent financial news.
    Uses AsyncBatchCrawler to fetch all sources concurrently, then
    TrendingAnalyzer for n-gram analysis (falls back to Counter if unavailable).

    Args:
        sources: Sources to analyze (default: finance sources)
        limit_per_source: Articles per source (default 30, max 50)
        top_n: Top N phrases to return (default 30, max 100)
        ngram_range: N-gram sizes e.g. [2, 3, 4]. Default [2, 3, 4].
    """
    sources_map, _, _ = _get_source_metadata()
    all_sources = set(sources_map.keys())
    sources = [s.lower().strip() for s in (sources or FINANCE_SOURCES)
               if s.lower().strip() in all_sources][:8]
    limit_per_source = max(5, min(limit_per_source, 50))
    top_n = max(5, min(top_n, 100))
    ngram_range = ngram_range or [2, 3, 4]
    total_n = limit_per_source * len(sources)

    try:
        import pandas as pd

        # Try TrendingAnalyzer with Vietnamese stopwords
        try:
            import vnstock_news as _vnn
            import os as _os
            from vnstock_news.trending.analyzer import TrendingAnalyzer
            _sw_path = _os.path.join(_os.path.dirname(_vnn.__file__), "config", "vietnamese-stopwords.txt")
            _sw_path = _sw_path if _os.path.exists(_sw_path) else None
            analyzer = TrendingAnalyzer(stop_words_file=_sw_path, min_token_length=3)
            use_analyzer = True
        except ImportError:
            use_analyzer = False

        # Concurrent fetch
        df_all = _run_async(_async_fetch_multi(sources, top_n=total_n))
        df_all = _to_df(df_all)

        if df_all.empty:
            return f"No articles fetched from {sources}."

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
            return f"No text content extracted from {total_articles} fetched articles."

        if use_analyzer:
            trends = analyzer.get_top_trends(top_n=top_n)
            result_df = pd.DataFrame(
                sorted(trends.items(), key=lambda x: x[1], reverse=True),
                columns=["phrase", "count"]
            )
        else:
            import re
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
            f"Sources: {', '.join(sources)} | Articles: {total_articles} | Top {top_n}\n\n"
            + to_claude_text(result_df, mode="table", max_rows=top_n)
        )

    except ImportError:
        return "[vnstock_news not installed. Run: pip install vnstock_news]"
    except Exception as e:
        return handle_vnstock_error(e, "get_trending_keywords")


def get_news_sources() -> str:
    """
    List all 21 supported Vietnamese news sources with fetch strategy and category.
    No parameters required.
    """
    import pandas as pd

    rows = [
        # Finance — Sitemap/Batch
        ("cafef",                  "Finance Portal",    "Sitemap/Batch"),
        ("vneconomy",              "Finance/Business",  "Sitemap/Batch"),
        ("dddn",                   "Business Forum",    "Sitemap/Batch"),
        ("baodautu",               "Investment News",   "Sitemap/Batch"),
        ("tienphong",              "General News",      "Sitemap/Batch"),
        ("nhandan",                "Central Org",       "Sitemap/Batch"),
        ("nguoiquansat",           "News Aggregator",   "Sitemap/Batch"),
        ("24h",                    "News Aggregator",   "Sitemap/Batch"),
        ("plo",                    "Local (HCM)",       "Sitemap/Batch"),
        ("ktsg",                   "Saigon Economy",    "Sitemap/Batch"),
        # Finance & General — RSS
        ("cafebiz",                "Finance Portal",    "RSS"),
        ("vietstock",              "Finance Portal",    "RSS"),
        ("thoibaotaichinhvietnam", "Finance Ministry",  "RSS"),
        ("petrotimes",             "Energy/Industry",   "RSS"),
        ("vnexpress",              "General (Top)",     "RSS"),
        ("dantri",                 "General News",      "RSS"),
        ("tuoitre",                "General News",      "RSS"),
        ("thanhnien",              "General News",      "RSS"),
        ("nld",                    "General News",      "RSS"),
        ("znews",                  "News Aggregator",   "RSS"),
        ("vietnamnet",             "General News",      "RSS"),
    ]

    df = pd.DataFrame(rows, columns=["source_name", "category", "fetch_strategy"])
    return (
        "## Supported News Sources (21 total)\n\n"
        "RSS = fast real-time (fewer fields) | Sitemap/Batch = full content, ~1s/article\n"
        "search_news & get_trending_keywords use AsyncBatchCrawler (concurrent fetch).\n"
        "Default finance sources: cafef, vneconomy, cafebiz, vietstock, dddn, thoibaotaichinhvietnam\n\n"
        + to_claude_text(df, mode="table", max_rows=25)
    )
