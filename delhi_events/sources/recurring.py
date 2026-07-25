"""Standing weekly walks, expanded from an operator-maintained schedule.

Some venues run the same walk every weekend for years and never publish a dated
calendar. Sunder Nursery is the clearest case: its heritage walk page advertises
"Every Weekend ... 11 a.m. and 4 p.m.", its bird-walk page still links a booking
form dated 2023, and the footer's "Programmes & Events" link goes to Facebook.
There is nothing to scrape.

So these events are **declared, not discovered** -- read from
``config/recurring.yaml``. That is a meaningfully weaker claim than the scraped
sources make, and three things keep it honest:

1. **A liveness check.** Each run re-fetches the venue page and looks for a
   marker string. If the venue drops the walk from its site, generation stops.
2. **An expiry.** ``confirmed_until`` is the date a human last confirmed the
   walk actually runs. Past it, the schedule stops producing events rather than
   projecting forward forever. Re-confirm by phone, then push the date out.
3. **A caveat on every event**, carrying the venue's phone number, because a
   declared event is a weaker promise than a listed one.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from ..fetch import Fetcher
from ..models import IST, Event, Format, Topic
from .base import BaseSource

log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "recurring.yaml"

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

DEFAULT_HORIZON_DAYS = 60


def load_schedules(path: Path | str = CONFIG_PATH) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    return data.get("schedules") or []


def _as_date(value) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def occurrences(weekdays: list[int], start: date, horizon_days: int) -> list[date]:
    return [
        day for offset in range(horizon_days + 1)
        if (day := start + timedelta(days=offset)).weekday() in weekdays
    ]


class Source(BaseSource):
    def fetch(self, fetcher: Fetcher) -> list[Event]:
        schedules = [s for s in load_schedules() if s.get("source_id") == self.config.id]
        if not schedules:
            log.warning("recurring: no schedules configured for %s", self.config.id)
            return []

        today = datetime.now(IST).date()
        events: list[Event] = []

        for schedule in schedules:
            events.extend(self._expand(schedule, today, fetcher))
        return events

    def _expand(self, schedule: dict, today: date, fetcher: Fetcher) -> list[Event]:
        name = schedule.get("title", "<untitled>")

        confirmed_until = _as_date(schedule.get("confirmed_until"))
        if confirmed_until is None:
            log.warning("recurring: %r has no confirmed_until; skipping", name)
            return []
        if confirmed_until < today:
            log.warning(
                "recurring: %r was last confirmed for %s and has now lapsed. "
                "Call the venue, then update confirmed_until in config/recurring.yaml.",
                name, confirmed_until,
            )
            return []

        if not self._page_still_advertises(schedule, fetcher):
            return []

        weekdays = [WEEKDAYS[d.lower()] for d in schedule.get("weekdays", [])
                    if d.lower() in WEEKDAYS]
        if not weekdays:
            log.warning("recurring: %r lists no valid weekdays; skipping", name)
            return []

        horizon = int(schedule.get("horizon_days", DEFAULT_HORIZON_DAYS))
        # Never generate past the confirmation date.
        last_day = min(today + timedelta(days=horizon), confirmed_until)
        horizon = max((last_day - today).days, 0)

        times = schedule.get("times") or []
        duration = int(schedule.get("duration_minutes", 0))

        caveat = schedule.get("caveat", "").strip()
        description = schedule.get("description", "").strip()
        body = "\n\n".join(p for p in (description, caveat) if p)

        try:
            fmt = Format(schedule.get("format", "walk"))
        except ValueError:
            fmt = Format.WALK
        topics = []
        for name_ in schedule.get("topics", []):
            try:
                topics.append(Topic(name_))
            except ValueError:
                log.debug("recurring: unknown topic %r", name_)

        events: list[Event] = []
        for day in occurrences(weekdays, today, horizon):
            for clock in times or [None]:
                start = datetime(day.year, day.month, day.day)
                all_day = clock is None
                end = None
                if clock is not None:
                    hour, minute = (int(p) for p in str(clock).split(":"))
                    start = start.replace(hour=hour, minute=minute)
                    if duration:
                        end = start + timedelta(minutes=duration)

                events.append(Event(
                    source_id=self.config.id,
                    venue=schedule.get("venue", self.config.name),
                    address=schedule.get("address", self.config.address),
                    source_url=schedule.get("url", self.config.url),
                    title=schedule["title"],
                    description=body,
                    start=start,
                    end=end,
                    all_day=all_day,
                    sub_venue=schedule.get("sub_venue", ""),
                    format=fmt,
                    topics=topics,
                    price=schedule.get("price", ""),
                ))

        log.info("recurring: %r -> %d occurrences to %s", schedule["title"],
                 len(events), last_day)
        return events

    def _page_still_advertises(self, schedule: dict, fetcher: Fetcher) -> bool:
        """Stop generating if the venue quietly drops the walk from its site.

        A fetch failure is not treated as removal -- a site being down for a day
        should not silently empty the calendar.
        """
        marker = schedule.get("marker")
        url = schedule.get("url")
        if not marker or not url:
            return True

        try:
            page = fetcher.get(url)
        except RuntimeError as exc:
            log.warning("recurring: could not verify %r (%s); assuming it still runs",
                        schedule.get("title"), exc)
            return True

        normalised = " ".join(page.split()).lower()
        if " ".join(marker.split()).lower() in normalised:
            return True

        log.warning(
            "recurring: %r no longer appears on %s -- generating nothing. "
            "Check whether the walk has ended, or update the marker.",
            schedule.get("title"), url,
        )
        return False
