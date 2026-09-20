"""Golden-file tests for each adapter.

These exist to catch the failure mode that matters: a venue redesigns its site,
the selectors stop matching, and the adapter quietly returns nothing. A test
that asserts real field values fails loudly instead.

Refresh the fixtures with `make fixtures` when a site legitimately changes.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from delhi_events.models import IST, Format, Topic


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


def test_every_adapter_returns_usable_events(iic, ihc, alliance_francaise, goethe,
                                             bikaner_house):
    """A blanket sanity check: no adapter may emit an untitled or unvenued
    event, because both flow straight into the site and the calendar feed."""
    for source, fetcher in (iic, ihc, alliance_francaise, goethe, bikaner_house):
        for event in source.fetch(fetcher):
            assert event.title.strip()
            assert event.venue.strip()
            assert event.start.tzinfo is not None
            assert event.id
            if event.end is not None:
                assert event.end >= event.start


# -- Bikaner House ----------------------------------------------------------

def _pin_today(monkeypatch, when: datetime) -> None:
    """Freeze the adapter's notion of today so the month walk lands on the two
    months we have golden files for."""
    from delhi_events.sources import bikaner_house
    monkeypatch.setattr(bikaner_house, "_today", lambda: when.replace(tzinfo=IST))


def test_bikaner_house_walks_forward_past_the_current_month(bikaner_house, monkeypatch):
    """Each show is listed only under the month it opens in, so a one-month
    fetch gives no notice at all of an exhibition opening on the 1st."""
    source, fetcher = bikaner_house
    _pin_today(monkeypatch, datetime(2026, 9, 15))
    events = source.fetch(fetcher)

    assert any("/2026/9" in url for url in fetcher.requested)
    assert any("/2026/10" in url for url in fetcher.requested)

    october_only = next(e for e in events if e.title == "Nar Nari 2.0")
    assert october_only.start.strftime("%Y-%m-%d") == "2026-10-01"
    assert october_only.end.strftime("%Y-%m-%d") == "2026-10-06"


def test_bikaner_house_parses_a_gallery_show(bikaner_house, monkeypatch):
    source, fetcher = bikaner_house
    _pin_today(monkeypatch, datetime(2026, 9, 15))
    events = source.fetch(fetcher)

    show = next(e for e in events if e.title == "A Moment in the Monumental")
    assert show.start.strftime("%Y-%m-%d") == "2026-09-27"
    assert show.end.strftime("%Y-%m-%d") == "2026-10-06"
    assert show.all_day
    assert show.venue == "Bikaner House"
    assert show.format is Format.EXHIBITION
    assert show.description.startswith("Art Alive Gallery")
    # The date line lives in the title box; letting it into the blurb would put
    # "27th September 2026 - 06th October 2026" at the head of the card summary.
    assert "27th September 2026" not in show.description


def test_bikaner_house_does_not_repeat_a_show_across_month_pages(bikaner_house, monkeypatch):
    """The walk re-requests a page whenever the routing repeats a month; the
    same show must not land in the store twice."""
    source, fetcher = bikaner_house
    _pin_today(monkeypatch, datetime(2026, 9, 15))
    ids = [e.id for e in source.fetch(fetcher)]
    assert len(ids) == len(set(ids))


def test_bikaner_house_raises_when_the_current_month_is_unreadable(monkeypatch):
    """Convention: an adapter raises on genuine failure. Returning the empty
    list would read to `doctor` as "the venue has nothing on"."""
    from delhi_events.sources.base import SourceConfig, load_source

    class DeadFetcher:
        def get(self, url, *, referer=None, retries=3):
            raise RuntimeError("could not fetch")

    source = load_source(SourceConfig(
        id="bikaner_house", adapter="bikaner_house",
        name="Bikaner House", url="https://example.test/",
    ))
    with pytest.raises(RuntimeError):
        source.fetch(DeadFetcher())


def test_bikaner_house_tolerates_a_missing_later_month(bikaner_house, monkeypatch):
    """A month page that 404s because nothing is booked yet is normal and must
    not sink the months that did load."""
    source, fetcher = bikaner_house
    _pin_today(monkeypatch, datetime(2026, 9, 15))

    real_get = fetcher.get

    def flaky(url, *, referer=None, retries=3):
        if "/2026/11" in url or "/2026/12" in url:
            raise RuntimeError("404")
        return real_get(url, referer=referer, retries=retries)

    fetcher.get = flaky
    assert source.fetch(fetcher)


@pytest.mark.parametrize("text,expected", [
    ("27th September 2026 - 06th October 2026", ("2026-09-27", "2026-10-06")),
    ("15th August 2026", ("2026-08-15", None)),          # one-day event
    ("06th October 2026 - 27th September 2026", ("2026-10-06", None)),  # reversed
    ("31st September 2026", (None, None)),               # impossible date
    ("coming soon", (None, None)),
])
def test_bikaner_house_date_ranges(text, expected):
    from delhi_events.sources.bikaner_house import _parse_range
    start, end = _parse_range(text)
    fmt = lambda d: d.strftime("%Y-%m-%d") if d else None
    assert (fmt(start), fmt(end)) == expected


# -- galleries (shared selector-driven adapter) -----------------------------

def _pin_gallery_today(monkeypatch, when: date) -> None:
    from delhi_events.sources import gallery
    monkeypatch.setattr(gallery, "_today", lambda: when)


def test_knma_parses_its_own_shows(knma, monkeypatch):
    source, fetcher = knma
    _pin_gallery_today(monkeypatch, date(2026, 9, 20))
    events = source.fetch(fetcher)

    show = next(e for e in events if e.title == "In-Rhythm")
    assert show.start.strftime("%Y-%m-%d") == "2026-09-09"
    # The date line ends with opening hours -- "09 Sep 2026 — 22 Dec 2026
    # 11:00 am - 8:00 pm" -- which must not be read as the closing date.
    assert show.end.strftime("%Y-%m-%d") == "2026-12-22"
    assert show.all_day
    assert show.format is Format.EXHIBITION
    assert show.source_url.startswith("https://www.knma.org/whats-on/")


def test_knma_drops_shows_that_are_not_in_delhi(knma, monkeypatch):
    """KNMA lists shows its collection travels to on the same page as its own
    programme; a Delhi tracker should not carry a show at MoMA."""
    source, fetcher = knma
    _pin_gallery_today(monkeypatch, date(2026, 9, 20))
    events = source.fetch(fetcher)

    assert events, "expected KNMA's own shows to survive the allowlist"
    for event in events:
        assert any(k in event.sub_venue for k in ("KNMA", "Saket", "Noida")), event.sub_venue
    assert not any("Museum of Modern Art" in e.sub_venue for e in events)


def test_gallery_filters_out_finished_shows(knma, monkeypatch):
    """The listing carries the archive on the same page as the current shows."""
    source, fetcher = knma
    _pin_gallery_today(monkeypatch, date(2027, 12, 31))
    assert source.fetch(fetcher) == []


def test_latitude_28_reads_split_start_and_end_elements(latitude_28, monkeypatch):
    """This one publishes the two ends in separate spans, with no range text to
    parse -- reading the card's text instead gives "Aug 21, 2026 Sep 19, 2026"
    with no separator, and the end date is silently lost."""
    source, fetcher = latitude_28
    _pin_gallery_today(monkeypatch, date(2026, 9, 1))
    events = source.fetch(fetcher)

    show = next(e for e in events if e.title == "Between Dust and Distant Sky")
    assert show.start.strftime("%Y-%m-%d") == "2026-08-21"
    assert show.end.strftime("%Y-%m-%d") == "2026-09-19"
    # src is a blank SVG placeholder; the real file is in data-src.
    assert show.image_url.startswith("https://")
    assert not show.image_url.startswith("data:")


def test_gallery_raises_when_the_card_selector_stops_matching(monkeypatch):
    """A redesign that breaks the selector must not read as "nothing on" --
    every gallery entry sets allow_empty, so `doctor` would say nothing."""
    from delhi_events.sources.base import SourceConfig, load_source

    class EmptyPage:
        def get(self, url, *, referer=None, retries=3):
            return "<html><body><p>Coming soon</p></body></html>"

    source = load_source(SourceConfig(
        id="test_gallery", adapter="gallery", name="Test Gallery",
        url="https://example.test/", options={"card": ".exhibition", "title": "h2"},
    ))
    with pytest.raises(RuntimeError, match="no cards matched"):
        source.fetch(EmptyPage())


def test_every_configured_gallery_has_the_selectors_it_needs():
    """The selectors are the whole integration, so a config entry missing one
    is a broken source that would only show up on the next live refresh."""
    from delhi_events.sources.base import load_configs

    galleries = [c for c in load_configs() if c.adapter == "gallery"]
    assert galleries, "expected gallery sources to be configured"
    for config in galleries:
        assert config.options.get("card"), f"{config.id}: no card selector"
        assert config.options.get("title"), f"{config.id}: no title selector"
        # Empty is a normal state for a gallery between shows, and the adapter
        # raises rather than returning [] when a selector actually breaks.
        assert config.allow_empty, f"{config.id}: gallery should allow_empty"


def test_knma_takes_the_blurb_from_the_show_s_own_page(knma, monkeypatch):
    """The listing card carries a blurb for some shows and nothing at all for
    others, so cards on the site were a heading with no sense of what the show
    was. The writing lives on the detail page."""
    source, fetcher = knma
    _pin_gallery_today(monkeypatch, date(2026, 9, 20))
    events = source.fetch(fetcher)

    show = next(e for e in events if e.title == "Inheritors of Earth")
    assert "curated by Premjish Achari" in show.description
    assert any("/whats-on/exhibitions/" in url for url in fetcher.requested)


def test_knma_drops_the_house_promo_from_the_blurb(knma, monkeypatch):
    """Every KNMA page signs off "While you're here, explore KNMA's two ongoing
    exhibitions...", which padded the summary and -- worse -- handed the
    classifier the words "exhibitions" and "screenings" for a book talk."""
    source, fetcher = knma
    _pin_gallery_today(monkeypatch, date(2026, 9, 20))

    for event in source.fetch(fetcher):
        assert "While you" not in event.description
        assert "two ongoing exhibitions" not in event.description


def test_gallery_does_not_refetch_a_blurb_it_already_has(monkeypatch):
    """The detail fetch is per current show, so it must be skipped whenever the
    listing already said enough -- otherwise every refresh opens a page a
    venue never needed us to."""
    from delhi_events.sources.base import SourceConfig, load_source

    listing = (
        '<div class="card"><h2>A Show</h2><span class="d">5 - 30 October 2026</span>'
        '<p class="blurb">' + "A full paragraph of real description. " * 4 + "</p>"
        '<a href="/shows/a-show"></a></div>'
    )

    class CountingFetcher:
        def __init__(self): self.urls = []
        def get(self, url, *, referer=None, retries=3):
            self.urls.append(url)
            return listing

    source = load_source(SourceConfig(
        id="test_gallery", adapter="gallery", name="Test Gallery",
        url="https://example.test/shows", options={
            "card": ".card", "title": "h2", "dates": ".d",
            "description": ".blurb", "detail_description": ".body",
        },
    ))
    from delhi_events.sources import gallery
    monkeypatch.setattr(gallery, "_today", lambda: date(2026, 10, 1))

    fetcher = CountingFetcher()
    assert source.fetch(fetcher)
    assert fetcher.urls == ["https://example.test/shows"]
