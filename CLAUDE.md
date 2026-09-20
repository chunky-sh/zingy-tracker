# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A scraper + static-site generator for Delhi's cultural venues -- four cultural centres (IIC,
India Habitat Centre, Alliance Française, Goethe/Max Mueller Bhavan), two nature-walk organisers
(BNHS, Sunder Nursery), Bikaner House, and the commercial gallery circuit (KNMA, Nature Morte,
Vadehra, Shrine Empire, Latitude 28, Gallery Espace, Exhibit 320). It pulls each venue's
programme into SQLite (`data/events.db`) and publishes a filterable page plus four subscribable
`.ics` feeds into `site/dist/`. README calls the project "zingy-tracker"; the package is
`delhi_events`.

## Commands

The project is run from its checkout, never installed — always go through the venv interpreter.

```sh
make install                 # create .venv, install requirements + pytest/anthropic/pypdf
make refresh                 # scrape all enabled sources into data/events.db
make build                   # write site/dist (index.html, events.json, *.ics)
make dev                     # live site on :8000, rebuilds + reloads on change
make test                    # golden-file suite, no network (95 tests, <1s)
make doctor                  # detect silently broken parsers
make list                    # upcoming events in the terminal
make fixtures                # re-capture tests/fixtures/ from the live sites

PORT=8001 make dev           # different port
make dev REFRESH=30          # also re-scrape venues every 30 minutes
make refresh-llm             # refresh + Claude tagging (needs ANTHROPIC_API_KEY)
```

Direct CLI (same thing, more flags):

```sh
.venv/bin/python -m delhi_events.cli refresh --source iic --cache-ttl 3600
.venv/bin/python -m delhi_events.cli list --topic nature --days 14 --format walk
.venv/bin/python -m delhi_events.cli add-manual --venue "Some Gallery" < blurb.txt
```

`--cache-ttl N` reuses on-disk responses in `.cache/` for N seconds — use it while iterating on
an adapter so you are not hammering a venue's site.

Running tests:

```sh
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m pytest tests/test_sources.py -q                          # one file
.venv/bin/python -m pytest tests/test_sources.py::test_iic_parses_listing -q # one test
```

`pytest.ini` sets `pythonpath = .` so the bare `pytest` script and `python -m pytest` behave the
same (this differed between local and CI before).

## Architecture

Data flows one way: **adapter → `Event` → store → build**. Each stage speaks only `Event`.

- `delhi_events/sources/` — one module per venue, each exposing `Source(BaseSource)` with
  `fetch(fetcher) -> list[Event]`. Adapters never touch the DB. `base.py` holds `SourceConfig`
  and loads adapters by name from `config/sources.yaml`. `gallery.py` is the exception to
  "one module per venue": it is driven entirely by CSS selectors in `options`, so the seven
  commercial galleries share it and adding an eighth is a config entry rather than a module.
- `delhi_events/daterange.py` — `parse_range`, which reads the date line galleries print above
  a show. Every gallery abbreviates differently ("10 Oct - 12 Dec 2026", "10 - 25 October 2026",
  "September 5 - October 10, 2026"), so each side is parsed for whatever parts it carries and
  the left is backfilled from the right. It also skips non-date fragments, because KNMA runs the
  opening hours onto the same line.
- `delhi_events/models.py` — the `Event` pydantic model, the `Format`/`Topic`/`Status` enums,
  and the normalisation everything depends on. Naive datetimes are coerced to IST here (times
  are IST throughout the project). `Event.id` is a hash of
  `source_id | slug(title) | start date | slug(sub_venue) | HH:MM (unless all_day)` — stable
  across URL/description/image churn, and deliberately *not* derived from `source_url`.
  `content_hash` covers user-visible fields so a re-scrape can be told from a genuine edit.
- `delhi_events/db.py` — SQLite store. `upsert` returns `new`/`updated`/`unchanged`/`duplicate`;
  `find_duplicate` merges near-identical titles on the same day at the same venue (Jaccard over
  content words after stripping a category prefix) but keeps different `sub_venue`s and different
  start times apart. `reconcile` flags only *future* events that vanished from a listing.
- `delhi_events/fetch.py` — `Fetcher`: one session per run, retries, a politeness delay, an
  optional on-disk cache, and `warm()` for the session-cookie dance `indiahabitat.org` requires.
- `delhi_events/taxonomy.py` — deterministic keyword classification into one `format` and zero or
  more `topics`, trusting the venue's own category label when it publishes one. The two axes are
  independent on purpose: "nature" is a walk, a talk *and* a photo show.
- `delhi_events/llm.py` — optional Claude tagging/extraction, only where the rules abstain.
  Model via `DELHI_EVENTS_MODEL` (default `claude-haiku-4-5`), needs `ANTHROPIC_API_KEY`,
  responses cached by content hash under `.cache/llm/`. Any extracted date whose day+month do not
  appear in the source text is rejected — date hallucination would quietly poison the calendar.
  `anthropic` is imported lazily so the base install stays optional.
- `delhi_events/refresh.py` — runs adapters, upserts, reconciles, records a row in the `runs`
  table. One failing venue never sinks the whole run.
