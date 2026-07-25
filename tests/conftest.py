"""Offline fetcher so adapter tests never touch the network."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from delhi_events.sources.base import SourceConfig, load_source

FIXTURES = Path(__file__).parent / "fixtures"


class FakeFetcher:
    """Serves saved responses by URL.

    Routes are (regex, fixture) pairs checked in order, so an adapter that
    follows N detail links is answered by one representative fixture. Any URL
    that matches nothing raises -- a silent empty response would let a broken
    parser pass its own test.
    """

    def __init__(self, routes: list[tuple[str, str]]):
        self.routes = [(re.compile(pattern), name) for pattern, name in routes]
        self.requested: list[str] = []

    def _resolve(self, url: str) -> Path:
        self.requested.append(url)
        for pattern, name in self.routes:
            if pattern.search(url):
                return FIXTURES / name
        raise AssertionError(f"no fixture routed for {url}")

    def get_bytes(self, url: str, *, referer: str | None = None, retries: int = 3) -> bytes:
        return self._resolve(url).read_bytes()

    def get(self, url: str, *, referer: str | None = None, retries: int = 3) -> str:
        return self._resolve(url).read_bytes().decode("utf-8", errors="replace")

    def warm(self, url: str) -> None:
        pass


def build_source(source_id: str, adapter: str, name: str, routes: list[tuple[str, str]]):
    config = SourceConfig(id=source_id, adapter=adapter, name=name,
                          url="https://example.test/", address="Test Address")
    return load_source(config), FakeFetcher(routes)


@pytest.fixture
def iic():
    return build_source("iic", "iic", "India International Centre", [
        (r"/programmes/current", "iic_current.html"),
        (r"/programmes/", "iic_detail_unsung.html"),
    ])


@pytest.fixture
def ihc():
    return build_source("ihc", "ihc", "India Habitat Centre", [
        (r"/Events_details/", "ihc_detail_1317.html"),
        (r"/Exhibitions_Details", "ihc_exhibitions.html"),
        (r"/Events", "ihc_events.html"),
    ])


@pytest.fixture
def alliance_francaise():
    return build_source("alliance_francaise", "alliance_francaise",
                        "Alliance Française de Delhi", [
                            (r"/events/feed/", "af_feed.xml"),
                            (r"ical/", "af_bastille.ics"),
                            (r"/events/$", "af_listing.html"),
                        ])


@pytest.fixture
def goethe():
    return build_source("goethe", "goethe", "Goethe-Institut / Max Mueller Bhavan", [
        (r"fetchEvents", "goethe_page0.json"),
    ])


@pytest.fixture
def bnhs():
    return build_source("bnhs", "bnhs", "BNHS Conservation Education Centre", [
        (r"nature-trails", "bnhs_nature_trails.html"),
    ])


@pytest.fixture
def sunder_nursery():
    return build_source("sunder_nursery", "recurring", "Sunder Nursery", [
        (r"heritage-and-nature-walk", "sunder_heritage_walk.html"),
    ])
