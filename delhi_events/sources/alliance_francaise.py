"""Alliance Française de Delhi -- afdelhi.org (WordPress + Events Manager 7.4).

Note on feeds: the site-wide export at ``/events/?ical=1`` looks inviting but is
stale -- it returns the fifty *oldest* events (2013-2014) and silently ignores
``scope`` and ``limit``. The per-event export at ``/events/<slug>/ical/`` is
correct and current, so we discover permalinks from the listing page and the RSS
feed, then pull one small ICS per event.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup
from icalendar import Calendar

from ..fetch import Fetcher
from ..models import Event
from ..taxonomy import classify, split_title_prefix
from .base import BaseSource

log = logging.getLogger(__name__)

LISTING_URL = "https://afdelhi.org/events/"
RSS_URL = "https://afdelhi.org/events/feed/"

PERMALINK_RE = re.compile(r"https://afdelhi\.org/events/([a-z0-9][a-z0-9-]*)/")
NON_EVENT_SLUGS = {"feed", "page", "category", "tag", "ical"}

VENUE_LINE_RE = re.compile(r"^\s*venue\s*[:\-]\s*(.+)$", re.I | re.M)


def _discover_permalinks(fetcher: Fetcher) -> list[str]:
    """Union of the listing page and the RSS feed.

    Two sources because each truncates differently: WordPress caps RSS at ten
    items by default, and the listing paginates. Neither alone is trustworthy.
    """
    slugs: dict[str, None] = {}

    for url in (LISTING_URL, RSS_URL):
        try:
            body = fetcher.get(url)
        except RuntimeError as exc:
            log.warning("alliance_francaise: could not fetch %s: %s", url, exc)
            continue
        for slug in PERMALINK_RE.findall(body):
            if slug not in NON_EVENT_SLUGS:
                slugs.setdefault(slug, None)

    return [f"https://afdelhi.org/events/{slug}/" for slug in slugs]


def _ical_text(component, key: str) -> str:
    value = component.get(key)
    if value is None:
        return ""
    # icalendar unescapes \n and \, for us; strip any HTML the editor left in.
    text = str(value)
    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "lxml").get_text("\n")
    return text


def _as_datetime(value) -> datetime | None:
    if value is None:
        return None
    dt = getattr(value, "dt", value)
    if isinstance(dt, datetime):
        return dt
    if hasattr(dt, "year"):  # a plain date -> all-day
        return datetime(dt.year, dt.month, dt.day)
    return None


class Source(BaseSource):
    def fetch(self, fetcher: Fetcher) -> list[Event]:
        permalinks = _discover_permalinks(fetcher)
        log.info("alliance_francaise: %d permalinks", len(permalinks))

        events: list[Event] = []
        for url in permalinks:
            try:
                raw = fetcher.get_bytes(f"{url}ical/", referer=LISTING_URL)
                calendar = Calendar.from_ical(raw)
            except Exception as exc:  # noqa: BLE001 - one bad event must not sink the run
                log.warning("alliance_francaise: bad ical for %s: %s", url, exc)
                continue

            for component in calendar.walk("VEVENT"):
                event = self._to_event(component, url)
                if event is not None:
                    events.append(event)

        return events

    def _to_event(self, component, permalink: str) -> Event | None:
        start = _as_datetime(component.get("DTSTART"))
        if start is None:
            return None
        end = _as_datetime(component.get("DTEND"))

        raw_title = _ical_text(component, "SUMMARY")
        if not raw_title:
            return None
        description = _ical_text(component, "DESCRIPTION")

        # Events Manager repeats the title as the first line of the description.
        if description.startswith(raw_title):
            description = description[len(raw_title):].lstrip("\n ")

        title, prefix_hint = split_title_prefix(raw_title)

        categories = component.get("CATEGORIES")
        category_text = ""
        if categories is not None:
            cats = getattr(categories, "cats", None) or []
            # "Events" is the plugin's own umbrella term, not a real category.
            useful = [str(c) for c in cats if str(c).lower() != "events"]
            category_text = useful[0] if useful else ""

        sub_venue = ""
        venue_match = VENUE_LINE_RE.search(description)
        if venue_match:
            sub_venue = venue_match.group(1).split(",")[0].strip()
        elif (location := _ical_text(component, "LOCATION")).strip(", "):
            sub_venue = location.split(",")[0].strip()

        fmt, topics, _ = classify(title, description, category_text or prefix_hint)

        return Event(
            **self.base_fields(),
            source_url=permalink,
            source_event_id=str(component.get("UID", "")),
            title=title,
            description=description,
            start=start,
            end=end,
            sub_venue=sub_venue,
            format=fmt,
            topics=topics,
        )
