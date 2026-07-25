"""India International Centre -- iicdelhi.in (Drupal).

The listing at /programmes/current is server-rendered and carries title, native
category, venue, image and a start time per card. It does not carry an end time,
which matters for the multi-week exhibitions IIC runs, so each card is followed
to its detail page for the exact range and the full description.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from ..fetch import Fetcher
from ..models import Event
from ..taxonomy import classify, split_title_prefix
from .base import BaseSource

log = logging.getLogger(__name__)

BASE = "https://iicdelhi.in"
LISTING_URL = f"{BASE}/programmes/current"

# "22 Jul 2026, 11:00 AM" on the card.
CARD_DATE_RE = re.compile(r"(\d{1,2}\s+\w{3,}\s+\d{4})(?:\s*,\s*(\d{1,2}:\d{2}\s*[AP]M))?", re.I)

# "Event starts on Wednesday, 22 July 2026 at 11:00 hrs" on the detail page.
DETAIL_DATE_RE = re.compile(
    r"Event\s+(starts|ends)\s+on\s+\w+,\s*(\d{1,2}\s+\w+\s+\d{4})\s+at\s+(\d{1,2}:\d{2})\s*hrs",
    re.I,
)


def _parse_card_datetime(text: str) -> datetime | None:
    match = CARD_DATE_RE.search(text)
    if not match:
        return None
    day, time_part = match.group(1), match.group(2)
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            base = datetime.strptime(day, fmt)
        except ValueError:
            continue
        if time_part:
            try:
                clock = datetime.strptime(time_part.replace(" ", "").upper(), "%I:%M%p")
                return base.replace(hour=clock.hour, minute=clock.minute)
            except ValueError:
                pass
        return base
    return None


def _parse_detail_dates(text: str) -> tuple[datetime | None, datetime | None]:
    start = end = None
    for kind, day, clock in DETAIL_DATE_RE.findall(text):
        for fmt in ("%d %B %Y", "%d %b %Y"):
            try:
                parsed = datetime.strptime(day, fmt)
            except ValueError:
                continue
            hour, minute = (int(p) for p in clock.split(":"))
            parsed = parsed.replace(hour=hour, minute=minute)
            if kind.lower() == "starts":
                start = parsed
            else:
                end = parsed
            break
    return start, end


class Source(BaseSource):
    def fetch(self, fetcher: Fetcher) -> list[Event]:
        soup = BeautifulSoup(fetcher.get(LISTING_URL), "lxml")
        cards = soup.select("div.card.programme-card")
        log.info("iic: %d cards", len(cards))

        events: list[Event] = []
        for card in cards:
            try:
                event = self._parse_card(card, fetcher)
            except Exception as exc:  # noqa: BLE001
                log.warning("iic: skipped a card: %s", exc)
                continue
            if event is not None:
                events.append(event)
        return events

    def _parse_card(self, card: Tag, fetcher: Fetcher) -> Event | None:
        link = card.select_one("a.text-decoration-none[href]")
        title_el = card.select_one("h6.card-title")
        if link is None or title_el is None:
            return None

        title, prefix_hint = split_title_prefix(title_el.get_text(" ", strip=True))
        href = str(link["href"])
        url = href if href.startswith("http") else f"{BASE}{href}"

        badge = card.select_one("span.badge")
        category = badge.get_text(" ", strip=True) if badge else ""

        date_el = card.select_one("p.card-text")
        start = _parse_card_datetime(date_el.get_text(" ", strip=True)) if date_el else None

        venue_el = card.select_one("p.card-text-small")
        sub_venue = venue_el.get_text(" ", strip=True) if venue_el else ""

        image = card.select_one("img.programme-card-img")
        image_url = str(image["src"]) if image and image.has_attr("src") else ""

        description, detail_start, detail_end = self._parse_detail(url, fetcher)

        # The detail page is authoritative: the card shows only a start.
        start = detail_start or start
        if start is None:
            log.warning("iic: no parseable date for %r", title)
            return None

        fmt, topics, _ = classify(title, description, category or prefix_hint)

        return Event(
            **self.base_fields(),
            source_url=url,
            source_event_id=href.rsplit("/", 1)[-1],
            title=title,
            description=description,
            start=start,
            end=detail_end,
            sub_venue=sub_venue,
            format=fmt,
            topics=topics,
            image_url=image_url,
        )

    def _parse_detail(self, url: str, fetcher: Fetcher) -> tuple[str, datetime | None, datetime | None]:
        try:
            soup = BeautifulSoup(fetcher.get(url, referer=LISTING_URL), "lxml")
        except RuntimeError as exc:
            log.warning("iic: detail fetch failed %s: %s", url, exc)
            return "", None, None

        info = soup.select_one("p.programme-details-info")
        start, end = _parse_detail_dates(info.get_text(" ", strip=True)) if info else (None, None)

        body = soup.select_one("div.programme-details-description")
        description = body.get_text("\n", strip=True) if body else ""

        return description, start, end
