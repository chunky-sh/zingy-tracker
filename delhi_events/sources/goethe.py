"""Goethe-Institut / Max Mueller Bhavan New Delhi -- goethe.de.

The visible calendar at /ins/in/en/sta/del/ver.cfm is a Vue app and renders
nothing useful to a plain fetch. It is backed by a public REST endpoint that
does respond to ordinary HTTP, so no headless browser is needed at runtime --
the browser was only used once, to discover the call.

The event detail pages add nothing the JSON does not already carry (they show
the same date, type and subheading), so this adapter makes no per-event request.

Quirks of the payload:
  * ``date_start_full`` is always T12:00:00 -- a placeholder, not the start time.
  * The real clock time hides in ``event_location_txt``: "7:00 PM IST | Film
    Screening". Events with no time there are genuinely all-day.
  * ``institute_ID`` 311 is New Delhi; the category_ID list is the full set the
    site's own calendar requests, i.e. "everything".
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from datetime import datetime

from ..fetch import Fetcher
from ..models import Event
from ..taxonomy import classify
from .base import BaseSource

log = logging.getLogger(__name__)

API_URL = "https://www.goethe.de/rest/objeventcalendarRedesign/events/fetchEvents"
DETAIL_URL = "https://www.goethe.de/ins/in/en/m/ver.cfm?event_id={id}&fuseaction=events.detail"

PAGE_SIZE = 20
MAX_PAGES = 25  # a hard stop; the calendar never runs to 500 events

CONFIG_DATA = {
    "category_ID": (
        "178926_178927_178937_178936_178935_178934_178933_178932_"
        "178931_178930_178929_178928_178938"
    ),
    "elementsperpage": PAGE_SIZE,
    "frontendfilter": "adress_IDtxt,category_IDtxt,date_range",
    "outputtype": "standardkalender",
    "institute_ID": 311,
    "week_day_start": 1,
    "timezone": 48,
}

TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*([AP]M)", re.I)


def _page_url(start: int) -> str:
    query = urllib.parse.urlencode({
        "configData": json.dumps(CONFIG_DATA, separators=(",", ":")),
        "langId": 1,
        "viewMode": -1,
        "filterData": json.dumps({"start": start}, separators=(",", ":")),
    })
    return f"{API_URL}?{query}"


def _parse_time(text: str) -> tuple[int, int] | None:
    match = TIME_RE.search(text or "")
    if not match:
        return None
    hour, minute, meridiem = int(match.group(1)), int(match.group(2)), match.group(3).upper()
    if meridiem == "PM" and hour != 12:
        hour += 12
    elif meridiem == "AM" and hour == 12:
        hour = 0
    return hour, minute


def _parse_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


class Source(BaseSource):
    def fetch(self, fetcher: Fetcher) -> list[Event]:
        items = self._fetch_all(fetcher)
        log.info("goethe: %d items from api", len(items))

        events: list[Event] = []
        for item in items:
            event = self._to_event(item)
            if event is not None:
                events.append(event)
        return events

    def _fetch_all(self, fetcher: Fetcher) -> list[dict]:
        items: list[dict] = []
        seen_ids: set[int] = set()

        for page in range(MAX_PAGES):
            raw = fetcher.get(_page_url(page * PAGE_SIZE), referer=self.config.url)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"goethe api returned non-JSON on page {page}: {exc}") from exc

            batch = payload.get("eventItems") or []
            fresh = [i for i in batch if i.get("object_id") not in seen_ids]
            seen_ids.update(i.get("object_id") for i in fresh)
            items.extend(fresh)

            # The endpoint clamps out-of-range offsets to the last page rather
            # than returning empty, so stop on "nothing new" as well as short pages.
            if len(batch) < PAGE_SIZE or not fresh:
                break

        return items

    def _to_event(self, item: dict) -> Event | None:
        title = (item.get("headline") or "").strip()
        start_date = _parse_date(item.get("date_start_ical", ""))
        if not title or start_date is None:
            return None

        location_text = item.get("event_location_txt") or ""
        clock = _parse_time(location_text)
        all_day = clock is None
        start = start_date.replace(hour=clock[0], minute=clock[1]) if clock else start_date

        end = _parse_date(item.get("date_end_ical", ""))
        if end is not None and end.date() <= start.date():
            end = None

        event_type = (item.get("event_type") or "").split("|")[0].strip()
        categories = [c.get("category_text", "") for c in item.get("secondary_categories") or []]

        subheadline = (item.get("subheadline") or "").strip()
        description = " | ".join(p for p in (subheadline, item.get("event_type") or "") if p)

        # Both the venue's own label and its category tags are worth a look:
        # event_type says "Film Screening", the tags say "Film".
        category_hint = event_type or (categories[0] if categories else "")
        fmt, topics, _ = classify(title, f"{description} {' '.join(categories)}", category_hint)

        object_id = item.get("object_id")

        return Event(
            **self.base_fields(),
            source_url=DETAIL_URL.format(id=object_id) if object_id else self.config.url,
            source_event_id=str(object_id or ""),
            title=title,
            description=description,
            start=start,
            end=end,
            all_day=all_day,
            sub_venue=(item.get("event_city") or "").strip() if item.get("is_online") else "",
            format=fmt,
            topics=topics,
            price=(item.get("price") or "").strip(),
            booking_url=(item.get("registration_link_url") or "").strip(),
        )
