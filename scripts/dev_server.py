#!/usr/bin/env python3
"""Local dev server: rebuilds on change and reloads the open page.

The site embeds its event data inline, so nothing appears until `build` runs
again. This watches the template, the adapters and the store, rebuilds when any
of them change, and nudges the browser to reload.

The reload snippet is injected at serve time, so it never lands in site/dist
and cannot ship to GitHub Pages.

    python scripts/dev_server.py [--port 8000] [--refresh-every 0]

`--refresh-every N` also re-scrapes the venues every N minutes. Off by default:
a dev loop should not hammer someone's website.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import logging
import socketserver
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from delhi_events import build as build_mod  # noqa: E402
from delhi_events import db  # noqa: E402

DIST = ROOT / "site" / "dist"

WATCH = [
    ROOT / "site" / "template.html",
    ROOT / "delhi_events",
    ROOT / "config",
    ROOT / "data" / "events.db",
]

# Polls a counter that changes only when a rebuild actually happened, so an
# idle page makes one tiny request a second and never reloads on its own.
RELOAD_SNIPPET = """
<script>
(function () {
  var current = null;
  setInterval(function () {
    fetch("/__dev/version", { cache: "no-store" })
      .then(function (r) { return r.text(); })
      .then(function (v) {
        if (current === null) { current = v; return; }
        if (v !== current) { location.reload(); }
      })
      .catch(function () { /* server restarting; try again next tick */ });
  }, 1000);
})();
</script>
"""

log = logging.getLogger("dev")
_version = {"value": str(time.time())}


def _mtimes() -> dict[str, float]:
    stamps: dict[str, float] = {}
    for target in WATCH:
        if target.is_dir():
            for path in target.rglob("*"):
                if path.is_file() and path.suffix in {".py", ".html", ".yaml", ".yml"}:
                    stamps[str(path)] = path.stat().st_mtime
        elif target.exists():
            stamps[str(target)] = target.stat().st_mtime
    return stamps


def rebuild(reason: str) -> None:
    try:
        conn = db.connect()
        counts = build_mod.build(conn)
        conn.close()
    except Exception as exc:  # noqa: BLE001 - a syntax error mid-edit is normal here
        log.error("build failed (%s): %s", reason, exc)
        return
    _version["value"] = str(time.time())
    log.info("rebuilt after %s — %d events", reason, counts["events"])


def watch_loop(interval: float = 0.7) -> None:
    previous = _mtimes()
    while True:
        time.sleep(interval)
        current = _mtimes()
        if current == previous:
            continue
        changed = [Path(p).name for p in set(current) ^ set(previous)]
        changed += [Path(p).name for p in current
                    if p in previous and current[p] != previous[p]]
        previous = current
        rebuild(", ".join(sorted(set(changed))[:3]) or "a change")


def refresh_loop(minutes: int) -> None:
    from delhi_events.refresh import refresh

    while True:
        time.sleep(minutes * 60)
        log.info("re-scraping sources…")
        conn = db.connect()
        report = refresh(conn)
        conn.close()
        log.info("scrape done — %d events", report.total)
        rebuild("a scrape")


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path.startswith("/__dev/version"):
            payload = _version["value"].encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return

        if self.path in ("/", "/index.html"):
            page = (DIST / "index.html")
            if page.exists():
                body = page.read_text(encoding="utf-8").replace(
                    "</body>", f"{RELOAD_SNIPPET}</body>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return

        super().do_GET()

    def log_message(self, fmt: str, *args) -> None:
        if "/__dev/" not in (args[0] if args else ""):
            log.debug(fmt, *args)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--refresh-every", type=int, default=0,
                        metavar="MINUTES", help="also re-scrape the venues (0 = never)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    DIST.mkdir(parents=True, exist_ok=True)
    rebuild("startup")

    threading.Thread(target=watch_loop, daemon=True).start()
    if args.refresh_every > 0:
        threading.Thread(target=refresh_loop, args=(args.refresh_every,),
                         daemon=True).start()
        log.info("re-scraping every %d min", args.refresh_every)

    handler = functools.partial(Handler, directory=str(DIST))
    try:
        with Server(("127.0.0.1", args.port), handler) as httpd:
            log.info("\n  Delhi Culture — http://localhost:%d\n"
                     "  watching for changes; the page reloads itself. Ctrl-C to stop.\n",
                     args.port)
            httpd.serve_forever()
    except OSError as exc:
        log.error("could not bind port %d: %s", args.port, exc)
        log.error("something else is using it — try --port 8001")
        return 1
    except KeyboardInterrupt:
        log.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
