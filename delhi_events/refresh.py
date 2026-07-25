"""Run adapters and write their output to the store."""

from __future__ import annotations

import logging
import sqlite3
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import db
from .fetch import Fetcher
from .sources.base import BaseSource, load_all

log = logging.getLogger(__name__)


@dataclass
class SourceResult:
    source_id: str
    ok: bool
    count: int = 0
    new: int = 0
    updated: int = 0
    duplicates: int = 0
    disappeared: int = 0
    error: str = ""
    tagged: int = 0


@dataclass
class RefreshReport:
    results: list[SourceResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def total(self) -> int:
        return sum(r.count for r in self.results)

    def render(self) -> str:
        width = max((len(r.source_id) for r in self.results), default=10)
        lines = []
        for r in self.results:
            if r.ok:
                lines.append(
                    f"  {r.source_id:<{width}}  {r.count:4d} events "
                    f"(+{r.new} new, ~{r.updated} updated, "
                    f"={r.duplicates} dup, -{r.disappeared} gone"
                    + (f", {r.tagged} llm-tagged" if r.tagged else "")
                    + ")"
                )
            else:
                lines.append(f"  {r.source_id:<{width}}  FAILED: {r.error.splitlines()[0][:90]}")
        lines.append(f"  {'total':<{width}}  {self.total:4d} events")
        return "\n".join(lines)


def refresh_source(conn: sqlite3.Connection, source: BaseSource, fetcher: Fetcher,
                   tag_with_llm: bool = False) -> SourceResult:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        events = source.fetch(fetcher)
    except Exception as exc:  # noqa: BLE001 - one venue must never sink the run
        error = f"{exc}\n{traceback.format_exc()}"
        log.error("%s failed: %s", source.id, exc)
        db.record_run(conn, source.id, started, ok=False, count=0, error=error)
        conn.commit()
        return SourceResult(source.id, ok=False, error=str(exc))

    tagged = 0
    if tag_with_llm and events:
        from .llm import tag_events  # imported lazily; anthropic is optional

        tagged = tag_events(events)

    result = SourceResult(source.id, ok=True, count=len(events), tagged=tagged)
    seen: set[str] = set()

    for event in events:
        outcome = db.upsert(conn, event)
        seen.add(event.id)
        if outcome == "new":
            result.new += 1
        elif outcome == "updated":
            result.updated += 1
        elif outcome == "duplicate":
            result.duplicates += 1

    result.disappeared = db.reconcile(conn, source.id, seen)
    db.record_run(conn, source.id, started, ok=True, count=len(events))
    conn.commit()
    return result


def refresh(conn: sqlite3.Connection, only: list[str] | None = None,
            cache_ttl: int = 0, tag_with_llm: bool = False) -> RefreshReport:
    fetcher = Fetcher(cache_ttl=cache_ttl)
    report = RefreshReport()
    for source in load_all(only=only):
        log.info("refreshing %s", source.id)
        report.results.append(refresh_source(conn, source, fetcher, tag_with_llm))
    return report
