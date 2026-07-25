"""BNHS trail scraping and the declared-schedule expansion behind Sunder Nursery."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from delhi_events.models import Format, Topic
from delhi_events.sources.bnhs import NCR_RE, _parse_date
from delhi_events.sources.recurring import load_schedules, occurrences

CONFIG = "config/recurring.yaml"


# -- BNHS ------------------------------------------------------------------

def test_bnhs_listing_parses_without_error(bnhs):
    """The fixture is an all-Mumbai week, so the correct result is zero Delhi
    events -- but the parser must still have found and read the cards."""
    source, fetcher = bnhs
    assert source.fetch(fetcher) == []


def test_bnhs_raises_when_the_markup_changes(bnhs, tmp_path):
    """Returning [] on a redesign would look identical to a quiet week and slip
    past `doctor`, which is why this source raises instead."""
    from tests.conftest import FakeFetcher

    empty = tmp_path / "empty.html"
    empty.write_text("<html><body>site redesigned</body></html>")

    source, _ = bnhs
    broken = FakeFetcher([])
    broken._resolve = lambda url: empty  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="markup changed"):
        source.fetch(broken)


def test_bnhs_keeps_delhi_trails(bnhs, monkeypatch):
    """Same real fixture, with one Mumbai trail renamed to an Asola one, so the
    filter is exercised against genuine BNHS markup rather than a hand-built card."""
    source, fetcher = bnhs
    # Note the non-breaking spaces -- BNHS's own markup, and the reason the
    # adapter normalises whitespace before matching place names.
    html = fetcher._resolve("https://www.bnhs.org/nature-trails").read_text(
        encoding="utf-8", errors="replace"
    ).replace(
        "SGNP\xa0Bird\xa0Monitoring Programme",
        "Bird\xa0Walk at Asola\xa0Bhatti Wildlife Sanctuary",
    )
    assert "Asola" in html, "fixture markup changed; update this substitution"

    monkeypatch.setattr(fetcher, "get", lambda url, **kw: html)

    events = source.fetch(fetcher)
    assert len(events) == 1
    event = events[0]
    assert "Asola" in event.title
    assert event.format is Format.WALK
    assert Topic.NATURE in event.topics
    # BNHS publishes a date but never a start time.
    assert event.all_day
    assert event.booking_url.startswith("https://www.bnhs.org/nature-trails-book/")


def test_bnhs_parses_multi_day_camp_ranges():
    """"15nd - 18th January 2026" -- the month belongs to the second number, so
    a naive pattern returns the 18th as the start date."""
    from delhi_events.sources.bnhs import parse_range

    start, end = parse_range("15nd – 18th January 2026", datetime(2026, 7, 26))
    assert (start.month, start.day) == (1, 15)
    assert (end.month, end.day) == (1, 18)
    assert start.year == end.year == 2026


def test_ncr_filter_survives_non_breaking_spaces():
    """BNHS separates title words with \\xa0. Matched raw, a two-word place name
    like "sanjay van" would silently never hit."""
    from delhi_events.models import clean_text

    raw = "Bird\xa0Walk at Sanjay\xa0Van"  # no other NCR word to fall back on
    assert NCR_RE.search(raw) is None          # what the bug looked like
    assert NCR_RE.search(clean_text(raw))      # what the adapter now does


@pytest.mark.parametrize("place,expected", [
    ("Bird Walk at Asola Bhatti Wildlife Sanctuary", True),
    ("Sultanpur National Park Birding", True),
    ("Yamuna Biodiversity Park Walk", True),
    ("Okhla Bird Sanctuary Trail", True),
    ("BNHS Marine Walk at Juhu Beach", False),
    ("Malabar Hill Tree Walk", False),
    ("BNHS Awareness Bird Walk at Vetal Tekdi Trail", False),
])
def test_ncr_place_filter(place, expected):
    assert bool(NCR_RE.search(place)) is expected


@pytest.mark.parametrize("text,expected", [
    ("08th February 2026", (2026, 2, 8)),
    ("06 June 2026", (2026, 6, 6)),
    ("26th July 2026", (2026, 7, 26)),
])
def test_bnhs_date_formats(text, expected):
    parsed = _parse_date(text, datetime(2026, 7, 26))
    assert (parsed.year, parsed.month, parsed.day) == expected


def test_bnhs_missing_year_resolves_forward():
    """"17th May, Sunday" carries no year. A listing advertises what is coming,
    so a date well past must belong to next year, not this one."""
    today = datetime(2026, 7, 26)
    assert _parse_date("17th May, Sunday", today).year == 2027
    # ...but something a few days back is a stale listing, not next year's.
    assert _parse_date("20th July, Monday", today).year == 2026


# -- Sunder Nursery / recurring --------------------------------------------

def test_configured_schedule_expands_to_both_daily_sessions(sunder_nursery):
    source, fetcher = sunder_nursery
    events = source.fetch(fetcher)

    assert events, "the configured walk should still be generating"
    assert all(e.venue == "Sunder Nursery" for e in events)
    assert all(e.format is Format.WALK for e in events)
    assert all(Topic.NATURE in e.topics for e in events)

    # Two sessions a day, at 11:00 and 16:00.
    times = {e.start.strftime("%H:%M") for e in events}
    assert times == {"11:00", "16:00"}

    # Weekends only.
    assert all(e.start.weekday() in (5, 6) for e in events)


def test_same_day_sessions_are_separate_events(sunder_nursery):
    """Two walks at 11:00 and 16:00 share a title, date and meeting point.
    Without the time in the identity key the second overwrites the first."""
    source, fetcher = sunder_nursery
    events = source.fetch(fetcher)

    by_day = {}
    for event in events:
        by_day.setdefault(event.start.date(), []).append(event)

    a_day = next(v for v in by_day.values() if len(v) == 2)
    assert len({e.id for e in a_day}) == 2


def test_events_carry_the_unverified_caveat(sunder_nursery):
    """These are declared, not scraped -- every event says so and carries the
    venue's number."""
    source, fetcher = sunder_nursery
    event = source.fetch(fetcher)[0]
    assert "not from a dated listing" in event.description
    assert "9871066025" in event.description


