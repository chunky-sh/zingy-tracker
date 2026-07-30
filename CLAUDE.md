# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A scraper + static-site generator for Delhi's cultural venues (IIC, India Habitat Centre,
Alliance Française, Goethe/Max Mueller Bhavan, BNHS, Sunder Nursery). It pulls each venue's
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
  and loads adapters by name from `config/sources.yaml`.
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

**Disable sources in `config/sources.yaml` rather than deleting them** — `doctor` only checks
enabled ones. `ihc_pdf` (Claude-extracted PDF backfill) is disabled by default.

**Adding a source:** write `delhi_events/sources/<name>.py` with a `Source(BaseSource)` class,
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
