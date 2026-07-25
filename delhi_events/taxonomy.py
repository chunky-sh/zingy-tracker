"""Classification into a format (one) and topics (zero or more).

The two axes are deliberately independent. "Nature" is not a kind of event --
it shows up as a walk, a talk and a photography exhibition alike -- so filtering
by interest has to work across formats, and filtering by format has to work
across interests.

Everything here is deterministic keyword matching. ``llm.py`` fills gaps only
where these rules abstain.
"""

from __future__ import annotations

import re

from .models import Format, Topic

# Native category strings published by the venues themselves. When a source
# hands us one of these, trust it over any keyword guess.
CATEGORY_TO_FORMAT: dict[str, Format] = {
    "dance": Format.PERFORMANCE,
    "music": Format.PERFORMANCE,
    "theatre": Format.PERFORMANCE,
    "theater": Format.PERFORMANCE,
    "performance": Format.PERFORMANCE,
    "cultural": Format.PERFORMANCE,  # IIC's label for music and dance evenings
    "film": Format.FILM,
    "films": Format.FILM,
    "film club": Format.FILM,
    "film & theatre": Format.FILM,
    "film screening": Format.FILM,  # Goethe's own event_type label
    "screening": Format.FILM,
    "cinema": Format.FILM,
    "talk": Format.TALK,
    "talks": Format.TALK,
    "discussion": Format.TALK,
    "discussions": Format.TALK,
    "lecture": Format.TALK,
    "lectures": Format.TALK,
    "seminar": Format.TALK,
    "seminars": Format.TALK,
    "conference": Format.TALK,
    "conferences": Format.TALK,
    "library programmes": Format.TALK,
    "walk": Format.WALK,
    "walks": Format.WALK,
    "workshop": Format.WORKSHOP,
    "workshops": Format.WORKSHOP,
    "exhibition": Format.EXHIBITION,
    "exhibitions": Format.EXHIBITION,
    "art": Format.EXHIBITION,
    "festival": Format.FESTIVAL,
    "festivals": Format.FESTIVAL,
    "online": Format.OTHER,
    "webcast": Format.OTHER,
    "webcasts": Format.OTHER,
    "other": Format.OTHER,
}

# Some venue categories name a subject rather than a shape. IHC tagging a show
# "Theatre" tells us both that it is a performance and that it is about theatre,
# and that is a stronger signal than anything we could infer from a title like
# "Tragedy Mein Comedy".
CATEGORY_TO_TOPIC: dict[str, Topic] = {
    "dance": Topic.DANCE,
    "music": Topic.MUSIC,
    "theatre": Topic.THEATRE,
    "theater": Topic.THEATRE,
    "film": Topic.CINEMA,
    "cinema": Topic.CINEMA,
    "film & theatre": Topic.CINEMA,
    "art": Topic.ART,
    "exhibition": Topic.ART,
    "exhibitions": Topic.ART,
}

# "Dance" pins the subject; "Exhibition" only says it hangs on a wall. Topics
# from the latter are worth recording but must not mark the event as confidently
# classified, or a wildlife photography show stays tagged nothing but "art".
VAGUE_CATEGORIES = {"art", "exhibition", "exhibitions"}

# Ordered: first match wins, so the most specific patterns come first. A
# "printmaking workshop" is a workshop, not an exhibition, even though it will
# also match art keywords further down.
FORMAT_RULES: list[tuple[Format, str]] = [
    (Format.WORKSHOP, r"\bworkshop|masterclass|master class|hands[- ]on|residency|\bclinic\b"),
    (Format.WALK, r"\bwalks?\b|\bheritage tour|\bnature trail|\btrail\b|guided tour|walkthrough"),
    (Format.EXHIBITION, r"\bexhibitions?\b|\bretrospective\b|solo show|group show|\bon view\b|\bvernissage\b"),
    (Format.FESTIVAL, r"\bfestival|\butsav\b|\bsamaroh\b|\bsammelan\b|\bmahotsav\b|\bbiennale\b|\bmela\b"),
    (Format.FILM, r"\bscreening|\bfilm club\b|\bcin[eé]\b|\bdocumentary\b|\bfilms?\b"),
    (Format.TALK, r"\btalks?\b|\blectures?\b|\bdiscussions?\b|\bpanel\b|in conversation|\bseminar\b|"
                  r"\bsymposium\b|book launch|book discussion|\breadings?\b|\bkeynote\b"),
    (Format.PERFORMANCE, r"\bconcert\b|\brecital\b|\bplay\b|\bdance\b|\bmusic\b|\btheatre\b|"
                         r"\bperformance\b|\bbaithak\b|\bjugalbandi\b"),
]

