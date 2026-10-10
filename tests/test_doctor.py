"""`doctor`'s health rules, judged from run rows without a database."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from delhi_events.cli import FAIL_STREAK, STALE_DAYS, source_health
from delhi_events.sources.base import SourceConfig

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def config(**overrides) -> SourceConfig:
    return SourceConfig(id="venue", adapter="gallery", name="Venue", url="https://x", **overrides)


def run(days_ago: float, ok: bool = True, count: int = 10, error: str = "") -> dict:
    started = (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return {"ok": int(ok), "count": count, "error": error, "started_at": started}


def test_a_single_failure_is_a_note_not_a_problem():
    rows = [run(0, ok=False, error="read timed out"), run(1), run(2)]
    problem, note = source_health(config(), rows, NOW)
    assert problem is None
    assert "1 in a row" in note and "read timed out" in note


def test_a_failure_that_persists_is_a_problem():
    rows = [run(i, ok=False, error="403 Forbidden") for i in range(FAIL_STREAK)] + [run(FAIL_STREAK)]
    problem, _ = source_health(config(), rows, NOW)
    assert problem == f"venue: failed the last {FAIL_STREAK} runs — 403 Forbidden"


def test_a_source_that_has_never_succeeded_is_a_problem():
    problem, _ = source_health(config(), [run(0, ok=False, error="boom")], NOW)
    assert "failed the last 1 runs" in problem


def test_recovery_clears_the_failure():
    rows = [run(0), run(1, ok=False, error="x"), run(2, ok=False, error="x")]
    assert source_health(config(), rows, NOW) == (None, None)


def test_a_stale_local_only_source_says_how_to_fix_it():
    # CI skips local_only sources, so their newest run is simply old.
    rows = [run(STALE_DAYS + 1)]
    problem, _ = source_health(config(local_only=True), rows, NOW)
    assert problem == f"venue: no successful scrape in {STALE_DAYS + 1} days — run `make refresh-local`"


def test_a_recent_local_only_source_is_healthy():
    assert source_health(config(local_only=True), [run(STALE_DAYS - 1)], NOW) == (None, None)


@pytest.mark.parametrize("allow_empty", [False, True])
def test_zero_events_fails_unless_allowed(allow_empty):
    problem, note = source_health(config(allow_empty=allow_empty), [run(0, count=0), run(1)], NOW)
    assert (problem is None) == allow_empty
    assert (note is not None) == allow_empty


def test_a_steep_drop_is_flagged_as_a_partial_parse():
    problem, _ = source_health(config(), [run(0, count=4), run(1, count=10)], NOW)
    assert problem == "venue: 4 events, down from 10 — possible partial parse"


def test_counts_are_judged_on_the_last_good_run_after_a_blip():
    rows = [run(0, ok=False, error="timeout"), run(1, count=0), run(2)]
    problem, _ = source_health(config(), rows, NOW)
    assert "returned 0 events" in problem
