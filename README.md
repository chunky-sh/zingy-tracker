# Delhi Culture

A tracker for what's on at Delhi's cultural centres — art exhibitions, talks,
nature and heritage walks, workshops, film and performance — pulled daily from
the venues' own programme pages into one filterable site and a set of calendar
feeds you can subscribe to.

Built because IIC, IHC, Alliance Française and Max Mueller Bhavan each publish
their programme separately, and the interesting things get lost between them.

## Quick start

```sh
make install     # .venv + dependencies
make refresh     # scrape every enabled source
make serve       # build and serve the site at localhost:8000
```

Other commands:

```sh
make list                    # upcoming events in the terminal
make test                    # golden-file tests, no network
make doctor                  # check sources for silent breakage
make refresh-llm             # refresh + Claude tagging (needs ANTHROPIC_API_KEY)

.venv/bin/python -m delhi_events.cli list --topic nature --days 14
.venv/bin/python -m delhi_events.cli list --format walk
```

## Sources

| Source | How it's read |
|---|---|
| India International Centre | `iicdelhi.in/programmes/current`, then each detail page for the exact date range |
| India Habitat Centre | `indiahabitat.org/Events` calendar grid + `/Exhibitions_Details` |
| Alliance Française | Per-event iCal at `/events/<slug>/ical/`, discovered from the listing and RSS |
| Goethe-Institut / MMB | The REST endpoint behind its Vue calendar |
| IHC monthly PDF | Claude-extracted backfill — **disabled by default**, see `config/sources.yaml` |

Everything not built yet, with the reconnaissance already done, is in
[FUTURE_SCOPE.md](FUTURE_SCOPE.md) — museums and galleries, bird walks, theatre,
and the Instagram path.

## How events are classified

Two independent axes, because interests cut across event types — "nature" is a
walk, a talk *and* a photography show:

- **format** — one of `exhibition`, `talk`, `walk`, `workshop`, `film`,
  `performance`, `festival`, `other`
- **topics** — zero to four of `art`, `nature`, `birds`, `heritage`,
  `sociology`, `cinema`, `music`, `dance`, `theatre`, `literature`,
  `photography`, `history`, `science`

Classification is keyword rules in `delhi_events/taxonomy.py`, using each venue's
own category where it publishes one. `--llm` sends only the events the rules
were unsure about to Claude for tagging.

## Calendar feeds

`make build` writes four subscribable feeds to `site/dist/`:

| File | Contains |
|---|---|
| `delhi-events.ics` | everything |
| `nature.ics` | nature, birds |
| `art.ics` | art, photography |
| `ideas.ics` | sociology, history, science, literature, heritage |

Subscribe by URL in Google Calendar (*Other calendars → From URL*) once the site
is deployed — subscribing beats importing, since the feed then updates itself.

## Adding a source

1. Write `delhi_events/sources/<name>.py` with a `Source(BaseSource)` class whose
   `fetch(fetcher)` returns a list of `Event`.
2. Add it to `config/sources.yaml`.
3. Save a response fixture in `tests/fixtures/` and add a test asserting real
   field values.

The adapter's only job is producing `Event` objects — the store, deduplication,
taxonomy, site and feeds need no changes. `Fetcher` handles retries, throttling,
and the session-cookie dance that `indiahabitat.org` requires.

An adapter should **raise** on failure rather than return an empty list: empty
is how `doctor` detects a parser that broke silently after a site redesign, so
it needs to mean "nothing is on", not "I crashed".

## Automation

`.github/workflows/refresh.yml` runs daily at 06:00 IST: tests the parsers
against fixtures, refreshes, builds, runs `doctor`, commits the data and deploys
to GitHub Pages. Set `ANTHROPIC_API_KEY` as a repository secret if you want LLM
tagging.

To enable it: `git init && git add . && git commit`, push to GitHub, then turn on
Pages (*Settings → Pages → Source: GitHub Actions*).

## Layout

```
config/sources.yaml       venue registry
delhi_events/
  models.py               Event model, stable ids, IST handling
  db.py                   SQLite store, dedupe, run history
  fetch.py                HTTP session, retries, cache
  taxonomy.py             format + topic classification
  llm.py                  Claude tagging and extraction (optional)
  build.py                events.json, ICS feeds, site render
  cli.py                  refresh | build | list | doctor | add-manual
  sources/                one module per venue
site/template.html        the site; filters are vanilla JS
tests/                    golden-file tests against saved fixtures
```

Times are IST throughout. Always confirm with the venue before travelling —
programmes change.
