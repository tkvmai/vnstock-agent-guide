"""
vnstock MCP Server — Vietnamese Stock Market Data for AI Agents
Entry point for Claude Desktop, Claude Code, and other MCP clients.
"""

import sys
import os
import subprocess
import threading

# Ensure tool modules resolve correctly when run as a script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# vnai (telemetry pulled in by vnstock_data) spawns `git` subprocesses without
# redirecting stdin. Under MCP stdio transport, children inherit the protocol
# pipe and never exit, deadlocking every tool call. Force DEVNULL stdin for any
# child process that doesn't explicitly request one.
_orig_popen_init = subprocess.Popen.__init__

def _popen_init_no_inherit_stdin(self, *args, **kwargs):
    if kwargs.get("stdin") is None:
        kwargs["stdin"] = subprocess.DEVNULL
    _orig_popen_init(self, *args, **kwargs)

subprocess.Popen.__init__ = _popen_init_no_inherit_stdin

# vnstock's own __init__.py calls update_notice() at import time, which checks
# `pip list` per dependency (subprocess, ~1-3s each on Windows) and then hits
# pypi.org / vnstocks.com to check for newer versions. Combined this can take
# 10+ seconds before the server even finishes importing, exceeding the MCP
# client's tool-call timeout. Patching update_notice after the fact is too
# late — importing the submodule to patch it first triggers `import vnstock`,
# which runs __init__.py (and update_notice) before our patch line executes.
# Instead, pre-register a stub module in sys.modules so vnstock's
# `from vnstock.core.utils.upgrade import update_notice` picks up our no-op.
import types as _types
_fake_upgrade = _types.ModuleType("vnstock.core.utils.upgrade")
_fake_upgrade.update_notice = lambda *a, **k: None
_fake_upgrade.show_full_notice = lambda *a, **k: None
sys.modules["vnstock.core.utils.upgrade"] = _fake_upgrade

# Defense in depth: also fast-fail any stray request to these hosts in case
# other code paths (e.g. vnstock_data) call the update-check directly.
import requests as _requests
_orig_requests_get = _requests.get
_BLOCKED_NOTICE_HOSTS = ("pypi.org", "vnstocks.com")

def _requests_get_no_update_check(url, *args, **kwargs):
    if isinstance(url, str) and any(h in url for h in _BLOCKED_NOTICE_HOSTS):
        raise _requests.exceptions.ConnectionError("vnstock update-check disabled by MCP server")
    return _orig_requests_get(url, *args, **kwargs)

_requests.get = _requests_get_no_update_check


def _warm_up():
    """Pre-import heavy vnstock libraries so the first tool call is fast."""
    try:
        import vnstock_data  # noqa: F401
    except Exception:
        pass
    try:
        import vnstock  # noqa: F401
    except Exception:
        pass


# NOTE: the main process no longer runs `_warm_up` in a background thread.
# Under the worker architecture, data fetches happen in the spawned worker
# (which does its own pre-warm), not here — so warming vnstock in the parent
# is useless, and its concurrent import of the numpy/scipy/seaborn native DLLs
# only adds parent-side load while the worker is cold-starting. Kept the
# function for opt-in use via VNSTOCK_MCP_FORCE_WARMUP=1.
if os.environ.get("VNSTOCK_MCP_FORCE_WARMUP") == "1":
    threading.Thread(target=_warm_up, daemon=True).start()

from fastmcp import FastMCP
import functools
from process_timeout import run_with_timeout

# Some vnstock data-source code paths deadlock/hang instead of raising when
# called through FastMCP's tool dispatch on a freshly-started interpreter —
# the same call returns fine once that interpreter has already run it (or a
# similar real-data call) once before. So real-data tools are routed through
# a single long-lived worker process (see process_timeout.py) that keeps
# warm state across calls like a normal long-running MCP session would,
# while still being a real OS process we can kill if a call gets wedged —
# without crashing the main MCP server the way an abandoned ThreadPoolExecutor
# thread did (it could finish late and try to deliver a second response to
# an already-completed request).
def with_timeout(func):
    # No fixed timeout here: run_with_timeout picks the budget adaptively — a
    # generous one while the worker is still cold-starting (so the first call
    # returns real data instead of failing), then the normal short budget once
    # the worker signals it is warm.
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return run_with_timeout(func, args, kwargs)
    return wrapper

# Import tool functions
from tools.market_data import get_stock_price, get_intraday, get_market_overview, get_price_board, get_foreign_trade, get_insider_deals, get_stock_summary, get_proprietary_flow
from tools.fundamentals import get_company_info, get_financial_ratios, get_income_statement, get_balance_sheet, get_cash_flow, get_shareholders, get_company_officers, get_company_events, get_company_news
from tools.screening import screen_stocks, get_index_members, get_stocks_by_industry, get_industry_list, get_money_flow, get_market_sentiment, get_screener_criteria, filter_stocks, get_market_valuation
from tools.technical import get_technical_indicators
from tools.news import get_news, get_trending_keywords, search_news, get_news_sources, get_article, get_news_archive
from tools.portfolio import compare_stocks, get_correlation
from tools.macro import get_macro_indicator, get_commodity_price, get_commodity_impact
from tools.pipeline import run_pipeline_task, inspect_data_file, query_data_file

