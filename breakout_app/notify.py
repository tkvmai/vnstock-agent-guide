"""Telegram Bot API notifier for outbound alerts.

Credentials are read from environment variables (TELEGRAM_BOT_TOKEN /
TELEGRAM_CHAT_ID) or from breakout_app/data/telegram_config.json — never
hardcoded. Uses stdlib urllib so there is no extra dependency. If credentials
are missing, send is a no-op that returns False.
"""

import json
import os
import urllib.request

import config


def _file_cfg():
    try:
        with open(config.TELEGRAM_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _token():
    return os.environ.get("TELEGRAM_BOT_TOKEN") or _file_cfg().get("bot_token")


def _chat_ids():
    """Return a list of chat ids. Accepts a single id or a list; env var
    TELEGRAM_CHAT_ID may be comma-separated."""
    env = os.environ.get("TELEGRAM_CHAT_ID")
    if env:
        raw = [c.strip() for c in env.split(",")]
    else:
        raw = _file_cfg().get("chat_ids") or _file_cfg().get("chat_id")
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    return [str(c).strip() for c in raw if str(c).strip()]


# Kill switch cho môi trường dev (04/09): run.py --no-telegram đặt cờ này (hoặc env
# BREAKOUT_NO_TELEGRAM=1) → mọi tin bị chặn tại đây, in preview ra console thay vì gửi.
# Sinh ra từ sự cố tin đôi 10:00 04/09 (app Windows quên tắt chạy song song server).
DISABLED = os.environ.get("BREAKOUT_NO_TELEGRAM") == "1"


def is_configured() -> bool:
    return bool(_token() and _chat_ids())


def _send_one(tok: str, chat_id: str, text: str) -> bool:
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{tok}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def send_telegram(text: str) -> bool:
    if DISABLED or os.environ.get("BREAKOUT_NO_TELEGRAM") == "1":
        preview = text.replace(chr(10), " | ")[:200]
        print(f"[notify] --no-telegram: CHẶN tin ({len(text)} ký tự): {preview}…")
        return False
    """Send an HTML message to every configured chat. Returns True if at least
    one delivery succeeded."""
    tok = _token()
    chat_ids = _chat_ids()
    if not (tok and chat_ids):
        return False
    # Materialise into a list FIRST: any() over a generator short-circuits on the
    # first success, so a lazy form would never send to the remaining chats.
    results = [_send_one(tok, cid, text) for cid in chat_ids]
    return any(results)
