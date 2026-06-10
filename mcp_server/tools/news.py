"""News and sentiment tools: fetch articles, extract trending keywords."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.df_converter import to_claude_text
from utils.error_handler import handle_vnstock_error

VALID_SOURCES = [
    "cafef", "vnexpress", "tinnhanhchungkhoan", "ndh", "vietstock",
    "dantri", "tuoitre", "vneconomy", "baodautu", "thesaigontimes",
]


def get_news(source: str = "cafef", limit: int = 20) -> str:
    """
    Get recent financial news articles from Vietnamese news sources.

    Args:
        source: News source name. Options: cafef, vnexpress, tinnhanhchungkhoan,
                ndh, vietstock, dantri, tuoitre, vneconomy, baodautu, thesaigontimes
        limit: Number of articles to fetch (max 100)
    """
    source = source.lower().strip()
    limit = max(1, min(limit, 100))

    try:
        from vnstock_news import Crawler
        crawler = Crawler(site_name=source)
        articles = crawler.get_articles(limit=limit)

        if not articles:
            return f"No news articles found from '{source}'."

        import pandas as pd
        if isinstance(articles, pd.DataFrame):
            df = articles
        else:
            df = pd.DataFrame(articles)

        if df.empty:
            return f"No articles returned from '{source}'."

        # Select key columns for display
        display_cols = []
        for col in ["publish_time", "title", "category", "author", "url"]:
            if col in df.columns:
                display_cols.append(col)
        if display_cols:
            df = df[display_cols]

        return (
            f"## News from '{source}' (latest {len(df)} articles)\n\n"
            + to_claude_text(df, mode="json", max_rows=limit)
        )
    except ImportError:
        return "[vnstock_news not installed. Run: pip install vnstock_news]"
    except Exception as e:
        return handle_vnstock_error(e, "get_news")


def get_trending_keywords(source: str = "cafef", limit: int = 50) -> str:
    """
    Extract trending keywords and topics from recent financial news.

    Args:
        source: News source (same options as get_news)
        limit: Number of articles to analyze for trend extraction (max 200)
    """
    source = source.lower().strip()
    limit = max(5, min(limit, 200))

    try:
        from vnstock_news import Crawler
        crawler = Crawler(site_name=source)
        articles = crawler.get_articles(limit=limit)

        if not articles:
            return f"No articles to analyze from '{source}'."

        import pandas as pd
        from collections import Counter

        if isinstance(articles, pd.DataFrame):
            df = articles
        else:
            df = pd.DataFrame(articles)

        if df.empty:
            return "No articles returned."

        # Extract keywords from tags and titles
        keyword_counts = Counter()

        if "tags" in df.columns:
            for tags in df["tags"].dropna():
                if isinstance(tags, list):
                    keyword_counts.update(tags)
                elif isinstance(tags, str):
                    keyword_counts.update([t.strip() for t in tags.split(",") if t.strip()])

        if "title" in df.columns and len(keyword_counts) < 5:
            # Fallback: simple word frequency from titles
            import re
            for title in df["title"].dropna():
                words = re.findall(r"[A-ZÀ-Ỹa-zà-ỹ]{4,}", str(title))
                keyword_counts.update(w.lower() for w in words)

        if not keyword_counts:
            return f"Could not extract keywords from '{source}' articles."

        top_keywords = keyword_counts.most_common(30)
        result_df = pd.DataFrame(top_keywords, columns=["keyword", "count"])

        return (
            f"## Trending Keywords from '{source}' ({len(df)} articles analyzed)\n\n"
            + to_claude_text(result_df, mode="table", max_rows=30)
        )
    except ImportError:
        return "[vnstock_news not installed. Run: pip install vnstock_news]"
    except Exception as e:
        return handle_vnstock_error(e, "get_trending_keywords")
