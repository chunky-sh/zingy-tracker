"""SQLite store: upsert, near-duplicate merging, and run bookkeeping."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import IST, Event, Format, Status, Topic

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "events.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id              TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL,
    source_url      TEXT DEFAULT '',
    source_event_id TEXT DEFAULT '',
    title           TEXT NOT NULL,
    description     TEXT DEFAULT '',
    start           TEXT NOT NULL,
    "end"           TEXT,
    all_day         INTEGER DEFAULT 0,
    venue           TEXT NOT NULL,
    sub_venue       TEXT DEFAULT '',
    address         TEXT DEFAULT '',
    format          TEXT DEFAULT 'other',
    topics          TEXT DEFAULT '',
    image_url       TEXT DEFAULT '',
    booking_url     TEXT DEFAULT '',
    price           TEXT DEFAULT '',
    status          TEXT DEFAULT 'active',
    content_hash    TEXT DEFAULT '',
    start_date      TEXT NOT NULL,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_start  ON events(start_date);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source_id);
CREATE INDEX IF NOT EXISTS idx_events_dedupe ON events(start_date, venue);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    ok          INTEGER DEFAULT 0,
    count       INTEGER DEFAULT 0,
    error       TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_source ON runs(source_id, started_at);
"""

_STOPWORDS = {"a", "an", "the", "of", "and", "in", "on", "at", "to", "for", "by", "with"}


def _tokens(title: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", title.lower()) if t not in _STOPWORDS}


def title_similarity(a: str, b: str) -> float:
    """Jaccard over content words, ignoring any leading category prefix.

    Chosen over sequence matching because venues reorder and re-prefix titles
    far more often than they misspell them. Stripping the prefix first means
    "Exhibition- Flux of Being" and "Flux of Being" score 1.0, so the threshold
    can stay tight enough to keep genuinely different shows apart.
    """
    from .taxonomy import split_title_prefix

    ta, tb = _tokens(split_title_prefix(a)[0]), _tokens(split_title_prefix(b)[0])
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        source_id=row["source_id"],
        source_url=row["source_url"],
        source_event_id=row["source_event_id"],
        title=row["title"],
        description=row["description"],
        start=datetime.fromisoformat(row["start"]),
        end=datetime.fromisoformat(row["end"]) if row["end"] else None,
        all_day=bool(row["all_day"]),
        venue=row["venue"],
        sub_venue=row["sub_venue"],
        address=row["address"],
        format=Format(row["format"]),
        topics=[Topic(t) for t in row["topics"].split(",") if t],
        image_url=row["image_url"],
        booking_url=row["booking_url"],
        price=row["price"],
        status=Status(row["status"]),
    )


def find_duplicate(conn: sqlite3.Connection, event: Event, threshold: float = 0.85) -> str | None:
    """Same day, same venue, compatible room, near-identical title -> the same
    event reached us twice (e.g. IHC listing a show on both its calendar and its
    exhibitions page).

    Differing sub_venues are treated as genuinely different events: a title can
    legitimately run in two galleries of the same building simultaneously.
    Differing start times likewise -- Sunder Nursery's heritage walk runs at
    11:00 and again at 16:00, and those are two walks, not one listed twice.
    """
    rows = conn.execute(
        'SELECT id, title, sub_venue, start, all_day FROM events '
        "WHERE start_date = ? AND venue = ? AND id != ?",
        (event.start.date().isoformat(), event.venue, event.id),
    ).fetchall()

    for row in rows:
        if row["sub_venue"] and event.sub_venue and row["sub_venue"] != event.sub_venue:
            continue
        if not row["all_day"] and not event.all_day:
            existing = datetime.fromisoformat(row["start"])
            if existing.strftime("%H:%M") != event.start.strftime("%H:%M"):
                continue
        if title_similarity(event.title, row["title"]) >= threshold:
            return row["id"]
    return None


