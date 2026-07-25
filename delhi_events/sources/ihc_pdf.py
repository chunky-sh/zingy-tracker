"""India Habitat Centre monthly PDF calendar -- LLM-extracted backfill.

IHC's /Events page shows roughly two months. The monthly PDF at
``assets/user/calendar/monthly-calendar.pdf`` is published ahead of that and is
the only machine-readable source for the extra weeks. It has no table structure
worth parsing -- week headings, day boxes and free-text blurbs -- so this is the
one v1 source that goes through Claude.

Disabled by default in config/sources.yaml: it needs ANTHROPIC_API_KEY, and the
HTML adapter already covers the overlapping period. Enable it when you want the
extra lead time.

Events it produces are deduplicated against the HTML adapter's by
``db.find_duplicate`` -- same venue, same day, near-identical title.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime

from ..fetch import Fetcher
from ..models import IST, Event
from .base import BaseSource

log = logging.getLogger(__name__)

HOME_URL = "https://indiahabitat.org/"
PDF_URL = "https://indiahabitat.org/assets/user/calendar/monthly-calendar.pdf"

# The cover reads "July 26'Calendar" -- two-digit year, then an apostrophe that
# is a curly U+2018 in the real PDF. Match any single punctuation mark rather
# than guessing which quote character the designer used this month.
COVER_RE = re.compile(r"([A-Z][a-z]+)\s*(\d{2})\s*[^\w\s]?\s*Calendar", re.I)

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], start=1)}


def _pdf_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is optional
        raise RuntimeError("pypdf is not installed. Run: pip install pypdf") from exc

    reader = PdfReader(io.BytesIO(raw))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _cover_period(text: str) -> tuple[int, int] | None:
    """Read (year, month) off the cover so a bare '7:00pm|...' entry under a
    'WED1' heading can be dated. Without it every date is a guess."""
    match = COVER_RE.search(text[:1500])
    if not match:
        return None
    month = MONTHS.get(match.group(1).lower())
    if month is None:
        return None
    return 2000 + int(match.group(2)), month


class Source(BaseSource):
    def fetch(self, fetcher: Fetcher) -> list[Event]:
        from ..llm import extract_events_from_text  # optional dependency

        fetcher.warm(HOME_URL)
        text = _pdf_text(fetcher.get_bytes(PDF_URL, referer=HOME_URL))
        if not text.strip():
            raise RuntimeError("ihc_pdf: the calendar PDF produced no text")

        period = _cover_period(text)
        if period is None:
            year = datetime.now(IST).year
            log.warning("ihc_pdf: no cover month found; assuming year %d", year)
        else:
            year, month = period
            log.info("ihc_pdf: calendar covers %04d-%02d", year, month)
            # Give the extractor the month explicitly -- day boxes say "WED1",
            # never "1 July", so the month lives only on the cover.
            text = (
                f"This calendar covers {month:02d}/{year}. "
                f"Every event below falls in that month unless stated otherwise.\n\n{text}"
            )

        events = extract_events_from_text(
            text,
            source_id=self.config.id,
            venue=self.config.name,
            address=self.config.address,
            source_url=PDF_URL,
            default_year=year,
        )
        log.info("ihc_pdf: %d events extracted", len(events))
        return events
