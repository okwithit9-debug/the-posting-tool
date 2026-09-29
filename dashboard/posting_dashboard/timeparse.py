"""Defensive parsing of human datetimes shown on native scheduled lists.

Returns ``(datetime | None, date_only: bool)``. Anything ambiguous returns
``None`` — callers must treat that as *unknown*, never guess.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, tzinfo
from typing import Optional

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_TIME = r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?"
_TIME24 = r"(\d{1,2}):(\d{2})(?!\s*[ap]\.?\s*m)"

_RE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?")
_RE_MON_D_Y_T = re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})(?:,?\s*(?:at\s+)?{_TIME})?", re.I)
_RE_D_MON_Y_T = re.compile(rf"\b(\d{{1,2}})\s+{_MON},?\s+(\d{{4}})(?:,?\s*(?:at\s+)?{_TIME})?", re.I)
_RE_MON_D_T = re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s*(?:at\s+)?{_TIME})?", re.I)
_RE_NUM = re.compile(rf"\b(\d{{1,2}})/(\d{{1,2}})/(\d{{2,4}})(?:,?\s*(?:at\s+)?{_TIME})?", re.I)
_RE_REL = re.compile(rf"\b(today|tomorrow)\b,?\s*(?:at\s+)?{_TIME}", re.I)
_RE_REL24 = re.compile(rf"\b(today|tomorrow)\b,?\s*(?:at\s+)?{_TIME24}", re.I)


def _h(hour: str, minute: Optional[str], ampm: Optional[str]) -> Optional[tuple[int, int]]:
    try:
        h = int(hour)
        m = int(minute or 0)
    except (TypeError, ValueError):
        return None
    if ampm:
        if not 1 <= h <= 12:
            return None
        a = ampm.lower()
        if a == "p" and h != 12:
            h += 12
        if a == "a" and h == 12:
            h = 0
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h, m


def _trailing_24h(rest: str):
    """A time right after a date with no am/pm: 24h clock, or 'bad' if 1-12
    (ambiguous 12h time with the am/pm missing -> unknown)."""
    m = re.match(r"\s*,?\s*(?:at\s+)?(\d{1,2}):(\d{2})\b", rest)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h < 13 and h != 0:
        return "bad"
    if h > 23 or mi > 59:
        return "bad"
    return h, mi


def _mk(y: int, mo: int, d: int, hm: Optional[tuple[int, int]], tz: tzinfo) -> Optional[datetime]:
    try:
        return datetime(y, mo, d, hm[0] if hm else 0, hm[1] if hm else 0, tzinfo=tz)
    except ValueError:
        return None


def parse_human_datetime(text: str, *, now: datetime, tz: tzinfo) -> tuple[Optional[datetime], bool]:
    if not text:
        return None, False
    t = " ".join(str(text).replace("\u202f", " ").replace("\xa0", " ").split())

    m = _RE_ISO.search(t)
    if m:
        y, mo, d, hh, mm, off = m.groups()
        try:
            if off:
                iso = f"{y}-{mo}-{d}T{hh}:{mm}:00{'+00:00' if off == 'Z' else off}"
                return datetime.fromisoformat(iso).astimezone(tz), False
            return datetime(int(y), int(mo), int(d), int(hh), int(mm), tzinfo=tz), False
        except ValueError:
            return None, False

    m = _RE_REL.search(t)
    if m:
        hm = _h(m.group(2), m.group(3), m.group(4))
        base = now.astimezone(tz) + (timedelta(days=1) if m.group(1).lower() == "tomorrow" else timedelta())
        return (_mk(base.year, base.month, base.day, hm, tz) if hm else None), False
    m = _RE_REL24.search(t)
    if m:
        hm = _h(m.group(2), m.group(3), None)
        base = now.astimezone(tz) + (timedelta(days=1) if m.group(1).lower() == "tomorrow" else timedelta())
        return (_mk(base.year, base.month, base.day, hm, tz) if hm else None), False

    for rx, order in ((_RE_MON_D_Y_T, "mdy"), (_RE_D_MON_Y_T, "dmy")):
        m = rx.search(t)
        if m:
            g = m.groups()
            if order == "mdy":
                mon, day, year, hh, mi, ap = g
            else:
                day, mon, year, hh, mi, ap = g
            mo = MONTHS.get(mon[:3].lower())
            hm = _h(hh, mi, ap) if hh else None
            if mo is None or (hh and hm is None):
                return None, False
            if hm is None:
                hm = _trailing_24h(t[m.end():])
                if hm == "bad":
                    return None, False
            return _mk(int(year), mo, int(day), hm, tz), hm is None

    m = _RE_NUM.search(t)
    if m:
        a, b, y, hh, mi, ap = m.groups()
        # US order (M/D/Y) only when unambiguous or D>12; otherwise unknown.
        a_i, b_i = int(a), int(b)
        if a_i > 12 and b_i <= 12:
            mo, d = b_i, a_i
        elif b_i > 12 and a_i <= 12:
            mo, d = a_i, b_i
        elif a_i == b_i:
            mo, d = a_i, b_i
        else:
            mo, d = a_i, b_i  # US UI locale (en-US) is what chrome_session sets
        year = int(y) + (2000 if len(y) == 2 else 0)
        hm = _h(hh, mi, ap) if hh else None
        if hh and hm is None:
            return None, False
        return _mk(year, mo, d, hm, tz), hm is None

    m = _RE_MON_D_T.search(t)
    if m:
        mon, day, hh, mi, ap = m.groups()
        mo = MONTHS.get(mon[:3].lower())
        hm = _h(hh, mi, ap) if hh else None
        if mo is None or (hh and hm is None):
            return None, False
        if hm is None:
            hm = _trailing_24h(t[m.end():])
            if hm == "bad":
                return None, False
        local_now = now.astimezone(tz)
        cand = _mk(local_now.year, mo, int(day), hm, tz)
        if cand is None:
            return None, False
        # Scheduled lists show future items; a date >30 days in the past
        # means next year.
        if cand < local_now - timedelta(days=30):
            cand = _mk(local_now.year + 1, mo, int(day), hm, tz)
        return cand, hm is None

    return None, False