def test_generation_stops_when_the_page_drops_the_walk(sunder_nursery, tmp_path):
    from tests.conftest import FakeFetcher

    page = tmp_path / "gone.html"
    page.write_text("<html><body>Sunder Nursery. No walks listed.</body></html>")

    source, _ = sunder_nursery
    fetcher = FakeFetcher([])
    fetcher._resolve = lambda url: page  # type: ignore[method-assign]

    assert source.fetch(fetcher) == []


def test_generation_stops_once_confirmation_lapses(sunder_nursery, monkeypatch):
    import delhi_events.sources.recurring as recurring

    stale = [dict(s, confirmed_until=date(2020, 1, 1))
             for s in load_schedules(CONFIG)]
    monkeypatch.setattr(recurring, "load_schedules", lambda *a, **k: stale)

    source, fetcher = sunder_nursery
    assert source.fetch(fetcher) == []


def test_a_fetch_failure_does_not_empty_the_calendar(sunder_nursery):
    """A venue site being down for a day must not silently drop the walks."""
    source, fetcher = sunder_nursery

    def boom(url, **kwargs):
        raise RuntimeError("connection reset")

    fetcher.get = boom  # type: ignore[method-assign]
    assert source.fetch(fetcher)


def test_horizon_never_runs_past_the_confirmation_date(sunder_nursery):
    source, fetcher = sunder_nursery
    schedule = next(s for s in load_schedules(CONFIG)
                    if s["source_id"] == "sunder_nursery")

    limit = schedule["confirmed_until"]
    if isinstance(limit, str):
        limit = datetime.strptime(limit, "%Y-%m-%d").date()

    for event in source.fetch(fetcher):
        assert event.start.date() <= limit


def test_occurrences_picks_the_right_weekdays():
    start = date(2026, 7, 27)  # a Monday
    days = occurrences([5, 6], start, horizon_days=13)
    assert days == [date(2026, 8, 1), date(2026, 8, 2),
                    date(2026, 8, 8), date(2026, 8, 9)]


def test_shipped_config_is_valid():
    """The schedule file is hand-edited; a typo there is silent otherwise."""
    schedules = load_schedules(CONFIG)
    assert schedules

    for schedule in schedules:
        for required in ("source_id", "title", "weekdays", "confirmed_until", "url"):
            assert schedule.get(required), f"{required} missing from {schedule.get('title')}"
        assert schedule.get("marker"), "a schedule without a marker cannot be verified"
        # A configured walk that already lapsed would silently produce nothing.
        limit = schedule["confirmed_until"]
        if isinstance(limit, str):
            limit = datetime.strptime(limit, "%Y-%m-%d").date()
        assert limit > date.today(), (
            f"{schedule['title']} lapsed on {limit} — re-confirm with the venue "
            "and update config/recurring.yaml"
        )
        assert limit < date.today() + timedelta(days=400), "confirmation window too long"
