"""Command line entry point: refresh | build | list | doctor | add-manual."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import build as build_mod
from . import db
from .models import IST
from .refresh import refresh
from .sources.base import load_configs

# A source that starts returning nothing looks identical to a quiet week, so
# `doctor` needs both a floor and a drop threshold to tell rot from calm.
DROP_RATIO = 0.5


def _log_setup(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def cmd_refresh(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    report = refresh(conn, only=args.source, cache_ttl=args.cache_ttl, tag_with_llm=args.llm)
    print("\nRefresh report:")
    print(report.render())
    conn.close()
    return 0 if report.ok else 1


def cmd_build(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    counts = build_mod.build(conn, Path(args.dist))
    print(f"\nBuilt {args.dist}:")
    for name, count in counts.items():
        print(f"  {name:<22} {count}")
    conn.close()
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    events = db.active_events(conn)
    today = datetime.now(IST).date()

    if args.days:
        cutoff = (today + timedelta(days=args.days))
        # An exhibition that opened a fortnight ago is still on, so it belongs
        # in "the next N days" -- but printing it under its opening date put a
        # past heading in the output. Split them out instead.
        events = [e for e in events if e.start.date() <= cutoff]
    if args.topic:
        events = [e for e in events if any(t.value == args.topic for t in e.topics)]
    if args.format:
        events = [e for e in events if e.format.value == args.format]
    if args.venue:
        events = [e for e in events if args.venue.lower() in e.venue.lower()]

    if not events:
        print("No matching events.")
        return 0

    def show(event, when: str) -> None:
        tags = ",".join(t.value for t in event.topics)
        print(f"  {when:>9}  {event.title[:62]}")
        print(f"             {event.venue}"
              + (f" — {event.sub_venue}" if event.sub_venue else "")
              + f"  [{event.format.value}{'/' + tags if tags else ''}]")

    running = [e for e in events if e.start.date() < today]
    upcoming = [e for e in events if e.start.date() >= today]

    if running:
        print("\n\033[1mAlready running\033[0m")
        for event in running:
            last = (event.end or event.start).date()
            show(event, "until" if last != today else "today")
            print(f"             \033[2mruns to {last:%a %d %b}\033[0m")

    current = None
    for event in upcoming:
        day = event.start.date()
        if day != current:
            current = day
            label = " · today" if day == today else ""
            print(f"\n\033[1m{day:%A %d %B}\033[0m{label}")
        show(event, "all day" if event.all_day else f"{event.start:%H:%M}")

    print(f"\n{len(events)} events."
          + (f" {len(running)} already running." if running else ""))
    conn.close()
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    conn = db.connect(args.db)
    enabled = [c for c in load_configs() if c.enabled]
    problems: list[str] = []
    notes: list[str] = []

    for config in enabled:
        source_id = config.id
        rows = conn.execute(
            "SELECT ok, count, error, started_at FROM runs WHERE source_id = ? "
            "ORDER BY started_at DESC LIMIT 5",
            (source_id,),
        ).fetchall()

        if not rows:
            problems.append(f"{source_id}: has never run")
            continue

        latest = rows[0]
        if not latest["ok"]:
            problems.append(f"{source_id}: last run failed — {latest['error'].splitlines()[0][:100]}")
            continue
        if latest["count"] == 0:
            if config.allow_empty:
                # BNHS genuinely has nothing in Delhi most weeks; Sunder Nursery
                # stops when its confirmation lapses. Worth saying, not failing.
                notes.append(f"{source_id}: 0 events (expected for this source)")
            else:
                problems.append(f"{source_id}: returned 0 events (parser likely broken)")
            continue

        previous = [r["count"] for r in rows[1:] if r["ok"]]
        if previous:
            baseline = max(previous)
            if baseline and latest["count"] < baseline * DROP_RATIO:
                problems.append(
                    f"{source_id}: {latest['count']} events, down from {baseline} — "
                    "possible partial parse"
                )

    total = len(db.active_events(conn))
    print(f"{len(enabled)} sources enabled, {total} active future events.")

    if notes:
        print("\nNotes:")
        for note in notes:
            print(f"  · {note}")

    if problems:
        print("\nProblems:")
        for problem in problems:
            print(f"  ✗ {problem}")
        conn.close()
        return 1

    print("All sources healthy.")
    conn.close()
    return 0


def cmd_add_manual(args: argparse.Namespace) -> int:
    """Turn a pasted blurb (an Instagram caption, a mailing list note) into an
    event. Shows what it extracted and asks before writing."""
    from .llm import extract_events_from_text

    text = Path(args.file).read_text() if args.file else sys.stdin.read()
    if not text.strip():
        print("Nothing to read.", file=sys.stderr)
        return 1

    events = extract_events_from_text(
        text, source_id=args.source_id, venue=args.venue, source_url=args.url
    )
    if not events:
        print("No events could be extracted.")
        return 1

    for event in events:
        print(f"\n  {event.title}")
        print(f"  {event.start:%a %d %b %Y, %H:%M}"
              + (f" → {event.end:%a %d %b %Y, %H:%M}" if event.end else ""))
        print(f"  {event.venue}" + (f" — {event.sub_venue}" if event.sub_venue else ""))
        print(f"  [{event.format.value}] {', '.join(t.value for t in event.topics)}")

    if input(f"\nAdd {len(events)} event(s)? [y/N] ").strip().lower() != "y":
        print("Not added.")
        return 0

    conn = db.connect(args.db)
    for event in events:
        print(f"  {db.upsert(conn, event)}: {event.title}")
    conn.commit()
    conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="delhi-events", description=__doc__)
    parser.add_argument("--db", default=str(db.DEFAULT_DB), help="path to the SQLite store")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("refresh", help="scrape all sources into the store")
    p.add_argument("--source", action="append", help="limit to a source id (repeatable)")
    p.add_argument("--cache-ttl", type=int, default=0,
                   help="reuse responses newer than N seconds (development aid)")
    p.add_argument("--llm", action="store_true", help="tag ambiguous events with Claude")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("build", help="write events.json, ICS feeds and the site")
    p.add_argument("--dist", default=str(build_mod.DIST_DIR))
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("list", help="print upcoming events")
    p.add_argument("--days", type=int, help="only the next N days")
    p.add_argument("--topic")
    p.add_argument("--format")
    p.add_argument("--venue")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("doctor", help="check sources for silent breakage")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("add-manual", help="extract an event from pasted text")
    p.add_argument("--file", help="read from a file instead of stdin")
    p.add_argument("--source-id", default="manual")
    p.add_argument("--venue", required=True)
    p.add_argument("--url", default="")
    p.set_defaults(func=cmd_add_manual)

    args = parser.parse_args(argv)
    _log_setup(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
