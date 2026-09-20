"""Classification rules -- the cases that were wrong at some point."""

from __future__ import annotations

import pytest

from delhi_events.models import Format, Topic
from delhi_events.taxonomy import classify, detect_topics, split_title_prefix


@pytest.mark.parametrize("title,description,category,expected", [
    ("Bird Walk at Sunder Nursery", "A morning walk to spot migratory birds.", None, Format.WALK),
    ("Printmaking Workshop for Beginners", "Learn etching.", None, Format.WORKSHOP),
    ("UNSUNG: Grandeur of Smallness", "", "Exhibitions", Format.EXHIBITION),
    ("The Democracy to Come", "", "Discussions", Format.TALK),
    ("Western Choral Music", "", "Music", Format.PERFORMANCE),
    ("Sophie Scholl", "", "Film Screening", Format.FILM),
    ("IHC Lok Sangeet Sammelan", "", None, Format.FESTIVAL),
])
def test_format_detection(title, description, category, expected):
    assert classify(title, description, category)[0] is expected


def test_plural_category_labels_are_recognised():
    """IIC labels its categories 'Talks' and 'Discussions', not the singular."""
    for label in ("Talks", "Discussions", "Seminars", "Exhibitions", "Films"):
        fmt, _, _ = classify("Some Event", "", label)
        assert fmt is not Format.OTHER, label


def test_venue_name_does_not_leak_into_topics():
    """'India Habitat Centre' appears in most Delhi listings -- a bare 'habitat'
    keyword tagged every one of them as nature."""
    topics = detect_topics(
        "Piano Recital",
        "Presented at the India Habitat Centre. Tickets at the Habitat Programmes desk.",
    )
    assert Topic.NATURE not in topics


def test_conservation_alone_is_not_a_nature_topic():
    """Heritage and archaeology talks use 'conservation' constantly."""
    topics = detect_topics(
        "Indian Archaeology",
        "A lecture on the conservation of monuments and the restoration of temple sites.",
    )
    assert Topic.NATURE not in topics
    assert Topic.HERITAGE in topics


def test_qualified_conservation_still_counts_as_nature():
    topics = detect_topics(
        "Saving the Ridge",
        "On wildlife conservation, biodiversity and the future of the forest.",
    )
    assert Topic.NATURE in topics


def test_passing_mention_does_not_create_a_topic():
    """A film blurb that says 'cultural heritage' once is not a heritage event."""
    topics = detect_topics(
        "Bastille Day French Film Screenings",
        "A selection of iconic French films, celebrating France's cultural heritage "
        "on the big screen. Stories of ambition, resilience and history.",
    )
    assert Topic.CINEMA in topics
    assert Topic.HERITAGE not in topics
    assert Topic.HISTORY not in topics


def test_repeated_mention_does_create_a_topic():
    topics = detect_topics(
        "Morning at the Wetland",
        "Birds everywhere. We count birds, photograph birds, and log every bird seen.",
    )
    assert Topic.BIRDS in topics


def test_format_and_topic_are_independent():
    """The whole point of two axes: nature spans walks, talks and exhibitions."""
    for title, expected_format in [
        ("Nature Walk in the Ridge Forest", Format.WALK),
        ("A Talk on the Ridge Forest and its Biodiversity", Format.TALK),
        ("Photographs of the Ridge Forest: an Exhibition", Format.EXHIBITION),
    ]:
        fmt, topics, _ = classify(title, "")
        assert fmt is expected_format
        assert Topic.NATURE in topics


@pytest.mark.parametrize("raw,expected_title,expected_hint", [
    ("Exhibition- UNSUNG: Grandeur", "UNSUNG: Grandeur", "exhibition"),
    ("Film- Victoria", "Victoria", "film"),
    ("Music : Fete de la Musique", "Fete de la Musique", "music"),
    ("BOOK DISCUSSION GROUP -Speaking Sculptures", "BOOK DISCUSSION GROUP -Speaking Sculptures", ""),
    ("Exhibition", "Exhibition", ""),
])
def test_title_prefix_stripping(raw, expected_title, expected_hint):
    title, hint = split_title_prefix(raw)
    assert title == expected_title
    assert hint == expected_hint


def test_vague_category_does_not_suppress_further_tagging():
    """'Exhibition' says nothing about subject, so a wildlife photo show must
    stay low-confidence and reach the LLM tagger."""
    _, _, confident = classify("India's Pride - The Asiatic Lions", "", "Exhibition")
    assert confident is False


def test_specific_category_is_confident():
    _, topics, confident = classify("Tragedy Mein Comedy", "", "Theatre")
    assert confident is True
    assert Topic.THEATRE in topics


# -- placeholder titles ----------------------------------------------------

@pytest.mark.parametrize("title", [
    "Loading...", "loading", "TBD", "TBA", "To be announced", "Untitled",
    "Coming soon", "test", "XXXX", "placeholder", "  ", "-", "N/A",
])
def test_cms_placeholders_are_rejected(title):
    """Goethe's API serves unpublished drafts with isDisplayed=true and a
    literal "Loading..." headline. Publishing one is worse than dropping it:
    ids derive from the title, so the placeholder would linger as a ghost
    record once the real name lands."""
    from delhi_events.models import is_placeholder_title
    assert is_placeholder_title(title)


@pytest.mark.parametrize("title", [
    "Sophie Scholl - The Final Days",
    "Loading the Frame: Photographs of Delhi",   # 'loading' as a real word
    "Test Match: A History of Cricket",
    "TBA Collective in Concert",
])
def test_real_titles_are_not_mistaken_for_placeholders(title):
    from delhi_events.models import is_placeholder_title
    assert not is_placeholder_title(title)


def test_format_follows_the_earliest_mention_not_the_rule_order():
    """A listing says what the event is up front and drops incidental mentions
    later. A KNMA panel discussion was filed as a festival because a speaker's
    bio mentioned "the Mumbai International Film Festival" and the festival
    rule happens to be checked before the talk rule."""
    fmt, _, _ = classify(
        "The Language of Belonging",
        "As part of KNMA's celebration of International Week of Deaf People, "
        "this panel brings together diverse Deaf perspectives. Following the "
        "panel, a screening of a documentary by a director who received a "
        "Special Mention at the Mumbai International Film Festival.",
    )
    assert fmt is Format.TALK


def test_rule_order_still_settles_a_tie():
    """Two rules matching at the same offset must resolve the way they always
    did -- the position rule is a tie-break refinement, not a reordering."""
    fmt, _, _ = classify("Workshop", "")
    assert fmt is Format.WORKSHOP


def test_a_title_still_outranks_the_description():
    """An exhibition whose blurb mentions an accompanying talk is an
    exhibition; the title gets looked at on its own first."""
    fmt, _, _ = classify(
        "Exhibition: Nocturnes",
        "A panel discussion and lecture accompany the show on its opening day.",
    )
    assert fmt is Format.EXHIBITION
