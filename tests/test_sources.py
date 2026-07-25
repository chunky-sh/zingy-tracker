"""Golden-file tests for each adapter.

These exist to catch the failure mode that matters: a venue redesigns its site,
the selectors stop matching, and the adapter quietly returns nothing. A test
that asserts real field values fails loudly instead.

Refresh the fixtures with `make fixtures` when a site legitimately changes.
"""

from __future__ import annotations

from delhi_events.models import Format, Topic


def test_iic_parses_listing(iic):
    source, fetcher = iic
    events = source.fetch(fetcher)

    assert len(events) == 11

    unsung = next(e for e in events if "UNSUNG" in e.title)
    # The category prefix is stripped, but still informs the format.
    assert unsung.title.startswith("UNSUNG")
    assert unsung.format is Format.EXHIBITION
    assert unsung.venue == "India International Centre"
    assert unsung.sub_venue == "Art Gallery, Kamaladevi Complex, IIC"
    assert unsung.start.strftime("%Y-%m-%d %H:%M") == "2026-07-22 11:00"
    # The end date comes from the detail page; the listing card has no end.
    assert unsung.end is not None
    assert unsung.end.strftime("%Y-%m-%d %H:%M") == "2026-08-04 19:00"
    assert unsung.is_multi_day
    assert Topic.ART in unsung.topics
    assert unsung.image_url.startswith("https://")
    assert len(unsung.description) > 200


def test_iic_times_are_ist(iic):
    source, fetcher = iic
    events = source.fetch(fetcher)
    for event in events:
        assert event.start.tzinfo is not None
        assert event.start.utcoffset().total_seconds() == 5.5 * 3600


def test_ihc_parses_calendar_and_exhibitions(ihc):
    source, fetcher = ihc
    events = source.fetch(fetcher)

    calendar = [e for e in events if not e.all_day]
    exhibitions = [e for e in events if e.all_day]
    assert len(calendar) == 8
    assert len(exhibitions) == 16

    playboy = next(e for e in calendar if "Playboy" in e.title)
    assert playboy.start.strftime("%Y-%m-%d %H:%M") == "2026-07-26 19:00"
    assert playboy.sub_venue == "The Stein Auditorium"
    assert playboy.format is Format.FILM
    assert playboy.booking_url.startswith("https://in.bookmyshow.com/")


def test_ihc_reads_month_headings_not_just_the_first(ihc):
    """The calendar renders two months; a parser that only reads the first
    heading dates August events into July."""
    source, fetcher = ihc
    events = source.fetch(fetcher)
    months = {e.start.strftime("%Y-%m") for e in events if not e.all_day}
    assert months == {"2026-07", "2026-08"}


def test_ihc_concurrent_shows_are_not_merged(ihc):
    """IHC runs the same exhibition in two galleries at once. sub_venue is part
    of the event id so the second does not overwrite the first."""
    source, fetcher = ihc
    events = source.fetch(fetcher)

    illumination = [e for e in events if "Illumination" in e.title]
    assert len(illumination) == 2
    assert len({e.sub_venue for e in illumination}) == 2
    assert len({e.id for e in illumination}) == 2


def test_ihc_exhibition_date_range(ihc):
    source, fetcher = ihc
    events = source.fetch(fetcher)

    show = next(e for e in events if e.title == "Banaras Ghat In My Eyes")
    # The source publishes no year, so assert the day and month it does give.
    assert (show.start.month, show.start.day) == (7, 1)
    assert show.end is not None
    assert (show.end.month, show.end.day) == (7, 5)
    assert show.all_day
    assert show.format is Format.EXHIBITION
    assert show.sub_venue == "Convention Centre Foyer"


def test_alliance_francaise_reads_per_event_ical(alliance_francaise):
    source, fetcher = alliance_francaise
    events = source.fetch(fetcher)

    assert len(events) == 1
    event = events[0]
    assert event.title.startswith("Bastille Day French Film Screenings")
    assert event.start.strftime("%Y-%m-%d %H:%M") == "2026-07-16 18:30"
    assert event.end.strftime("%Y-%m-%d %H:%M") == "2026-07-30 20:00"
    assert event.format is Format.FILM
    assert Topic.CINEMA in event.topics
    # Pulled out of the "Venue:" line in the iCal DESCRIPTION.
    assert event.sub_venue == "M.L. Bhartia Auditorium"


def test_alliance_francaise_uses_both_discovery_paths(alliance_francaise):
    """Listing page and RSS feed truncate differently, so both are consulted."""
    source, fetcher = alliance_francaise
    source.fetch(fetcher)
    assert any("/events/feed/" in u for u in fetcher.requested)
    assert any(u.endswith("/events/") for u in fetcher.requested)


def test_goethe_parses_api_payload(goethe):
    source, fetcher = goethe
    events = source.fetch(fetcher)

    assert len(events) == 2

    film = next(e for e in events if "Sophie Scholl" in e.title)
    # The clock time lives in event_location_txt, not in any date field.
    assert film.start.strftime("%Y-%m-%d %H:%M") == "2026-08-04 19:00"
    assert not film.all_day
    assert film.format is Format.FILM
    assert film.source_url.endswith("event_id=27311559&fuseaction=events.detail")

    conference = next(e for e in events if "Teachers" in e.title)
    # No time published -> all-day, not midnight.
    assert conference.all_day
    assert conference.start.strftime("%Y-%m-%d") == "2026-08-01"
    assert conference.end.strftime("%Y-%m-%d") == "2026-08-02"


def test_every_adapter_returns_usable_events(iic, ihc, alliance_francaise, goethe):
    """A blanket sanity check: no adapter may emit an untitled or unvenued
    event, because both flow straight into the site and the calendar feed."""
    for source, fetcher in (iic, ihc, alliance_francaise, goethe):
        for event in source.fetch(fetcher):
            assert event.title.strip()
            assert event.venue.strip()
            assert event.start.tzinfo is not None
            assert event.id
            if event.end is not None:
                assert event.end >= event.start
