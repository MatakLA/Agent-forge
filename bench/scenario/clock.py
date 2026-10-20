"""Dates, durations and time arithmetic for tasklog.

Public interface: today(), now_iso(), parse_date(text), parse_duration(text), format_minutes(m), week_start(d), month_start(d),
date_range(a, b). Environment TASKLOG_TODAY / TASKLOG_NOW pin the clock (tests, scripts).
Invariants: durations are whole minutes; parse_date accepts only YYYY-MM-DD; weeks start on Monday.
"""
import datetime as dt
import os
import re

_DURATION = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?")


def today():
    pinned = os.environ.get("TASKLOG_TODAY")
    return parse_date(pinned) if pinned else dt.date.today()


def now_iso():
    pinned = os.environ.get("TASKLOG_NOW")
    if pinned:
        return pinned
    return dt.datetime.now().strftime("%Y-%m-%dT%H:%M")


def parse_date(text):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(text)):
        raise ValueError(f"invalid date {text!r}, expected YYYY-MM-DD")
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"invalid date {text!r}, no such day")


def parse_duration(text):
    """'90' and '90m' are minutes, '2h' hours, '1h30m' both. Returns whole minutes (> 0)."""
    s = str(text).strip().lower()
    if s.isdigit():
        minutes = int(s)
    else:
        m = _DURATION.fullmatch(s)
        if not m or not s:
            raise ValueError(f"invalid duration {text!r}")
        minutes = int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    if minutes <= 0:
        raise ValueError("duration must be positive")
    return minutes


def format_minutes(minutes):
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h}h{m:02d}m"
    return f"{h}h" if h else f"{m}m"


def week_start(day):
    return day - dt.timedelta(days=day.weekday())


def month_start(day):
    return day.replace(day=1)


def date_range(first, last):
    """Every date from first to last inclusive."""
    d = first
    while d <= last:
        yield d
        d += dt.timedelta(days=1)
