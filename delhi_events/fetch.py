"""HTTP fetching with a persistent session, retries and an on-disk cache.

Several Delhi venue sites are picky: indiahabitat.org returns a 289-byte stub to
a bare request and only serves the real page once a session cookie is set and
the User-Agent looks like a browser. That handling lives here so adapters do not
each rediscover it.
"""

from __future__ import annotations

import hashlib
import importlib
import logging
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _accept_encoding() -> str:
    """Advertise only the codings we can actually decode.

    Hard-coding "gzip, deflate, br" invites a server to answer in Brotli, which
    urllib3 decodes only when the brotli package is installed. Without it the
    response comes back as binary, `resp.text` is mojibake, and the adapter
    reports a page with no cards on it rather than an error -- which reads to
    `doctor` as a venue with nothing on. Gallery Espace and Exhibit 320 both
    pick Brotli when offered it.
    """
    codings = ["gzip", "deflate"]
    for module, coding in (("brotli", "br"), ("brotlicffi", "br"), ("zstandard", "zstd")):
        if coding in codings:
            continue
        try:
            importlib.import_module(module)
        except ImportError:
            continue
        codings.append(coding)
    return ", ".join(codings)


DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": _accept_encoding(),
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


class Fetcher:
    """One instance per refresh run. Reuses connections and cookies."""

    def __init__(
        self,
        cache_dir: Path | str = CACHE_DIR,
        cache_ttl: int = 0,
        delay: float = 1.0,
        timeout: int = 30,
    ):
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.cache_dir = Path(cache_dir)
        self.cache_ttl = cache_ttl  # seconds; 0 disables the cache
        self.delay = delay  # politeness gap between live requests
        self.timeout = timeout
        self._last_request = 0.0

    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / f"{hashlib.sha1(url.encode()).hexdigest()}.bin"

    def _cached(self, url: str) -> bytes | None:
        if self.cache_ttl <= 0:
            return None
        path = self._cache_path(url)
        if path.exists() and time.time() - path.stat().st_mtime < self.cache_ttl:
            return path.read_bytes()
        return None

    def _store(self, url: str, body: bytes) -> None:
        if self.cache_ttl <= 0:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path(url).write_bytes(body)

    def _throttle(self) -> None:
        gap = time.time() - self._last_request
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last_request = time.time()

    def get_bytes(self, url: str, *, referer: str | None = None, retries: int = 3) -> bytes:
        cached = self._cached(url)
        if cached is not None:
            log.debug("cache hit %s", url)
            return cached

        headers = {"Referer": referer} if referer else {}
        last_error: Exception | None = None

        for attempt in range(retries):
            self._throttle()
            try:
                resp = self.session.get(url, headers=headers, timeout=self.timeout)
                resp.raise_for_status()
                self._store(url, resp.content)
                return resp.content
            except requests.RequestException as exc:
                last_error = exc
                wait = 2**attempt
                log.warning("fetch failed (%s/%s) %s: %s", attempt + 1, retries, url, exc)
                if attempt < retries - 1:
                    time.sleep(wait)

        raise RuntimeError(f"could not fetch {url}: {last_error}") from last_error

    def get(self, url: str, *, referer: str | None = None, retries: int = 3) -> str:
        raw = self.get_bytes(url, referer=referer, retries=retries)
        return raw.decode("utf-8", errors="replace")

    def warm(self, url: str) -> None:
        """Hit a page purely to collect cookies. Ignores failures -- a site that
        does not need warming should not break the adapter that warms it."""
        try:
            self._throttle()
            self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            log.debug("warm-up failed for %s: %s", url, exc)
