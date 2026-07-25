# Future scope

v1 covers four cultural centres: IIC, IHC, Alliance Française and Goethe/MMB.
This file records everything considered but not built, with the feasibility
notes gathered while scoping so the next person doesn't re-do the reconnaissance.

Adding a source is a new module in `delhi_events/sources/` plus an entry in
`config/sources.yaml`. The adapter's only job is to return `Event` objects; the
store, taxonomy, dedupe, site and feeds need no changes.

---

## Museums and galleries

| Venue | Endpoint | Notes |
|---|---|---|
| **KNMA** (Kiran Nadar Museum of Art) | `knma.org/whats-on/` | **Verified fetchable.** Server-rendered, ~159KB, date ranges like `05 Feb 2026 — 26 Jul 2026` present in the HTML. Serves gzip without negotiation — send `Accept-Encoding` and decompress (`requests` does this automatically; a raw socket client will get binary). Note `knma.in` 301s to `knma.org`. Two JSON-LD blocks exist but neither is an `Event`. |
| **Bikaner House** | `bikanerhouse.rajasthan.gov.in/upcoming-events/<YYYY>/<M>` | **Predictable month URLs** — the cleanest pagination of any source found. Iterate the next 3 months. Also `/gallery/<YYYY>/<M>`. |
| **NGMA** | `ngmaindia.gov.in` | `exhibition.asp` 404s; the current path needs rediscovering. Government site, expect fragility. |
| **Vadehra Art Gallery** | `vadehraart.com/exhibitions/` | Artlogic-style platform; exhibitions carry explicit date ranges. Two Defence Colony spaces (D40, D53) — treat as `sub_venue`. |
| **Nature Morte** | `naturemorte.com` | Not probed. |
| **Shrine Empire** | `shrineempiregallery.com` | Has a `/news` section alongside exhibitions. |
| **Latitude 28**, **Gallery Espace**, **Exhibit 320**, **Blueprint12** | — | Small commercial galleries. Individually low volume; collectively significant for the `art` topic. |
| **Triveni Kala Sangam**, **Sanskriti Kendra** | — | Not probed. |

Galleries mostly publish a handful of long-running shows rather than a daily
programme, so they suit the `all_day` + date-range shape the IHC exhibitions
adapter already uses — start by copying `_parse_exhibition` from `sources/ihc.py`.

## Nature, birds and walks

This is the weakest-covered interest in v1 (`nature.ics` is currently empty) and
the hardest to automate — most of it is announced on WhatsApp, mailing lists and
Instagram rather than a programme page.

| Organiser | Notes |
|---|---|
| **BNHS Conservation Education Centre, Delhi** | Runs the Asola Bhatti Wildlife Sanctuary walks — roughly weekly, Sundays and holidays, ~25 people, online registration. The single highest-value addition for the `nature`/`birds` topics. Registration flow needs inspecting for a listing page. |
| **Sunder Nursery** | `sundernursery.org/bird-walk.php` and `/heritage-and-nature-walk.php`. Bird walks 08:00–10:00 with Ishtiyak Ahamad (Give Me Trees Trust). Static pages describing a recurring walk rather than dated events — may be better modelled as a recurring rule than scraped. |
| **Delhi Earth Walks** (Asian Ecotours) | `earthwalks.asianecotours.com` — trip reports rather than a forward calendar. |
| **Delhi Bird Society** | Historically a Google Group. No scrapeable calendar; a mail-to-event bridge would be the path. |
| **WWF India** | Runs occasional nature education events in Delhi. |

Because so much of this is announced informally, the `add-manual` path
(below) matters more here than anywhere else.

## Theatre and performance

| Venue | Notes |
|---|---|
| **National School of Drama** | Repertory season plus Bharat Rang Mahotsav. |
| **Kamani Auditorium** | Often ticketed via BookMyShow — the booking link may be an easier source than the venue site. |
| **Shri Ram Centre**, **LTG Auditorium** | — |

## Government and civic

| Source | Notes |
|---|---|
| **Sahitya Kala Parishad** | Delhi's cultural wing — Thumri Festival, Bhakti Sangeet Utsav, Yuva Natya Samaroh. Large festivals, low listing frequency. |
| **artandculture.delhi.gov.in** | Has a "Cultural Events" section. Government CMS, expect fragility. |

## Instagram

No viable API — the Basic Display API is retired and the Graph API only reaches
accounts you own. Three options, worst to best:

1. **Scraping** — against the ToS and reliably broken by rate limits. Not recommended.
2. **A per-account RSS bridge** (RSS-Bridge, self-hosted) — works, needs a
   server, and breaks whenever Instagram changes its markup.
3. **Manual paste** — already built:
   ```
   make refresh   # ...then
   .venv/bin/python -m delhi_events.cli add-manual --venue "Gallery Name" --url <permalink>
   # paste the caption, Ctrl-D, confirm what it extracted
   ```
   `llm.extract_events_from_text` parses the caption, the date guardrail rejects
   anything not present in the text, and it shows you the result before writing.

Given how much of Delhi's nature-walk and small-gallery programming lives on
Instagram, the manual path is likely the realistic long-term answer for those.

## Aggregators (evaluated, not recommended)

- **BookMyShow / District** — ticketed commercial events; almost no overlap with
  the free talks-and-exhibitions programming this tracker is for.
- **Delhi-Fun-Dos**, **LBB** — blog-style roundups, editorialised and often stale.
- **StageBuzz** — theatre listings, worth a second look if NSD/Kamani prove hard.

Going direct to venues gives cleaner data and no attribution questions. The one
argument for an aggregator is discovering venues not yet on the list.

---

## Platform work worth doing

- **Recurring events.** Sunder Nursery's weekly bird walk is a rule, not a
  listing. The `Event` model has no recurrence field; adding one (or expanding
  rules into concrete events at build time) is a prerequisite for several
  nature sources.
- **Cross-source dedupe.** `db.find_duplicate` currently requires the same
  `venue`, which is right for v1 (each centre lists only its own events) but
  will not catch a gallery show that appears on both the gallery's site and its
  host venue's. Relax to a fuzzy venue match when the first such pair appears.
- **Notifications.** A weekly digest of newly-added events matching chosen
  topics — the `first_seen` column already exists to support this.
- **Past-event archive.** Events are kept, not deleted, so a "what did I miss"
  view is a query away.
