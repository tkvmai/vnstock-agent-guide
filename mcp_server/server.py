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


threading.Thread(target=_warm_up, daemon=True).start()

from fastmcp import FastMCP

# Import tool functions
from tools.market_data import get_stock_price, get_intraday, get_market_overview
from tools.fundamentals import get_company_info, get_financial_ratios, get_income_statement, get_balance_sheet
from tools.screening import screen_stocks, get_index_members, get_stocks_by_industry, get_industry_list, get_money_flow, get_market_sentiment
from tools.technical import get_technical_indicators
from tools.news import get_news, get_trending_keywords
from tools.portfolio import compare_stocks, get_correlation

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
mcp.tool()(get_stock_price)
mcp.tool()(get_intraday)
mcp.tool()(get_market_overview)

# ── Group 2: Fundamental Analysis ────────────────────────────────────────────
mcp.tool()(get_company_info)
mcp.tool()(get_financial_ratios)
mcp.tool()(get_income_statement)
mcp.tool()(get_balance_sheet)

# ── Group 3: Stock Screening ─────────────────────────────────────────────────
mcp.tool()(screen_stocks)
mcp.tool()(get_index_members)
mcp.tool()(get_stocks_by_industry)
mcp.tool()(get_industry_list)
mcp.tool()(get_money_flow)
mcp.tool()(get_market_sentiment)

# ── Group 4: Technical Analysis ──────────────────────────────────────────────
mcp.tool()(get_technical_indicators)

# ── Group 5: News & Sentiment ────────────────────────────────────────────────
mcp.tool()(get_news)
mcp.tool()(get_trending_keywords)

# ── Group 6: Portfolio & Risk ────────────────────────────────────────────────
mcp.tool()(compare_stocks)
mcp.tool()(get_correlation)


if __name__ == "__main__":
    mcp.run()
