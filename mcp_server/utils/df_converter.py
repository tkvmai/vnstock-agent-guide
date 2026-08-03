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


def _has_meaningful_index(df: pd.DataFrame) -> bool:
    """
    True when the index carries information that would be LOST by rendering with
    index=False.

    This matters because several upstream frames put their key in the index
    rather than a column:
      - CommodityPrice.* -> DatetimeIndex named 'time' (the only date reference)
      - returns.corr()    -> symbol names as the row labels
      - price frames keyed by date in compare_stocks
    Rendering those with index=False silently dropped the dates / row labels and
    produced tables that could not be interpreted at all.

    A plain positional RangeIndex carries nothing, so it stays hidden.
    """
    idx = df.index
    if isinstance(idx, pd.MultiIndex):
        return True
    if idx.name:  # explicitly named -> meaningful even if numeric
        return True
    if isinstance(idx, pd.RangeIndex):
        return False
    try:
        # An UNNAMED integer index is positional bookkeeping, never data.
        # sort_values() and boolean filtering both leave a shuffled or gappy
        # integer index behind (get_money_flow sorts by value_1d; industry
        # lookups filter by level), and treating those as meaningful rendered
        # a junk 'index' column of row numbers.
        # Every genuinely meaningful index in this codebase is either named or
        # non-integer (DatetimeIndex for time series, object Index for symbol
        # labels), so this exclusion is safe; a caller with a meaningful
        # integer key can name it or pass index_label.
        if pd.api.types.is_integer_dtype(idx):
            return False
    except Exception:
        pass
    return True


def df_to_markdown(df: pd.DataFrame, max_rows: int = 30, index_label: str = None) -> str:
    """
    Convert DataFrame to markdown table, truncated to max_rows.

    A meaningful index (dates, symbol labels) is materialized as the first
    column so it survives rendering; a positional RangeIndex is still hidden.
    """
    if df is None or df.empty:
        return "(No data)"

    if _has_meaningful_index(df):
        df = df.reset_index()
        # reset_index() names an unnamed index 'index' — relabel it if asked.
        if index_label and "index" in df.columns:
            df = df.rename(columns={"index": index_label})

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
    if _has_meaningful_index(df):
        df = df.reset_index()
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


def drop_all_nan_columns(df: pd.DataFrame):
    """
    Remove columns that are entirely NaN, returning (frame, dropped_names).

    Some upstream frames ship placeholder columns that are never populated
    (e.g. Insights().flow.foreign() always returns value_1m/3m/6m as all-NaN).
    Rendering them wastes width and reads as missing data rather than as a
    field the source does not publish.
    """
    if df is None or df.empty:
        return df, []
    all_nan = [c for c in df.columns if df[c].isna().all()]
    if not all_nan:
        return df, []
    return df.drop(columns=all_nan), all_nan


def to_claude_text(obj, mode: str = "table", max_rows: int = 30, index_label: str = None) -> str:
    """
    Main converter: dispatch to appropriate formatter.
    mode: "table" (markdown), "json" (records), "text" (dict narrative)
    """
    if isinstance(obj, pd.DataFrame):
        if mode == "json":
            return df_to_json(obj, max_rows)
        return df_to_markdown(obj, max_rows, index_label=index_label)
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
