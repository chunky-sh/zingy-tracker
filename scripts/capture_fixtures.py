#!/usr/bin/env python3
"""Re-capture the golden-file fixtures from the live sites.

Run after a venue legitimately redesigns its site, then re-run the tests and
update the expected values that changed. Deliberately manual: a test suite that
silently refreshed its own fixtures could never fail.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from delhi_events.fetch import Fetcher  # noqa: E402
from delhi_events.sources.goethe import _page_url  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

TARGETS = [
    ("iic_current.html", "https://iicdelhi.in/programmes/current", None),
    ("iic_detail_unsung.html",
     "https://iicdelhi.in/programmes/exhibition-unsung-celebrating-extraordinary-grandeur-smallness",
     None),
    ("ihc_events.html", "https://indiahabitat.org/Events", "https://indiahabitat.org/"),
    ("ihc_exhibitions.html", "https://indiahabitat.org/Exhibitions_Details",
     "https://indiahabitat.org/"),
    ("ihc_detail_1317.html", "https://indiahabitat.org/Events_details/1317",
     "https://indiahabitat.org/Events"),
    ("af_listing.html", "https://afdelhi.org/events/", None),
    ("af_feed.xml", "https://afdelhi.org/events/feed/", None),
    ("af_bastille.ics",
     "https://afdelhi.org/events/bastille-day-french-film-screenings-afd-cine-club-july-2026/ical/",
     None),
    ("goethe_page0.json", _page_url(0), None),
    ("bnhs_nature_trails.html", "https://www.bnhs.org/nature-trails", None),
    ("sunder_heritage_walk.html",
     "https://www.sundernursery.org/heritage-and-nature-walk.php", None),
]


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(cache_ttl=0, delay=1.0)
    fetcher.warm("https://indiahabitat.org/")

    failures = 0
    for name, url, referer in TARGETS:
        try:
            body = fetcher.get_bytes(url, referer=referer)
        except RuntimeError as exc:
            print(f"  FAILED {name}: {exc}")
            failures += 1
            continue
        (FIXTURES / name).write_bytes(body)
        print(f"  wrote {name:32} {len(body):>8} bytes")

    if failures:
        print(f"\n{failures} fixture(s) could not be captured.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
