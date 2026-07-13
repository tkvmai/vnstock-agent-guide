"""
Pipeline tools — small-batch data collection and local file inspection.

ARCHITECTURE NOTE:
vnstock_pipeline is primarily a background automation framework:
  - Bulk pipelines (100+ tickers) → hours-long, run as cron/Docker/systemd
  - WebSocket streaming → infinite loop, not suitable for MCP request-response
  - Scheduled daily collection → persistent scheduler process

MCP-SUITABLE USE CASES (bounded, stateless, return quickly):
  1. run_pipeline_task  — small batch ≤10 tickers, finishes in seconds/minutes
  2. inspect_data_file  — read schema + stats of a local CSV/Parquet pipeline export
  3. query_data_file    — filter rows from a local pipeline export file
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.df_converter import to_claude_text
from utils.error_handler import handle_vnstock_error

TASK_TYPES = {
    "ohlcv":     "Daily OHLCV price history → ./data/ohlcv/{ticker}.csv",
    "financial": "5 financial statements (balance_sheet, income_statement, cash_flow, ratio) → ./data/financial/",
    "intraday":  "Intraday tick data → ./data/intraday/{ticker}.csv",
}


def run_pipeline_task(
    tickers: list,
    task_type: str = "ohlcv",
    start: str = None,
    end: str = None,
    output_path: str = "./data",
    interval: str = "1D",
) -> str:
    """
    Run a small vnstock_pipeline data collection task for up to 10 tickers.
    Results are saved as CSV files in output_path. Not suitable for bulk runs —
    for 100+ tickers use a background script or Docker instead.

    Args:
        tickers: List of stock tickers (max 10, e.g. ['VCB', 'TCB', 'HPG'])
        task_type: Pipeline task type:
            'ohlcv'     - Daily OHLCV price history
            'financial' - All 5 financial statements (balance_sheet, income_statement, cash_flow, ratio)
            'intraday'  - Intraday tick data (current session or latest)
        start: Start date YYYY-MM-DD (required for ohlcv/intraday, ignored for financial)
        end: End date YYYY-MM-DD (required for ohlcv/intraday)
        output_path: Directory to save CSV files (default './data')
        interval: OHLCV interval — '1D', '1W', '1M', '1H', '15m', '5m', '1m' (ohlcv only)
    """
    task_type = task_type.lower().strip()
    if task_type not in TASK_TYPES:
        return f"[Unknown task_type '{task_type}'. Choose: {', '.join(TASK_TYPES)}]"

    if not tickers:
        return "[tickers list cannot be empty]"

    tickers = [t.upper().strip() for t in tickers[:10]]  # hard cap at 10

    try:
        if task_type == "ohlcv":
            if not start or not end:
                return "[ohlcv task requires start and end dates (YYYY-MM-DD)]"

            from vnstock_pipeline.tasks.ohlcv import run_task
            run_task(tickers, start=start, end=end, interval=interval)

        elif task_type == "financial":
            from vnstock_pipeline.tasks.financial import run_financial_task
            run_financial_task(tickers)

        elif task_type == "intraday":
            from vnstock_pipeline.tasks.intraday import run_intraday_task
            # EOD mode: fetch once and exit (no infinite loop)
            run_intraday_task(tickers, mode="EOD")

        return (
            f"## Pipeline completed: {task_type.upper()}\n"
            f"Tickers: {', '.join(tickers)}\n"
            f"Output: {output_path}/{task_type}/\n\n"
            f"Use `inspect_data_file` or `query_data_file` to explore results."
        )

    except ImportError:
        return (
            "[vnstock_pipeline not installed.\n"
            "Install: pip install vnstock_pipeline\n"
            "Requires sponsored (silver/golden/diamond) license.]"
        )
    except Exception as e:
        return handle_vnstock_error(e, f"run_pipeline_task({task_type})", str(tickers))


def inspect_data_file(file_path: str, sample_rows: int = 5) -> str:
    """
    Inspect a local data file exported by a pipeline (CSV or Parquet).
    Returns schema, row count, date range, null statistics, and a data sample.

    Args:
        file_path: Absolute or relative path to the file (e.g. './data/ohlcv/VCB.csv')
        sample_rows: Number of sample rows to show (default 5)
    """
    if not os.path.exists(file_path):
        return f"[File not found: '{file_path}']"

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in (".csv", ".parquet"):
        return f"[Unsupported file type '{ext}'. Supported: .csv, .parquet]"

    try:
        import pandas as pd

        if ext == ".csv":
            df = pd.read_csv(file_path)
        else:
            df = pd.read_parquet(file_path)

        if df.empty:
            return f"File '{file_path}' is empty."

        # Schema table
        schema_rows = []
        for col in df.columns:
            null_pct = df[col].isna().mean() * 100
            schema_rows.append({
                "column": col,
                "dtype": str(df[col].dtype),
                "non_null": int(df[col].notna().sum()),
                "null_%": f"{null_pct:.1f}%",
                "sample": str(df[col].dropna().iloc[0]) if df[col].notna().any() else "—",
            })
        schema_df = pd.DataFrame(schema_rows)

        # Date range if time column exists
        date_info = ""
        for col in ["time", "date", "trading_date", "publish_time"]:
            if col in df.columns:
                try:
                    times = pd.to_datetime(df[col], errors="coerce").dropna()
                    if not times.empty:
                        date_info = f"Date range : {times.min().date()} → {times.max().date()}\n"
                except Exception:
                    pass
                break

        size_kb = os.path.getsize(file_path) / 1024
        header = (
            f"## File: {os.path.basename(file_path)}\n"
            f"Path      : {file_path}\n"
            f"Format    : {ext[1:].upper()} | Size: {size_kb:.1f} KB\n"
            f"Rows      : {len(df):,} | Columns: {len(df.columns)}\n"
            f"{date_info}\n"
            f"### Schema\n"
        )

        sample = df.head(sample_rows)
        return (
            header
            + to_claude_text(schema_df, mode="table", max_rows=len(schema_df))
            + f"\n### Sample ({sample_rows} rows)\n"
            + to_claude_text(sample, mode="table", max_rows=sample_rows)
        )

    except Exception as e:
        return handle_vnstock_error(e, "inspect_data_file", file_path)


def query_data_file(
    file_path: str,
    condition: str,
    limit: int = 50,
) -> str:
    """
    Filter rows from a local CSV or Parquet pipeline export using a pandas query expression.
    Useful for exploring data after running run_pipeline_task.

    Args:
        file_path: Path to the file (CSV or Parquet)
        condition: pandas query string (e.g. 'close > 50000 and volume > 1000000')
                   Column names must match the file's actual columns.
                   Date filter example: 'time >= "2024-06-01"'
        limit: Maximum rows to return (default 50, max 500)
    """
    if not os.path.exists(file_path):
        return f"[File not found: '{file_path}']"

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in (".csv", ".parquet"):
        return f"[Unsupported file type '{ext}'. Supported: .csv, .parquet]"

    limit = max(1, min(limit, 500))

    try:
        import pandas as pd

        if ext == ".csv":
            df = pd.read_csv(file_path)
        else:
            df = pd.read_parquet(file_path)

        if df.empty:
            return f"File '{file_path}' is empty."

        # Parse time column if present
        for col in ["time", "date", "trading_date"]:
            if col in df.columns:
                try:
                    df[col] = pd.to_datetime(df[col], errors="coerce")
                except Exception:
                    pass
                break

        result = df.query(condition)
        total_matches = len(result)
        display = result.head(limit)

        if display.empty:
            return (
                f"No rows matched condition: `{condition}`\n"
                f"File has {len(df):,} rows. Available columns: {', '.join(df.columns)}"
            )

        return (
            f"## Query: `{condition}`\n"
            f"File: {os.path.basename(file_path)} ({len(df):,} rows total)\n"
            f"Matched: {total_matches:,} rows | Showing: {len(display)}\n\n"
            + to_claude_text(display, mode="table", max_rows=limit)
        )

    except Exception as e:
        # Give a helpful error if query syntax is wrong
        err = str(e)
        if "UndefinedVariableError" in err or "name" in err.lower():
            import pandas as pd
            try:
                df2 = pd.read_csv(file_path) if ext == ".csv" else pd.read_parquet(file_path)
                cols = ", ".join(df2.columns)
                return f"[Query error: {err}\nAvailable columns: {cols}]"
            except Exception:
                pass
        return handle_vnstock_error(e, "query_data_file", file_path)
