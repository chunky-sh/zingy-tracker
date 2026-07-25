"""Bombay Natural History Society nature trails -- bnhs.org/nature-trails.

BNHS runs the birding and butterfly walks at Asola Bhatti through its Delhi
Conservation Education Centre, which is why it is on the list. Two things to
know before reading this adapter:

* **The listing is national.** BNHS is Mumbai-based and most cards are Mumbai,
  Pune or Bengaluru. Everything is filtered against a Delhi/NCR place list, so
  an empty result is normal, not a broken parser -- hence ``allow_empty`` in
  config/sources.yaml.
* **Delhi CEC's own walks are largely not listed here.** They are booked by
  phone and email (cecbnhsdelhi@bnhs.org, 011-26042010). The centre's old site
  cecdelhi.org no longer resolves. This adapter catches Delhi trails when BNHS
  does publish them nationally; it is not a complete feed of CEC activity.

Dates come in three shapes on the same page -- "08th February 2026",
"06 June 2026" and "17th May, Sunday" with no year at all -- plus ranges like
"15nd - 18th January 2026" (the typo is theirs).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from ..fetch import Fetcher
from ..models import IST, Event, Format, Topic, clean_text
from ..taxonomy import classify
from .base import BaseSource

log = logging.getLogger(__name__)

LISTING_URL = "https://www.bnhs.org/nature-trails"

# Anything in Delhi and its immediate birding hinterland. Matched against the
# trail title, which is where BNHS always names the place.
NCR_PLACES = (
    "delhi", "asola", "bhatti", "aravalli", "yamuna", "okhla", "sultanpur",
    "najafgarh", "basai", "dhanauri", "mangar", "surajkund", "damdama",
    "gurgaon", "gurugram", "noida", "faridabad", "ghaziabad", "ncr",
    "sanjay van", "jahanpanah", "sunder nursery", "lodhi garden",
    "central ridge", "southern ridge", "kamla nehru ridge",
)
NCR_RE = re.compile("|".join(re.escape(p) for p in NCR_PLACES), re.I)

MONTHS = {m.lower(): i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}

ORDINAL = r"(?:st|nd|rd|th)?"

# "08th February 2026" / "06 June 2026" / "17th May, Sunday".
# The ordinal suffix is optional and sometimes wrong ("15nd"), so accept any.
DATE_RE = re.compile(rf"(\d{{1,2}})\s*{ORDINAL}\s+([A-Za-z]+)\.?(?:\s*,?\s*(\d{{4}}))?", re.I)

# Multi-day camps read "15nd - 18th January 2026" -- the month belongs to the
# second number, so the plain pattern above would silently return the 18th as
# the start. Matched first, and the dash may be hyphen, en- or em-dash.
RANGE_RE = re.compile(
    rf"(\d{{1,2}})\s*{ORDINAL}\s*[-–—]\s*(\d{{1,2}})\s*{ORDINAL}\s+([A-Za-z]+)\.?"
    rf"(?:\s*,?\s*(\d{{4}}))?",
    re.I,
)


def _month_number(word: str) -> int | None:
    word = word.lower()
    if word in MONTHS:
        return MONTHS[word]
    for name, number in MONTHS.items():
        if name.startswith(word[:3]):
            return number
    return None


def parse_range(text: str, today: datetime) -> tuple[datetime | None, datetime | None]:
    """Return (start, end). ``end`` is set only for an explicit day range."""
    match = RANGE_RE.search(text)
    if match:
        first, last, month_word, year = match.groups()
        month = _month_number(month_word)
        if month is not None:
            start = _build(int(first), month, year, today)
            end = _build(int(last), month, year, today)
            if start is not None:
                # Keep both days in the same year even when the year was inferred.
                if end is not None and end < start:
                    end = end.replace(year=start.year)
                elif end is not None:
                    end = end.replace(year=start.year)
                return start, end
    return _parse_date(text, today), None


def _build(day: int, month: int, year: str | None, today: datetime) -> datetime | None:
    try:
        if year:
            return datetime(int(year), month, day)
        candidate = datetime(today.year, month, day)
    except ValueError:
        return None
    # No year published: pick the next occurrence, allowing a short grace period
    # so a trail listed yesterday is not flung twelve months ahead.
    if (today - candidate).days > 14:
        try:
            candidate = candidate.replace(year=candidate.year + 1)
        except ValueError:
            return None
    return candidate


def _parse_date(text: str, today: datetime) -> datetime | None:
    """First date in the string. Infers a missing year forward, never back --
    a trail listing advertises what is coming, not what has gone."""
    match = DATE_RE.search(text)
    if not match:
        return None

    day, month_word, year = match.group(1), match.group(2).lower(), match.group(3)
    month = _month_number(month_word)
    if month is None:
        return None
    return _build(int(day), month, year, today)


class Source(BaseSource):
    def fetch(self, fetcher: Fetcher) -> list[Event]:
        soup = BeautifulSoup(fetcher.get(LISTING_URL), "lxml")
        cards = soup.select("div.nature-trail-card")
        if not cards:
            raise RuntimeError("bnhs: no trail cards found; the listing markup changed")

        today = datetime.now(IST).replace(tzinfo=None)
        events: list[Event] = []
        skipped = 0

        for card in cards:
            heading = card.select_one("h4.nature-trail-heading")
            if heading is None:
                continue
            # BNHS titles are peppered with non-breaking spaces
            # ("SGNP\xa0Bird\xa0Monitoring"). Without normalising, a two-word
            # place like "sanjay van" would never match the filter below.
            title = clean_text(heading.get_text(" ", strip=True))

            if not NCR_RE.search(title):
                skipped += 1
                continue

            event = self._parse_card(card, title, today)
            if event is not None:
                events.append(event)

        log.info("bnhs: %d cards, %d outside Delhi/NCR, %d kept",
                 len(cards), skipped, len(events))
        return events

    def _parse_card(self, card: Tag, title: str, today: datetime) -> Event | None:
        date_el = card.select_one("p.nature-trail-date")
        if date_el is None:
            return None
        date_text = date_el.get_text(" ", strip=True)

        start, end = parse_range(date_text, today)
        if start is None:
            log.warning("bnhs: unparseable date %r for %r", date_text, title)
            return None

        link = card.select_one("h4.nature-trail-heading a[href]")
        url = str(link["href"]) if link else LISTING_URL

        booking = card.select_one("a.donate-btn[href]")
        booking_url = str(booking["href"]) if booking else ""

        image = card.select_one("div.thumb img[src]")
        image_url = str(image["src"]) if image else ""

        cost_el = card.select_one("p.book-mw-cost")
        price = ""
        if cost_el:
            cost = cost_el.get_text(" ", strip=True)
            # The template renders a bare "₹ -" when no fee is published.
            price = "" if cost.replace("₹", "").strip() in {"", "-"} else cost

        fmt, topics, _ = classify(title, date_text, "Walk")
        # BNHS trails are outdoor natural-history walks by definition; the
        # title alone ("Monsoon Walk at BNHS Reserve") often says nothing.
        if Topic.NATURE not in topics:
            topics = [Topic.NATURE, *topics][:4]

        return Event(
            **self.base_fields(),
            source_url=url,
            title=title,
            description=f"BNHS nature trail. Published date: {date_text}. "
                        "Booking is by enquiry — confirm with BNHS before travelling.",
            start=start,
            end=end,
            all_day=True,  # BNHS publishes a date, never a start time
            format=fmt if fmt is not Format.OTHER else Format.WALK,
            topics=topics,
            image_url=image_url,
            booking_url=booking_url,
            price=price,
        )
