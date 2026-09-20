# Future scope

v1 covered four cultural centres: IIC, IHC, Alliance Française and Goethe/MMB.
Since then Bikaner House and seven of the commercial galleries have been built
too -- see the README. This file records what is *still* unbuilt, with the
feasibility notes gathered while scoping so the next person doesn't re-do the
reconnaissance.

Adding a source is a new module in `delhi_events/sources/` plus an entry in
`config/sources.yaml`. The adapter's only job is to return `Event` objects; the
store, taxonomy, dedupe, site and feeds need no changes.

---

## Museums and galleries

**Built:** Bikaner House (its own adapter, because it paginates by month) plus
KNMA, Nature Morte, Vadehra, Shrine Empire, Latitude 28, Gallery Espace and
Exhibit 320 (all on the shared selector-driven `gallery` adapter -- adding one
more is a `config/sources.yaml` entry, not a module).

What that exercise established, for whoever adds the next one:

- **Gallery listings carry their own archive on the same page.** Shrine Empire
  serves 106 cards, one of which is current. The adapter filters by date; a
  gallery genuinely between shows is why they all set `allow_empty`.
- **Several galleries list shows their artists appear in elsewhere** -- KNMA
  carries MoMA and the V&A, Nature Morte carries London. Use `sub_venue_allow`
  to keep a Delhi tracker about Delhi.
- **Do not advertise an encoding you cannot decode.** Gallery Espace and
  Exhibit 320 both answered in Brotli the moment `Accept-Encoding` offered it,
  and the binary that came back looked exactly like a page with no events on
  it. Fixed in `fetch.py`; worth remembering when a new venue comes up empty.

Still unbuilt:

| Venue | Endpoint | Notes |
|---|---|---|
| **NGMA** | `ngmaindia.gov.in` | `exhibition.asp` 404s and the site root serves an 801-byte shell; the current path needs rediscovering. Government site, expect fragility. |
| **DAG** | `dagworld.com` | TLS handshake fails from here (`SSLError`); may need a different client or may simply be misconfigured. |
| **Akar Prakar**, **Sanskriti Kendra** | — | Same `SSLError`. |
| **Blueprint12** | `blueprint12.com/exhibitions/` | The listing is client-rendered -- the served HTML has the nav and nothing else. Needs the underlying JSON endpoint or a headless fetch. |
| **Threshold**, **Palette Art** | — | Served pages carry no dates. |
| **Art Alive** | `artalivegallery.com` | `/exhibitions` 404s; find the real path. |
| **Museo Camera** (Gurugram) | `museocamera.org/exhibitions/` | **Verified fetchable**, 213 date strings, photography-focused. NCR rather than Delhi -- worth adding if the tracker's radius grows. |
| **Lalit Kala Akademi** | `lalitkala.gov.in` | **Verified fetchable**, 60 date strings. Government, so expect the markup to move. |
| **Anant Art**, **Art Heritage**, **Triveni Kala Sangam**, **Ojas Art**, **PHOTOINK** | — | All fetchable with dates present; selectors not yet worked out. |
| **India Art Fair** | `indiaartfair.in` | Annual (February) rather than a rolling programme. |

## Nature, birds and walks

**BNHS and Sunder Nursery are now built** — see the README for how each works and
why neither is an ordinary scrape. What that exercise established:

- **BNHS Delhi CEC has no public dated listing.** `cecdelhi.org` no longer
  resolves (NXDOMAIN); `bnhs.org/content-details/delhi-cec` is prose with no
  programme. The only dated BNHS feed is the national `nature-trails` page, and
  Delhi appears on it rarely. CEC bookings go through `cecbnhsdelhi@bnhs.org`
  and 011-26042010.
- **Sunder Nursery publishes nothing dated.** `workshops-&-events.php` still
  contains Lorem Ipsum, `bird-walk.php`'s booking link carries `date=...2023`,
  the site copyright reads 2019, and both "Programmes & Events" and
  "Tours & Walks" in the footer link to Facebook. Their live programme is on
  Facebook and Instagram, not the website.

Still open:

| Organiser | Notes |
|---|---|
| **Sunder Nursery bird walk** | 08:00–10:00 with Ishtiyak Ahamad (Give Me Trees Trust), #8800623154. Deliberately *not* scheduled — the page shows one undated line and a 2023 booking link, so there is no recurrence to expand. Phone first; if it runs, add it to `config/recurring.yaml`. |
| **Delhi Earth Walks** (Asian Ecotours) | `earthwalks.asianecotours.com` — trip reports rather than a forward calendar. |
| **Delhi Bird Society** | Historically a Google Group. No scrapeable calendar; a mail-to-event bridge would be the path. |
| **WWF India** | Runs occasional nature education events in Delhi. |
| **Asola Bhatti / Delhi Forest Dept.** | Sanctuary access is event-based; worth checking whether the forest department publishes a permit or walk calendar. |

Because so much of this is announced informally, the `add-manual` path matters
more here than anywhere else — and a Facebook-page bridge would now be worth as
much as an Instagram one.

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

- **Recurrence is built** (`sources/recurring.py`) but is weekday-and-time only.
  Monthly rules ("first Sunday"), seasonal windows (birding is a winter sport in
  Delhi) and blackout dates would all be needed before it covers much more.
- **Cross-source dedupe.** `db.find_duplicate` currently requires the same
  `venue`, which is right for v1 (each centre lists only its own events) but
  will not catch a gallery show that appears on both the gallery's site and its
  host venue's. Relax to a fuzzy venue match when the first such pair appears.
- **Notifications.** A weekly digest of newly-added events matching chosen
  topics — the `first_seen` column already exists to support this.
- **Past-event archive.** Events are kept, not deleted, so a "what did I miss"
  view is a query away.
