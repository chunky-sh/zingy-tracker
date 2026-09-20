"""Parsing the date ranges galleries print above an exhibition.

Every gallery writes the same fact a different way, and most of them leave
parts out because a human reader can fill them in:

    27th September 2026 - 06th October 2026     both sides complete
    10 Oct - 12 Dec 2026                        year only at the end
    10 - 25 October 2026                        month and year only at the end
    September 5 - October 10, 2026              month first, year at the end
    5 April 2026                                a one-day event

So parse each side independently into whatever parts it carries, then backfill
the left side from the right -- never the other way round, since the right side
is the one galleries write in full.
"""

from __future__ import annotations

import re
from datetime import datetime

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# En dash, em dash, hyphen, and the words galleries use instead.
SEPARATOR_RE = re.compile(r"\s*(?:[-–—]+|\bto\b|\buntil\b|\btill\b|\bthrough\b)\s*", re.I)

_DAY_FIRST_RE = re.compile(r"\b(\d{1,2})\s*(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\b", re.I)
_MONTH_FIRST_RE = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})\b(?!\s*:)", re.I)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_BARE_DAY_RE = re.compile(r"^\s*(\d{1,2})\s*(?:st|nd|rd|th)?\s*$", re.I)


def _month(name: str) -> int | None:
    return MONTHS.get(name[:3].lower())


def _parts(half: str) -> tuple[int | None, int | None, int | None]:
    """Pull (day, month, year) out of one side of a range; None for absent."""
    day = month = year = None

    if match := _YEAR_RE.search(half):
        year = int(match.group(1))

    if match := _DAY_FIRST_RE.search(half):
        if (m := _month(match.group(2))) is not None:
            day, month = int(match.group(1)), m
    if month is None and (match := _MONTH_FIRST_RE.search(half)):
        if (m := _month(match.group(1))) is not None:
            month, day = m, int(match.group(2))
    if day is None and (match := _BARE_DAY_RE.match(_YEAR_RE.sub("", half).strip())):
        day = int(match.group(1))

    return day, month, year


def _build(day: int | None, month: int | None, year: int | None) -> datetime | None:
    if day is None or month is None or year is None:
        return None
    try:
        return datetime(year, month, day)
    except ValueError:  # 31st September and friends
        return None


def parse_range(text: str) -> tuple[datetime | None, datetime | None]:
    """Parse an exhibition's date line into (start, end).

    ``end`` is None for a single date, and also when the two dates come out
    backwards -- a range that runs backwards means we misread it, and a wrong
    end date silently drops a show off the site early.
    """
    if not text:
        return None, None

    halves = [h for h in SEPARATOR_RE.split(text.strip()) if h.strip()]
    if not halves:
        return None, None

    # Keep only the pieces that actually carry a day, then take the first two.
    # Venues tack the opening hours onto the same line -- KNMA prints
    # "27 Sep 2026 — 13 Jun 2027 10:30 am - 5:30 pm" -- so reading the *last*
    # piece as the end date would drop the closing date for a trailing "5:30 pm".
    dated = [p for h in halves if (p := _parts(h))[0] is not None]
    if not dated:
        return None, None

    if len(dated) == 1:
        day, month, year = dated[0]
        return _build(day, month, year), None

    (left_day, left_month, left_year), (right_day, right_month, right_year) = dated[:2]

    # Galleries abbreviate the *first* date, never the last.
    start = _build(left_day, left_month or right_month, left_year or right_year)
    end = _build(right_day, right_month, right_year)

    if start is None:
        return end, None
    if end is not None and end < start:
        return start, None
    return start, end