# Initialize MCP server
mcp = FastMCP(
    name="vnstock",
    instructions=(
        "Vietnamese stock market data server powered by vnstock ecosystem. "
        "Provides real-time and historical data for stocks listed on HOSE, HNX, and UPCOM exchanges. "
        "All prices are in VND. Dates must be in YYYY-MM-DD format. "
        "Symbols are Vietnamese stock tickers (e.g. TCB, VCB, HPG, VNM, VNINDEX)."
    ),
)

# ── Group 1: Market Data ──────────────────────────────────────────────────────
mcp.tool()(with_timeout(get_stock_price))
mcp.tool()(with_timeout(get_intraday))
mcp.tool()(with_timeout(get_market_overview))
mcp.tool()(with_timeout(get_price_board))
mcp.tool()(with_timeout(get_foreign_trade))
mcp.tool()(with_timeout(get_insider_deals))
mcp.tool()(with_timeout(get_stock_summary))
mcp.tool()(with_timeout(get_proprietary_flow))

# ── Group 2: Fundamental Analysis ────────────────────────────────────────────
mcp.tool()(with_timeout(get_company_info))
mcp.tool()(with_timeout(get_financial_ratios))
mcp.tool()(with_timeout(get_income_statement))
mcp.tool()(with_timeout(get_balance_sheet))
mcp.tool()(with_timeout(get_cash_flow))
mcp.tool()(with_timeout(get_shareholders))
mcp.tool()(with_timeout(get_company_officers))
mcp.tool()(with_timeout(get_company_events))
mcp.tool()(with_timeout(get_company_news))

# ── Group 3: Stock Screening ─────────────────────────────────────────────────
mcp.tool()(with_timeout(screen_stocks))
mcp.tool()(with_timeout(get_index_members))
mcp.tool()(with_timeout(get_stocks_by_industry))
mcp.tool()(with_timeout(get_industry_list))
mcp.tool()(with_timeout(get_money_flow))
mcp.tool()(with_timeout(get_market_sentiment))
mcp.tool()(with_timeout(get_screener_criteria))
mcp.tool()(with_timeout(filter_stocks))
mcp.tool()(with_timeout(get_market_valuation))

# ── Group 4: Technical Analysis ──────────────────────────────────────────────
mcp.tool()(with_timeout(get_technical_indicators))

# ── Group 5: News & Sentiment ────────────────────────────────────────────────
# NOT routed through the vnstock worker. These tools need only vnstock_news +
# aiohttp — none of vnstock's warm in-process state — so the worker bought them
# nothing while imposing its two costs: the shared 25s per-call budget (a
# sitemap crawl legitimately needs longer, and used to be killed with NOTHING
# returned) and head-of-line blocking (one slow news call stalled every
# market-data call behind it in the single worker queue). They enforce their own
# wall-clock deadline internally and always return partial results instead of
# hanging, so running them in FastMCP's own threadpool is bounded and isolated.
mcp.tool()(get_news)
mcp.tool()(search_news)
mcp.tool()(get_trending_keywords)
mcp.tool()(get_news_sources)
mcp.tool()(get_article)
mcp.tool()(get_news_archive)

# ── Group 6: Portfolio & Risk ────────────────────────────────────────────────
mcp.tool()(with_timeout(compare_stocks))
mcp.tool()(with_timeout(get_correlation))

# ── Group 7: Macro & Commodities ─────────────────────────────────────────────
mcp.tool()(with_timeout(get_macro_indicator))
mcp.tool()(with_timeout(get_commodity_price))
mcp.tool()(with_timeout(get_commodity_impact))

# ── Group 8: Pipeline & Data Files ───────────────────────────────────────────
mcp.tool()(with_timeout(run_pipeline_task))
mcp.tool()(with_timeout(inspect_data_file))
mcp.tool()(with_timeout(query_data_file))


if __name__ == "__main__":
    from datetime import date, timedelta
    from process_timeout import start_worker, wait_until_warm

    # Pre-warm the worker with one real fetch so its spawn re-import and
    # vnstock's slow first-call cost are paid at startup, not on the client's
    # first tool call (which would otherwise time out while cold).
    _warm_end = date.today().isoformat()
    _warm_start = (date.today() - timedelta(days=10)).isoformat()
    start_worker(warmup=(get_stock_price, ("VNM", _warm_start, _warm_end, "1D"), {}))

    # Wait for the worker to warm BEFORE starting mcp.run(). Critical on
    # Windows: FastMCP's event loop running concurrently with the worker's
    # first numpy/native-extension import slows that import from ~15s to
    # 2-3 minutes. Bounded so a stuck worker can't hang startup — mcp.run()
    # proceeds regardless and the adaptive per-call timeout covers the rest.
    wait_until_warm(float(os.environ.get("VNSTOCK_WARM_WAIT", "60")))

    # Transport. Default stays stdio (one private server per client, spawned by
    # the client itself). Set VNSTOCK_MCP_TRANSPORT=http to run instead as a
    # single shared long-lived service that every Claude session connects to
    # over HTTP: the cold start above is then paid ONCE, at service start,
    # rather than by every session — which matters because concurrent cold
    # starts contend on the same slow Windows native-extension import and make
    # each other worse (that contention is what makes sessions miss the
    # client's connect timeout and drop the server entirely).
    _transport = os.environ.get("VNSTOCK_MCP_TRANSPORT", "stdio").lower()
    if _transport in ("http", "streamable-http", "sse"):
        mcp.run(
            transport=_transport,
            host=os.environ.get("VNSTOCK_MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("VNSTOCK_MCP_PORT", "8790")),
        )
    else:
        mcp.run()
