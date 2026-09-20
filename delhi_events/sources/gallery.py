"""A selector-driven adapter for commercial gallery exhibition listings.

Delhi's gallery circuit is two dozen small spaces, each with a listing page of
the same shape -- a repeating card carrying a show's title, its date range and
a blurb -- and each with entirely different class names. Written as one module
per gallery that would be two dozen near-identical scrapers to keep alive, so
instead the selectors live in ``config/sources.yaml`` and adding a gallery is a
config entry rather than a module.

An entry looks like::

    - id: nature_morte
      adapter: gallery
      name: Nature Morte
      url: https://naturemorte.com/exhibitions/
      options:
        card: div.card-exhibition     # the repeating container (required)
        title: h2                     # (required)
        dates: .date                  # falls back to scanning the card's text
        description: .card-text
        link: a[href]
        image: img[src]

Two things this adapter does that a per-venue scraper would not have to:

* **It filters to what is still on.** Gallery listings carry their archive on
  the same page -- Shrine Empire's runs to a hundred-odd past shows -- and the
  request was for what you can still go and see.
* **It tolerates a card it cannot date** -- a listing that mixes "5 - 30
  October 2026" with "Coming soon" is normal -- while still raising when the
  card selector matches nothing at all, since that means the page changed
  shape rather than emptied. A gallery genuinely between shows returns [],
  which is why the gallery entries carry ``allow_empty``.
"""

from __future__ import annotations

import copy
import logging
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..daterange import parse_range
from ..fetch import Fetcher
from ..models import IST, Event, Format
from ..taxonomy import classify
from .base import BaseSource

log = logging.getLogger(__name__)

# A show dated further out than this is a misparse, not a booking.
MAX_LEAD = timedelta(days=550)


def _today() -> date:
    """Indirection so "what is still on" can be pinned in tests against a
    fixture whose shows would otherwise expire and quietly stop being covered."""
    return datetime.now(IST).date()


class Source(BaseSource):
    def fetch(self, fetcher: Fetcher) -> list[Event]:
        options = self.config.options
        card_selector = options.get("card")
        title_selector = options.get("title")
        if not card_selector or not title_selector:
            raise RuntimeError(
                f"{self.id}: gallery adapter needs 'card' and 'title' selectors"
            )

        # Let a fetch failure propagate: an unreachable venue must not look
        # like a venue with nothing on.
        soup = BeautifulSoup(fetcher.get(self.config.url), "lxml")
        cards = soup.select(card_selector)
        if not cards:
            # The two empty outcomes mean opposite things and must not look
            # alike. No card matching the selector at all means the page
            # changed shape under us, so raise; cards that match but have all
            # finished is just a gallery between shows, and returns [] below.
            raise RuntimeError(
                f"{self.id}: no cards matched {card_selector!r} on {self.config.url}"
            )

        today = _today()
        events: list[Event] = []
        seen: set[str] = set()
        undated = 0

        for card in cards:
            event = self._parse_card(card, options, title_selector)
            if event is None:
                undated += 1
                continue
            last_day = (event.end or event.start).date()
            if last_day < today or event.start.date() - today > MAX_LEAD:
                continue
            if event.id in seen:
                continue
            seen.add(event.id)
            events.append(event)

        log.info("%s: %d cards, %d undated, %d current", self.id, len(cards), undated,
                 len(events))
        return events

    @staticmethod
    def _parse_dates(card: Tag, options: dict) -> tuple[datetime | None, datetime | None]:
        """Read a card's dates, however the venue chose to split them up.

        Three shapes in the wild: one element holding the whole range, two
        elements holding the ends separately (Latitude 28's .date-start and
        .date-end), and no element at all, where the range is a bare line in
        the card's text.
        """
        start_selector, end_selector = options.get("date_start"), options.get("date_end")
        if start_selector and end_selector:
            start_el = card.select_one(start_selector)
            end_el = card.select_one(end_selector)
            if start_el is not None:
                start, _ = parse_range(start_el.get_text(" ", strip=True))
                end = None
                if end_el is not None:
                    end, _ = parse_range(end_el.get_text(" ", strip=True))
                if start is not None:
                    return start, (end if end and end >= start else None)

        date_text = ""
        if date_selector := options.get("dates"):
            if date_el := card.select_one(date_selector):
                date_text = date_el.get_text(" ", strip=True)
        if not date_text:
            date_text = card.get_text(" ", strip=True)
        return parse_range(date_text)

    def _parse_card(self, card: Tag, options: dict, title_selector: str) -> Event | None:
        title_el = card.select_one(title_selector)
        if title_el is None:
            return None
        # Some headings carry a tag inside them -- Shrine Empire's h5 ends
        # "<span>| Group Show</span>" -- which belongs to neither the title nor
        # the blurb. Drop it from a copy so the card itself stays intact.
        if exclude_selector := options.get("title_exclude"):
            title_el = copy.copy(title_el)
            for junk in title_el.select(exclude_selector):
                junk.decompose()
        title = title_el.get_text(" ", strip=True).strip(" |-–—")
        if not title:
            return None

        start, end = self._parse_dates(card, options)
        if start is None:
            return None

        description = ""
        if description_selector := options.get("description"):
            parts = [
                el.get_text(" ", strip=True)
                for el in card.select(description_selector)
                if el is not title_el
            ]
            description = "\n".join(p for p in parts if p and p != title)

        # The card is often itself the anchor, so check it before looking inside.
        source_url = self.config.url
        link_selector = options.get("link", "a[href]")
        link = card if card.name == "a" and card.get("href") else card.select_one(link_selector)
        if link is not None and link.get("href"):
            source_url = urljoin(self.config.url, str(link["href"]))

        image_url = ""
        if image := card.select_one(options.get("image", "img")):
            # Lazy-loaded galleries put a blank SVG placeholder in src and the
            # real file in data-src, so a plain src read gets a data: URI.
            raw = str(image.get("src") or "")
            if not raw or raw.startswith("data:"):
                raw = str(image.get("data-src") or image.get("data-lazy-src") or "")
            if raw and not raw.startswith("data:"):
                image_url = urljoin(self.config.url, raw)

        sub_venue = options.get("sub_venue_label", "")
        if sub_venue_selector := options.get("sub_venue"):
            if el := card.select_one(sub_venue_selector):
                sub_venue = el.get_text(" ", strip=True)

        # KNMA and Nature Morte both list shows their work travels to -- the
        # V&A, MoMA -- on the same page as their Delhi programme. An allowlist
        # of their own spaces keeps a London show off a Delhi tracker.
        if allowed := options.get("sub_venue_allow"):
            if not any(a.lower() in sub_venue.lower() for a in allowed):
                return None

        # The page is the gallery's exhibitions listing, so the format is known
        # from context rather than guessed at; only the topics need reading.
        fmt = Format(options.get("format", Format.EXHIBITION.value))
        _, topics, _ = classify(title, description)

        return Event(
            **self.base_fields(),
            source_url=source_url,
            title=title,
            description=description,
            start=start,
            end=end,
            all_day=True,
            sub_venue=sub_venue,
            format=fmt,
            topics=topics,
            image_url=image_url,
        )
