"""Turn the store into the published artefacts: events.json, ICS feeds, site."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import db
from .models import IST, Event, Topic

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "site"
DIST_DIR = SITE_DIR / "dist"

SITE_TITLE = "Delhi Culture"
SITE_URL = ""  # set once deployed, used for absolute links in the feeds

# Subscribable slices, so a calendar client can take only what it wants rather
# than every listing in the city.
FEEDS: dict[str, tuple[str, set[Topic] | None]] = {
    "delhi-events": ("Delhi Culture — everything", None),
    "nature": ("Delhi Culture — nature & birds", {Topic.NATURE, Topic.BIRDS}),
    "art": ("Delhi Culture — art & photography", {Topic.ART, Topic.PHOTOGRAPHY}),
    # Heritage is deliberately absent: it belongs to recurring heritage *walks*
    # far more often than to talks, and letting it in swamped this feed with
    # the same weekend walk repeated for two months. Heritage events still
    # reach the full feed, and heritage walks carry a nature topic too.
    "ideas": (
        "Delhi Culture — talks & ideas",
        {Topic.SOCIOLOGY, Topic.HISTORY, Topic.SCIENCE, Topic.LITERATURE},
    ),
}


def event_to_dict(event: Event) -> dict:
    return {
        "id": event.id,
        "title": event.title,
        "description": event.description,
        "start": event.start.isoformat(),
        "end": event.end.isoformat() if event.end else None,
        "start_date": event.start.date().isoformat(),
        "end_date": (event.end or event.start).date().isoformat(),
        "time_label": "" if event.all_day else event.start.strftime("%-I:%M %p").lower(),
        "all_day": event.all_day,
        "multi_day": event.is_multi_day,
        "venue": event.venue,
        "sub_venue": event.sub_venue,
        "address": event.address,
        "source_id": event.source_id,
        "source_url": event.source_url,
        "format": event.format.value,
        "topics": [t.value for t in event.topics],
        "image_url": event.image_url,
        "booking_url": event.booking_url,
        "price": event.price,
    }


# -- ICS ------------------------------------------------------------------

def _ics_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """RFC 5545 caps content lines at 75 octets; continuations start with a
    space. Folding on characters is not strictly correct for multi-byte text,
    so fold on encoded bytes -- Delhi listings are full of Devanagari."""
    raw = line.encode("utf-8")
    if len(raw) <= 73:
        return line
    chunks, start = [], 0
    while start < len(raw):
        end = min(start + 73, len(raw))
        while end < len(raw) and (raw[end] & 0xC0) == 0x80:  # don't split a codepoint
            end -= 1
        chunks.append(raw[start:end].decode("utf-8"))
        start = end
    return "\r\n ".join(chunks)


def build_ics(events: list[Event], name: str, description: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//delhi-events//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(description)}",
        "X-WR-TIMEZONE:Asia/Kolkata",
    ]

    for event in events:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{event.id}@delhi-events")
        lines.append(f"DTSTAMP:{stamp}")

        if event.all_day:
            end_date = (event.end or event.start).date() + timedelta(days=1)  # DTEND is exclusive
            lines.append(f"DTSTART;VALUE=DATE:{event.start.date():%Y%m%d}")
            lines.append(f"DTEND;VALUE=DATE:{end_date:%Y%m%d}")
        else:
            start = event.start.astimezone(timezone.utc)
            end = (event.end or event.start + timedelta(hours=2)).astimezone(timezone.utc)
            lines.append(f"DTSTART:{start:%Y%m%dT%H%M%SZ}")
            lines.append(f"DTEND:{end:%Y%m%dT%H%M%SZ}")

        location = ", ".join(p for p in (event.sub_venue, event.venue, event.address) if p)
        lines.append(f"SUMMARY:{_ics_escape(event.title)}")
        if location:
            lines.append(f"LOCATION:{_ics_escape(location)}")

        body = event.description
        if event.source_url:
            body = f"{body}\n\n{event.source_url}".strip()
            lines.append(f"URL:{event.source_url}")
        if body:
            lines.append(f"DESCRIPTION:{_ics_escape(body)}")
        lines.append(f"CATEGORIES:{_ics_escape(','.join([event.format.value] + [t.value for t in event.topics]))}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


# -- site -----------------------------------------------------------------

def _day_groups(events: list[Event]) -> list[dict]:
    """Group by calendar day, repeating multi-day shows on each day they run.

    An exhibition that runs a fortnight should appear when you look at any day
    in that fortnight, not only on its opening date.
    """
    by_day: dict[date, list[Event]] = {}
    today = datetime.now(IST).date()

    for event in events:
        first = max(event.start.date(), today)
        last = (event.end or event.start).date()
        # Cap the span so a year-long permanent exhibit does not swamp every day.
        for offset in range((min(last, first + timedelta(days=120)) - first).days + 1):
            by_day.setdefault(first + timedelta(days=offset), []).append(event)

    groups = []
    for day in sorted(by_day):
        day_events = sorted(by_day[day], key=lambda e: (e.all_day is False, e.start))
        groups.append({
            "date": day.isoformat(),
            "label": day.strftime("%A %-d %B"),
            "is_today": day == today,
            "event_ids": [e.id for e in day_events],
        })
    return groups


def build(conn: sqlite3.Connection, dist: Path = DIST_DIR) -> dict[str, int]:
    events = db.active_events(conn)
    dist.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": datetime.now(IST).isoformat(timespec="seconds"),
        "events": [event_to_dict(e) for e in events],
        "days": _day_groups(events),
        "venues": sorted({e.venue for e in events}),
        "formats": sorted({e.format.value for e in events}),
        "topics": sorted({t.value for e in events for t in e.topics}),
    }
    (dist / "events.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1))

    counts = {"events": len(events)}
    for slug, (title, topics) in FEEDS.items():
        subset = events if topics is None else [e for e in events if topics & set(e.topics)]
        (dist / f"{slug}.ics").write_text(build_ics(subset, slug, title), encoding="utf-8")
        counts[f"{slug}.ics"] = len(subset)

    env = Environment(
        loader=FileSystemLoader(SITE_DIR),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("template.html")
    # Embedded rather than fetched so the page works straight off the filesystem.
    # Escaping "<" keeps a listing containing "</script>" from ending the block.
    inline = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    html = template.render(
        title=SITE_TITLE,
        payload=inline,
        generated_at=payload["generated_at"],
        feeds=[(slug, title) for slug, (title, _) in FEEDS.items()],
        event_count=len(events),
    )
    (dist / "index.html").write_text(html, encoding="utf-8")
    (dist / ".nojekyll").write_text("")  # GitHub Pages would otherwise eat _-prefixed paths

    log.info("built %s", dist)
    return counts
