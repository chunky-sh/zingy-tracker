"""Event identity, deduplication, and the published artefacts."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from icalendar import Calendar

from delhi_events import build as build_mod
from delhi_events import db
from delhi_events.llm import date_is_supported
from delhi_events.models import IST, Event, Format, Topic


def make_event(**overrides) -> Event:
    base = dict(
        source_id="test",
        title="A Talk on Yamuna Ecology",
        start=datetime(2099, 3, 4, 18, 30),
        venue="India International Centre",
        format=Format.TALK,
        topics=[Topic.NATURE],
    )
    base.update(overrides)
    return Event(**base)


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    yield connection
    connection.close()


# -- identity --------------------------------------------------------------

def test_id_survives_url_and_description_changes():
    a = make_event(source_url="https://old.example/1", description="draft")
    b = make_event(source_url="https://new.example/9", description="final copy")
    assert a.id == b.id
    assert a.content_hash != b.content_hash


def test_id_distinguishes_concurrent_shows_in_different_rooms():
    a = make_event(title="Illumination", sub_venue="Visual Arts Gallery")
    b = make_event(title="Illumination", sub_venue="Open Palm Court Gallery")
    assert a.id != b.id


def test_naive_datetimes_are_treated_as_ist():
    """A datetime that reaches the store without a timezone would shift by
    5h30m in the ICS export."""
    event = make_event(start=datetime(2099, 3, 4, 18, 30))
    assert event.start.utcoffset().total_seconds() == 5.5 * 3600


def test_end_before_start_is_read_as_crossing_midnight():
    event = make_event(start=datetime(2099, 3, 4, 22, 0), end=datetime(2099, 3, 4, 1, 0))
    assert event.end.day == 5


def test_absurd_end_is_dropped_rather_than_shifted():
    event = make_event(start=datetime(2099, 3, 4, 18, 0), end=datetime(2098, 1, 1, 9, 0))
    assert event.end is None


# -- store -----------------------------------------------------------------

def test_upsert_reports_new_then_unchanged_then_updated(conn):
    event = make_event()
    assert db.upsert(conn, event) == "new"
    assert db.upsert(conn, event) == "unchanged"
    assert db.upsert(conn, make_event(description="now with a blurb")) == "updated"


def test_near_identical_titles_are_merged(conn):
    db.upsert(conn, make_event(title="Flux of Being", source_id="a"))
    outcome = db.upsert(conn, make_event(title="Exhibition- Flux of Being", source_id="b"))
    assert outcome == "duplicate"
    assert len(db.active_events(conn, include_past=True)) == 1


def test_different_rooms_are_not_merged(conn):
    db.upsert(conn, make_event(title="Illumination", sub_venue="Visual Arts Gallery"))
    outcome = db.upsert(conn, make_event(title="Illumination", sub_venue="Open Palm Court"))
    assert outcome == "new"
    assert len(db.active_events(conn, include_past=True)) == 2


def test_reconcile_flags_only_future_events(conn):
    past = make_event(title="Already Happened", start=datetime.now(IST) - timedelta(days=10))
    future = make_event(title="Still Listed", start=datetime.now(IST) + timedelta(days=10))
    db.upsert(conn, past)
    db.upsert(conn, future)

    gone = db.reconcile(conn, "test", seen_ids=set())
    assert gone == 1  # the past event is left alone; it simply happened

    remaining = {e.title for e in db.active_events(conn, include_past=True)}
    assert "Already Happened" in remaining
    assert "Still Listed" not in remaining


def test_multi_day_show_stays_active_until_its_last_day(conn):
    now = datetime.now(IST)
    db.upsert(conn, make_event(
        title="Ongoing Exhibition",
        start=now - timedelta(days=3),
        end=now + timedelta(days=3),
        all_day=True,
    ))
    assert [e.title for e in db.active_events(conn)] == ["Ongoing Exhibition"]


# -- build -----------------------------------------------------------------

def test_ics_is_parseable_and_keeps_ist(conn):
    # An explicit clock time, not now() -- the point is that 18:30 IST survives
    # the round trip through UTC in the feed.
    future = (datetime.now(IST) + timedelta(days=5)).replace(hour=18, minute=30, second=0)
    db.upsert(conn, make_event(start=future))
    events = db.active_events(conn)
    calendar = Calendar.from_ical(build_mod.build_ics(events, "test", "Test"))

    vevents = list(calendar.walk("VEVENT"))
    assert len(vevents) == 1
    assert vevents[0]["DTSTART"].dt.astimezone(IST).hour == 18


def test_all_day_ics_uses_exclusive_end_date(conn):
    """RFC 5545 DTEND is exclusive, so a show ending on the 30th must write the
    31st or calendar clients drop the final day."""
    start = datetime.now(IST) + timedelta(days=2)
    db.upsert(conn, make_event(start=start, end=start + timedelta(days=4), all_day=True))

    vevent = list(Calendar.from_ical(
        build_mod.build_ics(db.active_events(conn), "t", "T")
    ).walk("VEVENT"))[0]

    assert (vevent["DTEND"].dt - vevent["DTSTART"].dt).days == 5


def test_ics_lines_respect_the_75_octet_limit(conn):
    """Folding is done on encoded bytes -- Delhi listings contain Devanagari."""
    db.upsert(conn, make_event(
        title="झड़पें / Skirmishes " + "अनुवाद " * 30,
        start=datetime.now(IST) + timedelta(days=1),
    ))
    raw = build_mod.build_ics(db.active_events(conn), "t", "T").encode("utf-8")

    assert all(len(line) <= 75 for line in raw.split(b"\r\n"))
    assert list(Calendar.from_ical(raw).walk("VEVENT"))


def test_build_writes_site_json_and_feeds(conn, tmp_path):
    db.upsert(conn, make_event(start=datetime.now(IST) + timedelta(days=1)))
    counts = build_mod.build(conn, tmp_path)

    assert counts["events"] == 1
    assert counts["nature.ics"] == 1  # the topic feed picked it up
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "events.json").exists()

    html = (tmp_path / "index.html").read_text()
    assert "A Talk on Yamuna Ecology" in html


def test_embedded_json_cannot_close_the_script_tag(conn, tmp_path):
    db.upsert(conn, make_event(
        title="Hack </script><script>alert(1)</script>",
        start=datetime.now(IST) + timedelta(days=1),
    ))
    build_mod.build(conn, tmp_path)
    html = (tmp_path / "index.html").read_text()

    assert "</script><script>alert(1)" not in html
    assert "\\u003c/script" in html


# -- llm guardrail ---------------------------------------------------------

@pytest.mark.parametrize("date_str,source,expected", [
    ("2026-08-15", "Walk on 15th August 2026", True),
    ("2026-08-15", "Walk on 15 Aug", True),
    ("2026-08-22", "Walk on 15th August 2026", False),   # day absent
    ("2026-07-15", "Walk on 15th August 2026", False),   # month absent
    ("2026-09-03", "Also 3 Sep at the gallery", True),
    ("not-a-date", "anything", False),
])
def test_llm_dates_must_appear_in_the_source(date_str, source, expected):
    assert date_is_supported(date_str, source) is expected


# -- cli list grouping ------------------------------------------------------

def test_list_separates_already_running_from_upcoming(conn, capsys):
    """An exhibition that opened a fortnight ago is still on, but printing it
    under its opening date put a past heading inside a "next N days" query."""
    import argparse

    from delhi_events.cli import cmd_list

    now = datetime.now(IST)
    db.upsert(conn, make_event(
        title="Opened Earlier", start=now - timedelta(days=14),
        end=now + timedelta(days=3), all_day=True,
    ))
    db.upsert(conn, make_event(title="Starts Tomorrow", start=now + timedelta(days=1)))
    conn.commit()

    args = argparse.Namespace(db=conn.execute("PRAGMA database_list").fetchone()[2],
                              days=5, topic=None, format=None, venue=None)
    cmd_list(args)
    out = capsys.readouterr().out

    assert "Already running" in out
    assert "Opened Earlier" in out
    assert "Starts Tomorrow" in out
    # The opening date is a fortnight back; it must not appear as a heading.
    stale = (now - timedelta(days=14)).strftime("%A %d %B")
    assert stale not in out


def test_description_does_not_repeat_the_title():
    """Venues open the blurb by restating the event name, so it rendered twice
    -- once as the heading, once as the first line beneath it. Stripped at the
    model so the ICS feeds benefit too, not just the page."""
    event = make_event(
        title="3rd All India Conference of East Asian Studies",
        description="3rd All India Conference of East Asian Studies\nKeynote by Prof. Kimura.",
    )
    assert event.description == "Keynote by Prof. Kimura."


def test_a_merely_similar_first_line_is_kept():
    event = make_event(
        title="Flux of Being",
        description="Flux of Being and Becoming: new work by six artists.",
    )
    assert event.description.startswith("Flux of Being and Becoming")


# -- card summaries ---------------------------------------------------------

def test_summary_prefers_the_lead_line_over_a_flattened_blurb():
    """Venues put the lead on its own line. Flattening first ran a subtitle
    straight into the next sentence: '...East Asia Keynote Address by Prof.'"""
    from delhi_events.build import summarise
    out = summarise(
        "Dissonances in the World Order: Implications for Indo-Pacific & East Asia\n"
        "Keynote Address by Prof. Kan Kimura, Kobe University",
        "3rd All India Conference of East Asian Studies",
    )
    assert out == "Dissonances in the World Order: Implications for Indo-Pacific & East Asia"


def test_summary_skips_a_line_that_restates_the_title():
    """A summary echoing the heading directly above it tells you nothing."""
    from delhi_events.build import summarise
    out = summarise(
        "Book Discussion Group Stories Carved in Stone\nBy Nishi Chawla, 2026.",
        "BOOK DISCUSSION GROUP- Stories Carved in Stone",
    )
    assert out == "By Nishi Chawla, 2026."


def test_summary_does_not_truncate_at_an_abbreviation():
    from delhi_events.build import summarise
    out = summarise("Keynote by Prof. Kan Kimura of Kobe University. " + "Filler. " * 40)
    assert out.startswith("Keynote by Prof. Kan Kimura")


def test_summary_falls_back_to_a_word_boundary():
    from delhi_events.build import summarise
    out = summarise("A photography exhibition " + "about many overlooked things " * 20)
    assert out.endswith("…")
    assert len(out) <= 181
    assert not out.rstrip("…").endswith(" ")


def test_summary_is_empty_when_there_is_nothing_to_say():
    from delhi_events.build import summarise
    assert summarise("") == ""
    assert summarise("   \n  ") == ""
    # A description that is only a restatement of the title leaves nothing.
    assert summarise("Flux of Being", "Flux of Being") == ""


def test_summary_reaches_events_json(conn, tmp_path):
    db.upsert(conn, make_event(
        start=datetime.now(IST) + timedelta(days=1),
        description="A guided walk along the Yamuna. Meets at the gate.",
    ))
    build_mod.build(conn, tmp_path)
    import json
    payload = json.loads((tmp_path / "events.json").read_text())
    assert payload["events"][0]["summary"].startswith("A guided walk along the Yamuna")