def upsert(conn: sqlite3.Connection, event: Event) -> str:
    """Insert or update. Returns 'new', 'updated', 'unchanged' or 'duplicate'."""
    now = _now()
    existing = conn.execute("SELECT * FROM events WHERE id = ?", (event.id,)).fetchone()

    if existing is None:
        dup_id = find_duplicate(conn, event)
        if dup_id is not None:
            conn.execute("UPDATE events SET last_seen = ? WHERE id = ?", (now, dup_id))
            return "duplicate"

    values = {
        "id": event.id,
        "source_id": event.source_id,
        "source_url": event.source_url,
        "source_event_id": event.source_event_id,
        "title": event.title,
        "description": event.description,
        "start": event.start.isoformat(),
        "end": event.end.isoformat() if event.end else None,
        "all_day": int(event.all_day),
        "venue": event.venue,
        "sub_venue": event.sub_venue,
        "address": event.address,
        "format": event.format.value,
        "topics": ",".join(t.value for t in event.topics),
        "image_url": event.image_url,
        "booking_url": event.booking_url,
        "price": event.price,
        "status": event.status.value,
        "content_hash": event.content_hash,
        "start_date": event.start.date().isoformat(),
        "last_seen": now,
    }

    if existing is None:
        values["first_seen"] = now
        cols = ", ".join(f'"{k}"' for k in values)
        placeholders = ", ".join(f":{k}" for k in values)
        conn.execute(f"INSERT INTO events ({cols}) VALUES ({placeholders})", values)
        return "new"

    if existing["content_hash"] == event.content_hash:
        conn.execute("UPDATE events SET last_seen = ?, status = ? WHERE id = ?",
                     (now, event.status.value, event.id))
        return "unchanged"

    assignments = ", ".join(f'"{k}" = :{k}' for k in values if k != "id")
    conn.execute(f"UPDATE events SET {assignments} WHERE id = :id", values)
    return "updated"


def reconcile(conn: sqlite3.Connection, source_id: str, seen_ids: set[str]) -> int:
    """Flag future events this source stopped listing.

    Past events are left alone -- they simply happened. Only a *future* event
    vanishing from a listing means cancelled or rescheduled, and that is worth
    surfacing rather than deleting outright.
    """
    today = datetime.now(IST).date().isoformat()
    rows = conn.execute(
        "SELECT id FROM events WHERE source_id = ? AND start_date >= ? AND status = 'active'",
        (source_id, today),
    ).fetchall()
    gone = [r["id"] for r in rows if r["id"] not in seen_ids]
    conn.executemany(
        "UPDATE events SET status = 'disappeared' WHERE id = ?", [(i,) for i in gone]
    )
    return len(gone)


def active_events(conn: sqlite3.Connection, include_past: bool = False) -> list[Event]:
    today = datetime.now(IST).date().isoformat()
    if include_past:
        rows = conn.execute(
            "SELECT * FROM events WHERE status = 'active' ORDER BY start"
        ).fetchall()
    else:
        # COALESCE keeps multi-day exhibitions visible until their final day.
        rows = conn.execute(
            'SELECT * FROM events WHERE status = \'active\' '
            'AND date(COALESCE("end", start)) >= ? ORDER BY start',
            (today,),
        ).fetchall()
    return [_row_to_event(r) for r in rows]


def record_run(conn: sqlite3.Connection, source_id: str, started: str,
               ok: bool, count: int, error: str = "") -> None:
    conn.execute(
        "INSERT INTO runs (source_id, started_at, finished_at, ok, count, error) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (source_id, started, _now(), int(ok), count, error[:2000]),
    )


def last_run_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Most recent successful count per source, for drift detection."""
    rows = conn.execute(
        "SELECT source_id, count FROM runs r WHERE ok = 1 AND started_at = "
        "(SELECT MAX(started_at) FROM runs WHERE source_id = r.source_id AND ok = 1) "
        "GROUP BY source_id"
    ).fetchall()
    return {r["source_id"]: r["count"] for r in rows}
