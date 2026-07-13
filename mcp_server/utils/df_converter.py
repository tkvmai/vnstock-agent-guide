"""Utilities to convert pandas DataFrames and dicts to Claude-readable text."""

import pandas as pd
import numpy as np


def _format_number(val):
    """Format numeric values for Vietnamese stock context."""
    if pd.isna(val) or val is None:
        return "N/A"
    if isinstance(val, (int, float)):
        if abs(val) >= 1e9:
            return f"{val / 1e9:.2f}B"
        if abs(val) >= 1e6:
            return f"{val / 1e6:.2f}M"
        if isinstance(val, float) and abs(val) < 1000:
            return f"{val:.2f}"
        return f"{val:,.0f}"
    return str(val)


def df_to_markdown(df: pd.DataFrame, max_rows: int = 30) -> str:
    """Convert DataFrame to markdown table, truncated to max_rows."""
    if df is None or df.empty:
        return "(No data)"
    total = len(df)
    if total > max_rows:
        df = df.head(max_rows).copy()
        note = f"[Showing top {max_rows} of {total} rows]\n\n"
    else:
        note = ""
    try:
        return note + df.to_markdown(index=False, floatfmt=".2f")
    except Exception:
        return note + df.to_string(index=False)


def df_to_json(df: pd.DataFrame, max_rows: int = 50) -> str:
    """Convert DataFrame to JSON records string (for news, screening)."""
    if df is None or df.empty:
        return "[]"
    df = df.head(max_rows).copy()
    # Replace NaN with None for clean JSON
    df = df.where(pd.notnull(df), None)
    return df.to_json(orient="records", force_ascii=False, indent=2, date_format="iso")


def dict_to_text(data: dict) -> str:
    """Convert a dict to readable key: value lines."""
    if not data:
        return "(No data)"
    lines = []
    for k, v in data.items():
        if v is not None and v != "" and not (isinstance(v, float) and np.isnan(v)):
            lines.append(f"**{k}**: {v}")
    return "\n".join(lines)


def to_claude_text(obj, mode: str = "table", max_rows: int = 30) -> str:
    """
    Main converter: dispatch to appropriate formatter.
    mode: "table" (markdown), "json" (records), "text" (dict narrative)
    """
    if isinstance(obj, pd.DataFrame):
        if mode == "json":
            return df_to_json(obj, max_rows)
        return df_to_markdown(obj, max_rows)
    elif isinstance(obj, dict):
        if mode == "json":
            import json
            return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
        return dict_to_text(obj)
    elif isinstance(obj, list):
        try:
            return df_to_json(pd.DataFrame(obj), max_rows)
        except Exception:
            return str(obj)
    return str(obj)
