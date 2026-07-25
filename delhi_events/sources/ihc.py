"""India Habitat Centre -- indiahabitat.org.

Two listings, two shapes:

* ``/Events`` is a month-by-month calendar grid. Day cells carry the time,
  native category and sub-venue; the linked detail page carries the blurb and
  the booking link. IHC publishes roughly two months ahead.
* ``/Exhibitions_Details`` is a masonry list of gallery shows with a day-month
  range but no year and no detail page, so the year has to be inferred.

The site returns a 289-byte stub to an unrecognised client. ``Fetcher.warm``
picks up the session cookie that unlocks the real page.
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

BASE = "https://indiahabitat.org"
HOME_URL = f"{BASE}/"
EVENTS_URL = f"{BASE}/Events"
EXHIBITIONS_URL = f"{BASE}/Exhibitions_Details"

MONTH_YEAR_RE = re.compile(r"([A-Z][a-z]+)\s+(\d{4})")
TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([AP]M)", re.I)
# "1 st Jul - 5 th Jul" once <sup> ordinals are flattened into the text.
DAY_MONTH_RE = re.compile(r"(\d{1,2})\s*(?:st|nd|rd|th)?\s*([A-Z][a-z]{2})[a-z]*", re.I)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_time(text: str) -> tuple[int, int]:
    match = TIME_RE.search(text)
    if not match:
        return 0, 0
    hour, minute, meridiem = int(match.group(1)), int(match.group(2)), match.group(3).upper()
    if meridiem == "PM" and hour != 12:
        hour += 12
    elif meridiem == "AM" and hour == 12:
        hour = 0
    return hour, minute


class Source(BaseSource):
    def fetch(self, fetcher: Fetcher) -> list[Event]:
        fetcher.warm(HOME_URL)
        events = self._fetch_calendar(fetcher) + self._fetch_exhibitions(fetcher)
        log.info("ihc: %d events total", len(events))
        return events

    # -- /Events ----------------------------------------------------------

    def _fetch_calendar(self, fetcher: Fetcher) -> list[Event]:
        soup = BeautifulSoup(fetcher.get(EVENTS_URL, referer=HOME_URL), "lxml")

        events: list[Event] = []
        month = year = None

        # Month headings and day grids are siblings at inconsistent depths, so
        # walk them in document order and carry the heading forward.
        for node in soup.select("div.month-day, div.calendar-container"):
            classes = node.get("class") or []
            if "month-day" in classes:
                if match := MONTH_YEAR_RE.search(node.get_text(" ", strip=True)):
                    month = MONTHS.get(match.group(1)[:3].lower())
                    year = int(match.group(2))
                continue

            if month is None or year is None:
                log.warning("ihc: calendar grid before any month heading; skipping")
                continue

            for cell in node.select("div.day-item"):
                day_el = cell.select_one("div.item-day span")
                if day_el is None or not day_el.get_text(strip=True).isdigit():
                    continue
                day = int(day_el.get_text(strip=True))
                for content in cell.select("div.day-content"):
                    event = self._parse_day_content(content, year, month, day, fetcher)
                    if event is not None:
                        events.append(event)

        log.info("ihc: %d calendar events", len(events))
        return events

    def _parse_day_content(self, content: Tag, year: int, month: int, day: int,
                           fetcher: Fetcher) -> Event | None:
        name_el = content.select_one("h3.event-name")
        if name_el is None:
            return None
        title = name_el.get_text(" ", strip=True)
        if not title:
            return None

        link = name_el.select_one("a[href]") or content.select_one("a.more-info[href]")
        url = str(link["href"]) if link else ""

        time_el = content.select_one("h4.event-time")
        hour, minute = _parse_time(time_el.get_text(" ", strip=True) if time_el else "")

        # "Film & Theatre | The Stein Auditorium"
        category = sub_venue = ""
        if meta_el := content.select_one("p"):
            parts = [p.strip() for p in meta_el.get_text(" ", strip=True).split("|")]
            category = parts[0] if parts else ""
            sub_venue = parts[1] if len(parts) > 1 else ""

        image_el = content.select_one("div.event-img img")
        image_url = str(image_el["src"]) if image_el and image_el.has_attr("src") else ""

        description, booking_url = self._parse_detail(url, fetcher) if url else ("", "")

        try:
            start = datetime(year, month, day, hour, minute)
        except ValueError:
            log.warning("ihc: impossible date %s-%s-%s for %r", year, month, day, title)
            return None

        fmt, topics, _ = classify(title, description, category)

        return Event(
            **self.base_fields(),
            source_url=url,
            source_event_id=url.rsplit("/", 1)[-1] if url else "",
            title=title,
            description=description,
            start=start,
            sub_venue=sub_venue,
            format=fmt,
            topics=topics,
            image_url=image_url,
            booking_url=booking_url,
        )

    def _parse_detail(self, url: str, fetcher: Fetcher) -> tuple[str, str]:
        try:
            soup = BeautifulSoup(fetcher.get(url, referer=EVENTS_URL), "lxml")
        except RuntimeError as exc:
            log.warning("ihc: detail fetch failed %s: %s", url, exc)
            return "", ""

        body = soup.select_one("section.ev-content")
        description = body.get_text(" ", strip=True) if body else ""

        booking = soup.select_one("div.reg-now a.reg-btn[href]")
        booking_url = str(booking["href"]) if booking else ""

        return description, booking_url

    # -- /Exhibitions_Details ---------------------------------------------

    def _fetch_exhibitions(self, fetcher: Fetcher) -> list[Event]:
        soup = BeautifulSoup(fetcher.get(EXHIBITIONS_URL, referer=HOME_URL), "lxml")

        # Later tab-panes repeat the same shows filtered by gallery; only the
        # first pane is the complete, non-duplicated set.
        pane = soup.select_one("div.tab-pane#all-events") or soup
        panels = pane.select(".white-panel")

        events: list[Event] = []
        for panel in panels:
            event = self._parse_exhibition(panel)
            if event is not None:
                events.append(event)

        log.info("ihc: %d exhibitions", len(events))
        return events

    def _parse_exhibition(self, panel: Tag) -> Event | None:
        heading_el = panel.select_one("h4")
        content_el = panel.select_one("div.ex-cont")
        if heading_el is None or content_el is None:
            return None

        heading = heading_el.get_text(" ", strip=True)
        sub_venue = heading.split("|")[-1].strip() if "|" in heading else ""

        start, end = self._parse_range(heading.split("|")[0])
        if start is None:
            log.warning("ihc: unparseable exhibition dates %r", heading)
            return None

        # The show's name is italicised; the rest of the paragraph is the blurb.
        title_el = content_el.select_one("em")
        description = content_el.get_text(" ", strip=True)
        if title_el is not None and title_el.get_text(strip=True):
            title = title_el.get_text(" ", strip=True)
        else:
            title = re.split(r"(?<=[.!?])\s", description)[0][:120] if description else ""
        if not title:
            return None

        image_el = panel.select_one("img")
        image_url = str(image_el["src"]) if image_el and image_el.has_attr("src") else ""

        _, topics, _ = classify(title, description, "Exhibition")

        return Event(
            **self.base_fields(),
            source_url=EXHIBITIONS_URL,
            title=title,
            description=description,
            start=start,
            end=end,
            all_day=True,
            sub_venue=sub_venue,
            format=Format.EXHIBITION,
            topics=topics,
            image_url=image_url,
        )

    @staticmethod
    def _parse_range(text: str) -> tuple[datetime | None, datetime | None]:
        """Parse "1 st Jul - 5 th Jul". No year is published, so infer it."""
        pairs = DAY_MONTH_RE.findall(text)
        if not pairs:
            return None, None

        today = datetime.now(IST)

        def build(day_str: str, month_str: str, year: int) -> datetime | None:
            month = MONTHS.get(month_str[:3].lower())
            if month is None:
                return None
            try:
                return datetime(year, month, int(day_str))
            except ValueError:
                return None

        start = build(pairs[0][0], pairs[0][1], today.year)
        if start is None:
            return None, None
        # A listing of upcoming shows will not be six months stale; a date that
        # far back means the calendar has rolled into the next year.
        if (today.replace(tzinfo=None) - start).days > 180:
            start = start.replace(year=start.year + 1)

        end = None
        if len(pairs) > 1:
            end = build(pairs[1][0], pairs[1][1], start.year)
            if end is not None and end < start:  # Dec -> Jan
                end = end.replace(year=end.year + 1)

        return start, end
