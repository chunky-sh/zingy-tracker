"""Claude-assisted classification and extraction.

Deliberately narrow. The keyword rules in ``taxonomy.py`` handle the four v1
sources; this module covers only what they cannot:

1. Topic tagging for events the rules could not classify confidently.
2. Extracting events from unstructured text -- the IHC monthly PDF, a pasted
   Instagram caption, a mailing-list announcement.

Two guardrails, because a wrong date is worse than a missing event:

* **Dates must appear in the source.** Any extracted date whose day and month
  are not present in the input text is rejected. Date hallucination is the one
  failure mode that would quietly poison the calendar.
* **Responses are cached by content hash**, so re-running a refresh costs
  nothing and a broken parser cannot run up a bill in a loop.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .models import Event, Format, Topic
from .taxonomy import classify

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "llm"

# Haiku is the right tier here: the inputs are a title and a short blurb, and
# the label set is fixed and small. Override for a one-off backfill where
# accuracy matters more than cost.
MODEL = os.environ.get("DELHI_EVENTS_MODEL", "claude-haiku-4-5")

# Note: no `output_config.effort` -- it is rejected on Haiku 4.5. No
# `cache_control` either: Haiku 4.5's minimum cacheable prefix is 4096 tokens
# and this system prompt is nowhere near that, so a breakpoint would silently
# do nothing while still costing the write premium.
MAX_TOKENS = 4096
BATCH_SIZE = 12

FORMATS = [f.value for f in Format]
TOPICS = [t.value for t in Topic]

TAG_SYSTEM = f"""You label cultural events in Delhi for a personal events tracker.

For each event you are given, return its format (exactly one) and its topics \
(zero to three, ordered most relevant first).

Formats: {", ".join(FORMATS)}
Topics: {", ".join(TOPICS)}

Guidance:
- format is the shape of the event; topics are what it is about. They are \
independent: a birding walk is format "walk" with topic "nature"/"birds"; a \
photography show about the Yamuna is format "exhibition" with topics \
"photography" and "nature".
- Only assign a topic the event is genuinely about. Do not assign a topic \
because a word appears once in passing.
- Use "other" for format only when nothing else fits.
- Return one entry per input event, in the same order, echoing the given index."""

EXTRACT_SYSTEM = """You extract cultural events from unstructured text \
(programme listings, PDFs, social media captions) for a Delhi events tracker.

