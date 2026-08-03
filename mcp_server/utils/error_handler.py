"""Centralized error handling for vnstock MCP tools."""


def handle_vnstock_error(e: Exception, tool_name: str, symbol: str = "") -> str:
    """Return a user-friendly error string instead of raising."""
    context = f" for '{symbol.upper()}'" if symbol else ""
    msg = str(e).lower()

    if "429" in msg or "rate limit" in msg or "too many" in msg:
        return (
            f"[Rate limit hit in {tool_name}{context}. "
            "Please wait 30–60 seconds and retry.]"
        )
    if "401" in msg or "403" in msg or "authentication" in msg or "license" in msg or "unauthorized" in msg:
        return (
            f"[Authentication error in {tool_name}{context}. "
            "Check your vnstock license credentials in ~/.vnstock/auth_state.json.]"
        )
    # HTTP status errors are about the ENDPOINT, not the symbol — "404 - Not Found"
    # used to fall through to the symbol branch and produce a nonsense
    # "Symbol '' not found" for symbol-less tools like get_macro_indicator.
    if "404" in msg or "500" in msg or "502" in msg or "503" in msg:
        return (
            f"[Upstream data source returned an HTTP error in {tool_name}{context}: "
            f"{str(e)[:160]}. The endpoint may have moved or been retired — this is "
            "not a problem with your arguments.]"
        )
    if symbol and ("not found" in msg or "invalid symbol" in msg or "no data" in msg):
        return (
            f"[Symbol '{symbol.upper()}' not found or no data available{context}. "
            "Use get_index_members('VN30') to browse valid symbols.]"
        )
    if "timeout" in msg or "timed out" in msg or "connection" in msg:
        return (
            f"[Network error in {tool_name}{context}: {str(e)[:160]} "
            "The data source may be temporarily unavailable. Please retry.]"
        )
    if "not found" in msg or "no data" in msg:
        return f"[No data available in {tool_name}: {str(e)[:200]}]"
    if "modulenotfounderror" in type(e).__name__.lower() or "importerror" in type(e).__name__.lower():
        lib = str(e).split("'")[1] if "'" in str(e) else "unknown"
        return (
            f"[Library '{lib}' not installed. "
            f"Run: pip install {lib}]"
        )

    return (
        f"[Error in {tool_name}{context}: "
        f"{type(e).__name__}: {str(e)[:300]}]"
    )


def validate_date(date_str: str, param_name: str = "date") -> str | None:
    """Return error string if date format is invalid, else None."""
    from datetime import datetime
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return None
    except ValueError:
        return f"[Invalid {param_name} format: '{date_str}'. Use YYYY-MM-DD (e.g. 2025-01-01)]"
