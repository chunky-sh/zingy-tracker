"""Event model and normalisation.

Adapters produce ``Event`` objects; everything downstream (db, taxonomy, build)
speaks only this type.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator

IST = ZoneInfo("Asia/Kolkata")


class Format(str, Enum):
    """What kind of thing it is. One per event."""

    EXHIBITION = "exhibition"
    TALK = "talk"
    WALK = "walk"
    WORKSHOP = "workshop"
    FILM = "film"
    PERFORMANCE = "performance"
    FESTIVAL = "festival"
    OTHER = "other"


class Topic(str, Enum):
    """What it is about. Zero or more per event, and deliberately orthogonal to
    Format -- "nature" spans a walk, a talk and a photo exhibition alike."""

    ART = "art"
    NATURE = "nature"
    BIRDS = "birds"
    HERITAGE = "heritage"
    SOCIOLOGY = "sociology"
    CINEMA = "cinema"
    MUSIC = "music"
    DANCE = "dance"
    THEATRE = "theatre"
    LITERATURE = "literature"
    PHOTOGRAPHY = "photography"
    HISTORY = "history"
    SCIENCE = "science"


class Status(str, Enum):
    ACTIVE = "active"
    ENDED = "ended"
    DISAPPEARED = "disappeared"


def slugify(text: str) -> str:
    """Lowercase ASCII slug. Used for stable ids, so it must be deterministic
    across runs and insensitive to the punctuation noise venues love."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return text.strip("-")


def clean_text(raw: str | None) -> str:
    """Collapse whitespace and strip the non-breaking spaces and zero-width
    characters that CMS exports are full of."""
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", raw)
    text = text.replace("​", "").replace("﻿", "")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# Titles a CMS emits for an event whose copy has not been written yet. Goethe's
# API serves these with isDisplayed=true, so that flag is no help. Publishing
# "Loading..." to a calendar is worse than publishing nothing: the entry gains a
# real title later, and since ids derive from the title, the placeholder would
# linger as a separate ghost record alongside the real one.
_PLACEHOLDER_TITLES = re.compile(
    r"^\s*(loading\.*|please wait\.*|untitled|tbd|tba|to be announced|"
    r"coming soon|test|testing|xxx+|placeholder|lorem ipsum.*|n/?a|-+)\s*$",
    re.I,
)


def is_placeholder_title(title: str) -> bool:
    """True for a title that is CMS scaffolding rather than an event name."""
    return not title.strip() or bool(_PLACEHOLDER_TITLES.match(title))


class Event(BaseModel):
    source_id: str
    source_url: str = ""
    source_event_id: str = ""

    title: str
    description: str = ""

    start: datetime
    end: datetime | None = None
    all_day: bool = False

    venue: str
    sub_venue: str = ""
    address: str = ""

    format: Format = Format.OTHER
    topics: list[Topic] = Field(default_factory=list)

    image_url: str = ""
    booking_url: str = ""
    price: str = ""

    status: Status = Status.ACTIVE

    @field_validator("title", "description", "sub_venue", "address", "price", mode="before")
    @classmethod
    def _clean(cls, v: object) -> str:
        return clean_text(v if isinstance(v, str) else None)

    @field_validator("start", "end", mode="before")
    @classmethod
    def _localise(cls, v: object) -> object:
        """Naive datetimes from scrapers are always IST -- venues publish local
        time. A datetime that reaches the DB without a tzinfo silently shifts by
        5h30m in the ICS export, so pin it here rather than at each call site."""
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=IST)
        if isinstance(v, date) and not isinstance(v, datetime):
            return datetime(v.year, v.month, v.day, tzinfo=IST)
        return v

    @model_validator(mode="after")
    def _drop_repeated_title(self) -> Event:
        """Venues routinely open the blurb by restating the event name, so it
        renders twice -- once as the heading, once as the first line beneath it.
        Fixed here rather than in the template so the ICS feeds benefit too."""
        if not self.description or not self.title:
            return self
        head, _, rest = self.description.partition("\n")
        if slugify(head) and slugify(head) == slugify(self.title):
            object.__setattr__(self, "description", rest.lstrip("\n "))
        return self

    @model_validator(mode="after")
    def _check_range(self) -> Event:
        if not self.title:
            raise ValueError("event has no title")
        if self.end is not None and self.end < self.start:
            # Venues publish "22:00 - 01:00" for a late show without moving the
            # date on. Roll the end forward a day when that produces a sane
            # duration; otherwise the pair is junk and the end is dropped.
            rolled = self.end + timedelta(days=1)
            if timedelta(0) < rolled - self.start <= timedelta(hours=12):
                object.__setattr__(self, "end", rolled)
            else:
                object.__setattr__(self, "end", None)
        return self

    @property
    def id(self) -> str:
        """Stable across URL changes, description edits and image swaps -- the
        things venues churn. Deliberately *not* derived from source_url.

        sub_venue is part of the key because IHC runs the same show in two
        galleries at once ("Illumination'26" in both the Visual Arts Gallery and
        the Open Palm Court Gallery); without it the second overwrites the first.

        The clock time is part of it for the same reason: Sunder Nursery's
        heritage walk runs twice a day, at 11:00 and 16:00, under one title in
        one place. All-day events omit it, so a venue adding a time to a listing
        it previously published as date-only does not fork the event.
        """
        parts = [
            self.source_id,
            slugify(self.title),
            self.start.date().isoformat(),
            slugify(self.sub_venue),
        ]
        if not self.all_day:
            parts.append(self.start.strftime("%H:%M"))
        return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]

    @property
    def content_hash(self) -> str:
        """Changes when anything user-visible changes, so we can tell a genuine
        update from an unchanged re-scrape."""
        parts = [
            self.title,
            self.description,
            self.start.isoformat(),
            self.end.isoformat() if self.end else "",
            self.venue,
            self.sub_venue,
            self.format.value,
            ",".join(sorted(t.value for t in self.topics)),
        ]
        return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]

    @property
    def is_multi_day(self) -> bool:
        return self.end is not None and self.end.date() > self.start.date()

    def occurs_on(self, day: date) -> bool:
        last = self.end.date() if self.end else self.start.date()
        return self.start.date() <= day <= last
