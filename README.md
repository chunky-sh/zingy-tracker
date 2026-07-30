<div align="center">

# 🏛️ zingy-tracker 🌿

### *Delhi's culture, minus the FOMO.*

Six venues. One page. Zero doomscrolling through Instagram at 11pm<br>wondering if you missed the good exhibition. **(You did. Not any more.)**

![Python](https://img.shields.io/badge/python-3.12+-9f1111?style=for-the-badge&logo=python&logoColor=white)
![Refreshes](https://img.shields.io/badge/refreshes-daily%20at%206am%20IST-e2756f?style=for-the-badge)
![Tests](https://img.shields.io/badge/tests-95%20passing-2e7d32?style=for-the-badge)
![Feeds](https://img.shields.io/badge/calendar-4%20.ics%20feeds-c17817?style=for-the-badge)

**[→ See what's on](https://chunky-sh.github.io/zingy-tracker/)**

</div>

---

## 🎪 What it actually does

Scrapes **IIC**, **India Habitat Centre**, **Alliance Française**, **Goethe/Max Mueller Bhavan**, **BNHS** and **Sunder Nursery** every morning, sorts everything into *what kind of thing it is* (walk, talk, exhibition, film…) and *what it's about* (art, nature, birds, sociology…), then spits out a filterable page and calendar feeds you can subscribe to.

Two axes, because **"nature"** isn't a type of event — it's a walk, a talk *and* a photo show. Filter by what you care about, not by what shape it comes in. 🐦

## ⚡ Get it running

```sh
make install     # venv + deps, one time
make refresh     # go fetch everything
make dev         # → localhost:8000, reloads itself when things change
```

That's it. That's the setup.

<details>
<summary><b>🔧 The rest of the buttons</b></summary>

```sh
make list                    # what's on, in your terminal
make test                    # 95 golden-file tests, no network needed
make doctor                  # did a venue redesign and quietly break a parser?
make build                   # write site/dist without serving it
make fixtures                # re-snapshot the venue HTML tests run against

PORT=8001 make dev           # 8000 taken
make dev REFRESH=30          # re-scrape every 30 min while you work
make refresh-llm             # let Claude tag the ambiguous ones (needs ANTHROPIC_API_KEY)

.venv/bin/python -m delhi_events.cli list --topic birds --days 14
.venv/bin/python -m delhi_events.cli list --format walk
```
</details>

## 📅 Put it in your calendar

`make build` writes four feeds to `site/dist/`. Subscribe **by URL** (not import — then they update themselves):

| | |
|---|---|
| 🎨 `art.ics` | exhibitions, photography |
| 🌿 `nature.ics` | walks, birds, ecology |
| 💭 `ideas.ics` | talks, history, literature, science |
| 🌀 `delhi-events.ics` | absolutely everything |

## 🧠 Two things to know

**Adapters must raise, never return `[]`.** An empty list is how `make doctor` spots a parser that broke silently after a site redesign — so it has to mean *"nothing's on"*, not *"I fell over"*. Sources where empty is normal (hi, BNHS) set `allow_empty: true`.

**Some walks are declared, not scraped.** Sunder Nursery publishes no dated events anywhere, so its standing weekend walk lives in `config/recurring.yaml` behind a `confirmed_until` date. ⏰ **A test fails when it lapses** — ring the venue, confirm, push the date out. No silent fabrication.

Deeper notes live in `FUTURE_SCOPE.md` (what's next, and the reconnaissance already done), and in the docstrings — every adapter explains its venue's particular weirdness.

## 🤖 It refreshes itself

GitHub Actions, daily at **06:00 IST** → tests, scrapes, builds, health-checks, deploys to Pages. Committing the data each run doubles as a heartbeat, so GitHub never disables the cron for inactivity. Sneaky. 😎

---

<div align="center">

**Made with love for zingy** 💛

*Times are IST. Programmes change — always confirm with the venue before you travel.*

</div>
