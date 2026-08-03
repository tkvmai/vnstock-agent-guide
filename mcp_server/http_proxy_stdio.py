"""
Featherweight stdio -> HTTP proxy for the shared vnstock MCP service.

Why this exists: stdio MCP servers are never shared — every client session
spawns its own copy of server.py, each paying the full cold start (spawned
worker + numpy native-extension import + warm-up fetch). On Windows those cold
starts contend with each other and get dramatically slower, which is how a
session ends up missing its client's connect timeout and losing vnstock
entirely for its whole lifetime.

So: run ONE shared `server.py` with VNSTOCK_MCP_TRANSPORT=http (paying the cold
start once, at service start), and point stdio-only clients at this proxy. It
imports nothing heavy — no vnstock, no pandas, no numpy — so it starts in ~1s
and simply forwards tool calls to the already-warm service.

Clients that speak HTTP directly should skip this and use the URL.

Env:
  VNSTOCK_MCP_URL   full URL of the shared service (default derived from
                    VNSTOCK_MCP_HOST/VNSTOCK_MCP_PORT, i.e.
                    http://127.0.0.1:8790/mcp)
"""

import os
import sys

from fastmcp import FastMCP

_host = os.environ.get("VNSTOCK_MCP_HOST", "127.0.0.1")
_port = os.environ.get("VNSTOCK_MCP_PORT", "8790")
URL = os.environ.get("VNSTOCK_MCP_URL", f"http://{_host}:{_port}/mcp")


if __name__ == "__main__":
    print(f"[vnstock-mcp-proxy] forwarding stdio -> {URL}", file=sys.stderr, flush=True)
    # as_proxy mirrors the remote server's tools/resources/prompts through this
    # local stdio process. If the shared service is down, tool calls surface a
    # connection error rather than silently returning nothing.
    FastMCP.as_proxy(URL, name="vnstock").run()