Rules:
- Extract only actual events with a date. Skip venue blurbs, ads and \
membership notices.
- Copy dates exactly as written in the source. Never infer, correct or \
invent a date. If a year is absent, use the year given to you as context.
- Times are local (Asia/Kolkata), 24-hour. Omit the time if none is stated.
- Set end_date only when the source gives an explicit end or range.
- Keep the title as written, minus any leading category prefix.
- If the text contains no events, return an empty list."""


class TagResult(BaseModel):
    index: int
    format: str
    topics: list[str] = Field(default_factory=list)


class TagResults(BaseModel):
    results: list[TagResult]


class ExtractedEvent(BaseModel):
    title: str
    start_date: str = Field(description="YYYY-MM-DD")
    start_time: str = Field(default="", description="HH:MM, 24-hour, or empty")
    end_date: str = Field(default="", description="YYYY-MM-DD, or empty")
    sub_venue: str = ""
    description: str = ""


class ExtractedEvents(BaseModel):
    events: list[ExtractedEvent]


def _client():
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency is optional
        raise RuntimeError(
            "anthropic is not installed. Run: pip install anthropic"
        ) from exc
    return anthropic.Anthropic()


def _cache_key(kind: str, payload: str) -> Path:
    digest = hashlib.sha1(f"{MODEL}|{kind}|{payload}".encode()).hexdigest()
    return CACHE_DIR / f"{kind}-{digest}.json"


def _cached_call(kind: str, payload: str, schema: type[BaseModel], prompt: str,
                 system: str) -> BaseModel | None:
    path = _cache_key(kind, payload)
    if path.exists():
        try:
            return schema.model_validate_json(path.read_text())
        except Exception:  # noqa: BLE001 - a stale cache entry is not fatal
            log.debug("discarding unreadable cache entry %s", path)

    try:
        response = _client().messages.parse(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
        )
    except Exception as exc:  # noqa: BLE001 - never let tagging break a refresh
        log.warning("llm call failed (%s): %s", kind, exc)
        return None

    if response.stop_reason == "refusal":
        log.warning("llm refused the %s request", kind)
        return None

    parsed = response.parsed_output
    if parsed is not None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(parsed.model_dump_json())
    return parsed


# -- topic tagging --------------------------------------------------------

def _needs_tagging(event: Event) -> bool:
    _, _, confident = classify(event.title, event.description)
    return not confident


def tag_events(events: list[Event], batch_size: int = BATCH_SIZE) -> int:
    """Fill in format and topics for events the keyword rules were unsure of.

    Mutates the events in place and returns how many were changed.
    """
    pending = [e for e in events if _needs_tagging(e)]
    if not pending:
        return 0

    log.info("llm: tagging %d of %d events", len(pending), len(events))
    tagged = 0

    for offset in range(0, len(pending), batch_size):
        batch = pending[offset:offset + batch_size]
        lines = []
        for index, event in enumerate(batch):
            blurb = re.sub(r"\s+", " ", event.description)[:400]
            lines.append(
                f"[{index}] title: {event.title}\n"
                f"     venue: {event.sub_venue or event.venue}\n"
                f"     description: {blurb or '(none)'}"
            )
        prompt = "\n\n".join(lines)

        result = _cached_call("tag", prompt, TagResults, prompt, TAG_SYSTEM)
        if result is None:
            continue

        for entry in result.results:
            if not 0 <= entry.index < len(batch):
                continue
            event = batch[entry.index]
            try:
                event.format = Format(entry.format)
            except ValueError:
                log.debug("llm returned unknown format %r", entry.format)
            topics = []
            for name in entry.topics:
                try:
                    topics.append(Topic(name))
                except ValueError:
                    log.debug("llm returned unknown topic %r", name)
            if topics:
                event.topics = sorted(set(topics), key=lambda t: t.value)
            tagged += 1

    return tagged


# -- extraction from unstructured text ------------------------------------

MONTHS = ("january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december")


def date_is_supported(date_str: str, source: str) -> bool:
    """True when the day and month of ``date_str`` actually appear in ``source``.

    Cheap and strict on purpose. It cannot catch every hallucination, but it
    catches the one that matters -- a plausible date the source never mentions.
    """
    try:
        parsed = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return False

    haystack = source.lower()
    day = parsed.day
    # "5", "05", "5th" all count as the day appearing.
    day_present = re.search(rf"\b0?{day}(?:st|nd|rd|th)?\b", haystack) is not None

    month_name = MONTHS[parsed.month - 1]
    month_present = (
        month_name in haystack
        or month_name[:3] in haystack
        or re.search(rf"\b0?{parsed.month}\s*[/-]", haystack) is not None
        or f"-{parsed.month:02d}-" in haystack
    )
    return day_present and month_present


def extract_events_from_text(
    text: str,
    source_id: str,
    venue: str,
    source_url: str = "",
    address: str = "",
    default_year: int | None = None,
) -> list[Event]:
    """Extract events from free text. Rejects dates the text does not support."""
    text = text.strip()
    if not text:
        return []

    year = default_year or datetime.now().year
    prompt = f"The current year is {year}, for use when the text omits one.\n\n{text[:60000]}"

    result = _cached_call("extract", prompt, ExtractedEvents, prompt, EXTRACT_SYSTEM)
    if result is None:
        return []

    events: list[Event] = []
    for item in result.events:
        if not date_is_supported(item.start_date, text):
            log.warning("rejected unsupported date %s for %r", item.start_date, item.title)
            continue

        try:
            start = datetime.strptime(item.start_date, "%Y-%m-%d")
        except ValueError:
            continue

        all_day = not item.start_time
        if item.start_time:
            try:
                clock = datetime.strptime(item.start_time, "%H:%M")
                start = start.replace(hour=clock.hour, minute=clock.minute)
            except ValueError:
                all_day = True

        end = None
        if item.end_date and date_is_supported(item.end_date, text):
            try:
                end = datetime.strptime(item.end_date, "%Y-%m-%d")
            except ValueError:
                end = None

        fmt, topics, _ = classify(item.title, item.description)

        try:
            events.append(Event(
                source_id=source_id,
                venue=venue,
                address=address,
                source_url=source_url,
                title=item.title,
                description=item.description,
                start=start,
                end=end,
                all_day=all_day,
                sub_venue=item.sub_venue,
                format=fmt,
                topics=topics,
            ))
        except ValueError as exc:
            log.warning("llm produced an invalid event %r: %s", item.title, exc)

    return events
