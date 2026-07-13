"""Vietnam trading-clock helpers (UTC+7), used for time-adjusted intraday ratios."""

from datetime import datetime, timezone, timedelta

import config

_VN_TZ = timezone(timedelta(hours=config.VN_UTC_OFFSET))


def now_vn() -> datetime:
    """Current time in Vietnam (UTC+7)."""
    return datetime.now(_VN_TZ)


def _at(today: datetime, hm) -> datetime:
    return today.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)


def minutes_elapsed(now: datetime = None) -> float:
    """Real trading minutes elapsed since 9:15, excluding the 11:30–13:00 break.

    Returns 0 before the open and 225 (full session) at/after the close.
    """
    now = now or now_vn()
    open_t = _at(now, config.SESSION_OPEN)
    morning_close = _at(now, config.MORNING_CLOSE)
    afternoon_open = _at(now, config.AFTERNOON_OPEN)
    close_t = _at(now, config.SESSION_CLOSE)

    if now <= open_t:
        return 0.0
    if now >= close_t:
        return float(config.TRADING_MINUTES)
    if now <= morning_close:
        return (now - open_t).total_seconds() / 60
    morning_minutes = (morning_close - open_t).total_seconds() / 60  # 135
    if now <= afternoon_open:
        return morning_minutes
    return morning_minutes + (now - afternoon_open).total_seconds() / 60


def is_trading_hours(now: datetime = None) -> bool:
    """True on weekdays during 9:15–14:45 Vietnam time."""
    now = now or now_vn()
    if now.weekday() >= 5:  # Sat/Sun
        return False
    return _at(now, config.SESSION_OPEN) <= now <= _at(now, config.SESSION_CLOSE)
