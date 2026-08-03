@echo off
REM Shared vnstock MCP service — one warm instance for every Claude session.
REM Started at logon by the "vnstock-mcp-http" scheduled task (see README notes).
REM Run this by hand to start it manually; Ctrl-C to stop.

set "VNSTOCK_MCP_TRANSPORT=http"
if "%VNSTOCK_MCP_HOST%"=="" set "VNSTOCK_MCP_HOST=127.0.0.1"
if "%VNSTOCK_MCP_PORT%"=="" set "VNSTOCK_MCP_PORT=8790"
set "PYTHONIOENCODING=utf-8"
set "FASTMCP_SHOW_SERVER_BANNER=false"

REM Per-day log. The old single 'vnstock-mcp-http.log' was appended to forever
REM and, worse, a single stale handle on it made this launcher fail instantly
REM and silently: the >> redirect is opened by cmd BEFORE python starts, so a
REM locked log meant the service never came up and nothing explained why
REM (observed 2026-08-03 — the file stayed locked with no server process alive
REM and could not even be renamed). A dated name means one bad handle costs at
REM most one day, and the log stops growing without bound.
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "LOGDATE=%%d"
set "LOGFILE=%LOCALAPPDATA%\vnstock-mcp-http-%LOGDATE%.log"

cd /d "%~dp0"
"C:\Users\tkvmai\.venv\Scripts\python.exe" server.py >> "%LOGFILE%" 2>&1