TOPIC_RULES: dict[Topic, str] = {
    Topic.ART: r"\bart\b|\barts\b|artist|painting|sculpture|gallery|ceramic|printmaking|"
               r"\bdrawings?\b|\bcanvas\b|installation|miniature|calligraph|textile|craft",
    # "conservation" is deliberately absent: heritage and archaeology talks use
    # it constantly, and it dragged them all into nature. Only the qualified
    # forms count.
    Topic.NATURE: r"\bnature\b|ecolog|biodiversity|environment|\bforest|\btrees?\b|garden|"
                  r"wildlife|\briver\b|yamuna|climate|\bridge\b|sustainab|"
                  r"botanic|\bflora\b|\bfauna\b|butterfl|wetland|national park|"
                  # No bare "habitat": the India Habitat Centre is a venue name
                  # that appears in most Delhi listings.
                  r"sanctuary|\bsafari\b|\bspecies\b|natural habitat|"
                  r"(?:nature|wildlife|habitat|species|forest)\s+conservation",
    Topic.BIRDS: r"\bbirds?\b|birding|birdwatch|\bavian\b|ornitholog|\braptors?\b|migratory bird",
    Topic.HERITAGE: r"heritage|monument|\btombs?\b|\bforts?\b|stepwell|\bbaoli\b|mughal|"
                    r"archaeolog|restoration|\bhavelis?\b|built heritage|old delhi|shahjahanabad",
    Topic.SOCIOLOGY: r"sociolog|\bgender\b|\bcaste\b|\burban\b|communit|migration|\blabour\b|"
                     r"democracy|politic|ambedkar|\bjustice\b|feminis|\bpolicy\b|citizenship|"
                     r"inequality|\bsociety\b|development|\brights\b|\bactivis",
    Topic.CINEMA: r"\bfilms?\b|cinema|screening|documentar|\bdirector\b|filmmaker",
    Topic.MUSIC: r"\bmusic|\bconcert\b|\braga\b|khayal|thumri|sangeet|\bvocal\b|\bsitar\b|"
                 r"\btabla\b|choral|\bjazz\b|qawwali|\brecital\b|\bsufi\b|\bghazal\b",
    Topic.DANCE: r"\bdance|kathak|bharatanatyam|odissi|kuchipudi|\bnritya\b|\bballet\b|choreograph",
    Topic.THEATRE: r"\btheatre\b|\btheater\b|\bplays?\b|\bdrama\b|\bnatya\b|rangmanch|playwright",
    Topic.LITERATURE: r"\bbooks?\b|poetry|literature|\bwriters?\b|\bnovel\b|\bkavita\b|"
                      r"sahitya|\bpoets?\b|storytelling|\bfiction\b",
    Topic.PHOTOGRAPHY: r"photograph|\bphotos?\b|\blens\b|\bcamera\b",
    Topic.HISTORY: r"\bhistor|\barchive|medieval|colonial|\bancient\b|chronicle|\bempire\b",
    Topic.SCIENCE: r"\bscience|astronom|\bphysics\b|mathematic|technolog|\bresearch\b|"
                   r"artificial intelligence|\bgenetic|\bspace\b",
}

_FORMAT_PATTERNS = [(fmt, re.compile(pat, re.I)) for fmt, pat in FORMAT_RULES]
_TOPIC_PATTERNS = {topic: re.compile(pat, re.I) for topic, pat in TOPIC_RULES.items()}