- `delhi_events/build.py` — `events.json`, the four ICS feeds (`FEEDS` dict), and the rendered
  `site/template.html`. Event data is embedded inline in the page (with `<` escaped) so the site
  works off the filesystem; multi-day shows are repeated on each day they run, capped at 120 days.
- `delhi_events/cli.py` — `refresh | build | list | doctor | add-manual`.

`site/template.html` is the entire front end; filters are vanilla JS over the embedded payload.

## Conventions that are easy to get wrong

**An adapter must raise on failure, never return `[]`.** An empty list is how `cli doctor`
detects a parser that broke silently after a site redesign, so it has to mean "the venue has
nothing on", not "I crashed". Sources where empty is a normal state (BNHS lists nationally and is
usually all-Mumbai) set `allow_empty: true` in `config/sources.yaml`; `doctor` then reports them
as a note rather than a failure. `doctor` also flags a source whose count dropped below
`DROP_RATIO` (0.5) of its recent baseline as a possible partial parse.

**`config/recurring.yaml` declares events rather than scraping them.** Sunder Nursery publishes
no dated listings, so its standing weekend walk is generated from a declared schedule. Three
guards keep that honest and must be preserved when editing:
a `marker` string re-checked on the venue page each run, a `confirmed_until` date past which
nothing is generated (a test in `tests/test_nature_sources.py` fails once it lapses — re-confirm
by phone, then push the date forward), and a `caveat` with the venue's phone number on every
generated event.

**Galleries keep the writing on the detail page.** Several print only a title and dates on the
listing (KNMA shows nothing at all for some), so `detail_description` opens each *current* show's
own page — after the date filter, so KNMA costs 6 fetches rather than 33, capped by `detail_limit`
and skipped when the listing blurb is already substantial. `detail_stop` cuts the house promo off
the end; leaving KNMA's "While you're here, explore KNMA's two ongoing exhibitions…" in place
padded every summary and filed a book talk as an exhibition. Format and topics are therefore
classified *after* the description is final, and a venue whose listing mixes shows with talks and
workshops sets `format: auto` instead of declaring one.

**`detect_format` takes the earliest match, not the first rule.** A listing states what the event
is up front and drops incidental mentions later, so position beats rule order; rule order only
breaks ties. Without this a panel discussion was filed as a festival because a speaker bio
mentioned the Mumbai International Film Festival.

**The `gallery` adapter's two empty results mean opposite things.** No card matching the `card`
selector means the page changed shape, so it *raises*; cards that match but have all finished is
a gallery between shows, so it returns `[]`. That distinction is what makes `allow_empty: true`
safe on every gallery entry -- without it, a redesign would read as "nothing on" and `doctor`
would stay quiet. Galleries also list shows their artists appear in elsewhere (KNMA carries MoMA
and the V&A), so `sub_venue_allow` keeps a Delhi tracker about Delhi.

**Never advertise a Content-Encoding we cannot decode.** `fetch.py` builds `Accept-Encoding` from
the codecs actually importable rather than hard-coding `br`. Offering Brotli without the `brotli`
package made two galleries answer in binary, which parsed as a page with no events on it — a
silent wrong answer rather than an error.

**Disable sources in `config/sources.yaml` rather than deleting them** — `doctor` only checks
enabled ones. `ihc_pdf` (Claude-extracted PDF backfill) is disabled by default.

**Adding a gallery** is usually just a `config/sources.yaml` entry pointing `adapter: gallery` at
the venue's listing page with `card`/`title` selectors (plus `dates` or `date_start`+`date_end`).
`tests/conftest.py::build_configured_source` builds the source from the *shipped* config, so a
selector typo fails the suite rather than the next refresh.

**Adding a source with a shape of its own:** write `delhi_events/sources/<name>.py` with a `Source(BaseSource)` class,
register it in `config/sources.yaml`, save a response fixture in `tests/fixtures/`, add it to
`TARGETS` in `scripts/capture_fixtures.py`, and add a fixture in `tests/conftest.py` plus a test
asserting real field values. Nothing downstream (store, taxonomy, build, feeds) needs changes.

## Testing

Tests are golden-file based and never hit the network. `tests/conftest.py` provides `FakeFetcher`,
which routes URLs to saved fixtures by regex in order and **raises on an unrouted URL** — a silent
empty response would let a broken parser pass its own test. Assert real parsed values (titles,
dates, rooms), not just counts.

Fixtures are refreshed manually with `make fixtures` after a venue legitimately redesigns its
site; the suite deliberately never refreshes them itself. Then re-run the tests and update the
expectations that changed.

## CI

`.github/workflows/refresh.yml` runs daily at 06:00 IST (and on pushes to `main` touching
`delhi_events/`, `site/template.html` or `config/sources.yaml`): tests parsers against fixtures,
refreshes, builds, runs `doctor`, commits `data/events.db` and `site/dist` (both are tracked, not
ignored), and deploys to Pages. Commits and deploys are gated on the *build* succeeding, so a
single broken source still publishes what came through, while broken code does not reach the site.
