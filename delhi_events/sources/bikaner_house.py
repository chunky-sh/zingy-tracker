"""Bikaner House -- bikanerhouse.rajasthan.gov.in.

A Rajasthan government venue on Pandara Road whose halls are hired week by
week, so its programme is mostly *other people's* shows: commercial galleries,
artist collectives and foundations that have no scrapeable calendar of their
own. One listing here stands in for a dozen small galleries, which makes it the
highest-yield art source in the city -- Delhi Contemporary Art Week, the annual
six-gallery fixture, runs here.

Listings live at ``/upcoming-events/<YYYY>/<M>``: a plain server-rendered page
per month, no pagination, month number *unpadded* (``/9``, not ``/09``).

Walk several months rather than one page. Each show is listed only under the
month it **opens** in, so a show opening on 1 October is invisible on the
September page no matter how close October is -- fetching just the current
month would give as little as a day's notice at month end.

``/gallery/<YYYY>/<M>`` is the same markup for months already past. We do not
read it: the store only wants events it can still put in front of someone.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from ..fetch import Fetcher
from ..models import IST, Event, Format
from ..taxonomy import classify
from .base import BaseSource

log = logging.getLogger(__name__)

BASE = "https://bikanerhouse.rajasthan.gov.in"
MONTH_URL = f"{BASE}/upcoming-events/{{year}}/{{month}}"

DEFAULT_MONTHS_AHEAD = 3

# "27th September 2026" -- ordinal optional, full month name, explicit year.
DATE_RE = re.compile(
    r"(\d{1,2})\s*(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})", re.I
)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _today() -> datetime:
    """Indirection so the month walk can be tested against fixed fixtures.

    The golden files are two specific months; without this the test would
    quietly stop covering the second one as soon as the real clock moved past
    them.
    """
    return datetime.now(IST)


def _parse_range(text: str) -> tuple[datetime | None, datetime | None]:
    """Parse "27th September 2026 - 06th October 2026".

    Both sides carry their own year, so unlike the IHC exhibitions listing
    there is nothing to infer. A single date means a one-day event.
    """
    parts = DATE_RE.findall(text)
    if not parts:
        return None, None

    dates = []
    for day, month_name, year in parts[:2]:
        month = MONTHS.get(month_name[:3].lower())
        if month is None:
            continue
        try:
            dates.append(datetime(int(year), month, int(day)))
        except ValueError:
            continue

    if not dates:
        return None, None
    start = dates[0]
    end = dates[1] if len(dates) > 1 else None
    if end is not None and end < start:
        return start, None
    return start, end


class Source(BaseSource):
    def fetch(self, fetcher: Fetcher) -> list[Event]:
        months_ahead = int(self.config.options.get("months_ahead", DEFAULT_MONTHS_AHEAD))
        today = _today()

        events: list[Event] = []
        seen: set[str] = set()

        for offset in range(months_ahead + 1):
            month_index = today.month - 1 + offset
            year, month = today.year + month_index // 12, month_index % 12 + 1
            url = MONTH_URL.format(year=year, month=month)

            try:
                body = fetcher.get(url)
            except RuntimeError as exc:
                # The first page is the one that must work: if the current
                # month cannot be read we have no listing at all and should
                # say so rather than return a short list that looks healthy.
                if offset == 0:
                    raise
                log.warning("bikaner_house: could not fetch %s: %s", url, exc)
                continue

            for card in BeautifulSoup(body, "lxml").select("div.eventimages"):
                event = self._parse_card(card, url)
                if event is None or event.id in seen:
                    continue
                seen.add(event.id)
                events.append(event)

        log.info("bikaner_house: %d events over %d months", len(events), months_ahead + 1)
        return events

    def _parse_card(self, card: Tag, url: str) -> Event | None:
        title_box = card.select_one("div.event-title-box")
        if title_box is None:
            return None

        heading = title_box.select_one("h2")
        title = heading.get_text(" ", strip=True) if heading else ""
        if not title:
            return None

        date_el = title_box.select_one("p")
        start, end = _parse_range(date_el.get_text(" ", strip=True) if date_el else "")
        if start is None:
            log.warning("bikaner_house: unparseable dates for %r", title)
            return None

        # The blurb is the paragraph in the sibling div -- the one inside
        # event-title-box is the date line we have already read.
        paragraphs = [
            p.get_text(" ", strip=True)
            for p in card.select("p")
            if p.find_parent("div", class_="event-title-box") is None
        ]
        description = "\n".join(p for p in paragraphs if p)

        image_el = card.select_one("div.images img[src]")
        image_url = str(image_el["src"]) if image_el else ""

        fmt, topics, _ = classify(title, description)
        # Bikaner House lets its halls, and what gets let is overwhelmingly a
        # gallery show. Only fall back to that when the text gave no signal at
        # all -- the venue does also host festivals and recitals.
        if fmt is Format.OTHER:
            fmt = Format.EXHIBITION

        return Event(
            **self.base_fields(),
            source_url=url,
            title=title,
            description=description,
            start=start,
            end=end,
            all_day=True,
            format=fmt,
            topics=topics,
            image_url=image_url,
        )