# Venues prefix titles with their own category: "Exhibition- UNSUNG",
# "Film- Victoria", "Music : Fete de la Musique". The prefix is a useful
# classification hint but pure noise once the format is shown as a tag.
_TITLE_PREFIX_RE = re.compile(
    r"^\s*(exhibitions?|films?|talks?|workshops?|walks?|discussions?|"
    r"lectures?|seminars?|concerts?|festivals?|music|dance|theatre|theater)"
    r"\s*[-–—:|]\s*",
    re.I,
)


def split_title_prefix(title: str) -> tuple[str, str]:
    """Return (title without its category prefix, the prefix as a hint).

    Only strips when something substantial remains -- "Exhibition" alone as a
    title stays as it is.
    """
    match = _TITLE_PREFIX_RE.match(title)
    if not match:
        return title, ""
    remainder = title[match.end():].strip()
    if len(remainder) < 3:
        return title, ""
    return remainder, match.group(1).lower()


def format_from_category(category: str | None) -> Format | None:
    """Map a venue's own category label. Returns None if unrecognised."""
    if not category:
        return None
    return CATEGORY_TO_FORMAT.get(category.strip().lower())


def detect_format(text: str, category: str | None = None) -> tuple[Format, bool]:
    """Returns (format, confident). ``confident`` is False when nothing matched
    and we fell back to OTHER -- that is the signal for LLM assistance."""
    from_category = format_from_category(category)
    if from_category is not None and from_category is not Format.OTHER:
        return from_category, True

    for fmt, pattern in _FORMAT_PATTERNS:
        if pattern.search(text):
            return fmt, True

    if from_category is Format.OTHER:
        return Format.OTHER, True
    return Format.OTHER, False


# A title hit alone is enough; a description needs to keep coming back to the
# subject. Tuned against real listings: a film blurb that mentions "cultural
# heritage" once is not a heritage event, but one that says "bird" four times is
# a birding event even if the title is just a poetic phrase.
TITLE_WEIGHT = 3
DESCRIPTION_CAP = 3
TOPIC_THRESHOLD = 3
MAX_TOPICS = 4


def score_topics(title: str, description: str = "") -> dict[Topic, int]:
    scores: dict[Topic, int] = {}
    for topic, pattern in _TOPIC_PATTERNS.items():
        score = 0
        if pattern.search(title):
            score += TITLE_WEIGHT
        if description:
            score += min(len(pattern.findall(description)), DESCRIPTION_CAP)
        if score:
            scores[topic] = score
    return scores


def detect_topics(title: str, description: str = "") -> list[Topic]:
    scores = score_topics(title, description)
    ranked = sorted(
        (t for t, s in scores.items() if s >= TOPIC_THRESHOLD),
        key=lambda t: (-scores[t], t.value),
    )
    return ranked[:MAX_TOPICS]


# Topics implied by a format, applied only when keyword matching found nothing.
# Keeps a bare "Kathak recital" from landing with no topics at all.
_FORMAT_FALLBACK_TOPICS: dict[Format, list[Topic]] = {
    Format.EXHIBITION: [Topic.ART],
    Format.FILM: [Topic.CINEMA],
    Format.WALK: [Topic.HERITAGE],
}


def classify(title: str, description: str = "", category: str | None = None
             ) -> tuple[Format, list[Topic], bool]:
    """Classify an event. Returns (format, topics, confident).

    ``confident`` is False when we had to fall back on a format or topic guess;
    those are the events worth spending an LLM call on.
    """
    # Format is a single label, so a description mention is enough to settle it
    # -- but the title gets first look, since "Workshop" in a blurb about a past
    # workshop should not outrank a title that says "Exhibition".
    fmt, fmt_confident = detect_format(title, category)
    if not fmt_confident and description:
        fmt, fmt_confident = detect_format(f"{title} {description}", category)

    topics = detect_topics(title, description)
    topics_confident = bool(topics)

    category_key = (category or "").strip().lower()
    if (from_category := CATEGORY_TO_TOPIC.get(category_key)):
        if from_category not in topics:
            topics = [from_category, *topics][:MAX_TOPICS]
        if category_key not in VAGUE_CATEGORIES:
            topics_confident = True

    if not topics:
        topics = _FORMAT_FALLBACK_TOPICS.get(fmt, [])

    confident = fmt_confident and topics_confident
    return fmt, sorted(set(topics), key=lambda t: t.value), confident
