"""Serve full transcripts from the host machine, and count page views for the site.

The site's meeting page fetches <host>/<city-slug>/<stem>.json when `transcriptHost` is set
in docs/config.js. This server answers only those requests: read-only, .json files under
the transcript folder, no directory listings, with a CORS header for the site's origin.

It also takes page-view pings: POST /hit with a page key such as "index" or
"meeting:boulder/2026-10-22_abc". Only a count per page per day is kept; no IP addresses,
user agents, cookies or referrers are stored. Counts are written to --visits (JSON) at most
every 30 seconds and on shutdown. `--report` prints them.

GitHub Pages is HTTPS, so browsers block plain-HTTP fetches from it. Put this behind a
TLS reverse proxy on your public name (on the Pi: Tailscale Funnel) and set transcriptHost
to that https:// URL.

Usage:
    python serve_transcripts.py --origin https://<user>.github.io --visits visits.json
    python serve_transcripts.py --report --visits visits.json [--days 30]
"""

import argparse
import json
import os
import re
import signal
import sys
import threading
import time
from datetime import date, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SAFE_PATH = re.compile(r"^/[a-z0-9-]+/[A-Za-z0-9_-]+\.json$")
# Page keys the site sends: a page name, optionally ":<city>" or ":<city>/<meeting id or tag>".
PAGE_KEY = re.compile(r"^(index|meeting|trends|issue|about)(:[a-z0-9-]{1,40}(/[A-Za-z0-9_-]{1,60})?)?$")
MAX_BODY = 256           # bytes accepted in a ping
MAX_KEYS_PER_DAY = 2000  # distinct pages counted per day; more are ignored (limits junk growth)
KEEP_DAYS = 400          # older days are dropped
FLUSH_SECONDS = 30


class VisitCounter:
    """{date: {page key: count}} in memory, written to a JSON file now and then."""

    def __init__(self, path):
        self.path = Path(path) if path else None
        self.lock = threading.Lock()
        self.data = {}
        self.dirty = False
        self.last_flush = 0.0
        if self.path and self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.data = {}

    def hit(self, key, day=None):
        """Count one view of `key`. Returns False if the key is rejected."""
        if not self.path or not PAGE_KEY.match(key):
            return False
        day = day or date.today().isoformat()
        with self.lock:
            pages = self.data.setdefault(day, {})
            if key not in pages and len(pages) >= MAX_KEYS_PER_DAY:
                return False
            pages[key] = pages.get(key, 0) + 1
            self.dirty = True
        if time.monotonic() - self.last_flush > FLUSH_SECONDS:
            self.flush()
        return True

    def flush(self):
        if not self.path:
            return
        with self.lock:
            if not self.dirty:
                return
            cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
            self.data = {d: p for d, p in self.data.items() if d >= cutoff}
            text = json.dumps(self.data, sort_keys=True, separators=(",", ":"))
            self.dirty = False
            self.last_flush = time.monotonic()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)  # atomic: a crash never leaves a half-written file


class TranscriptHandler(SimpleHTTPRequestHandler):
    origin = "*"
    counter = VisitCounter(None)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", self.origin)
        self.send_header("Vary", "Origin")
        super().end_headers()

    def allowed(self):
        if SAFE_PATH.match(self.path.split("?", 1)[0]):
            return True
        self.send_error(404)
        return False

    def do_GET(self):
        if self.allowed():
            super().do_GET()

    def do_HEAD(self):
        if self.allowed():
            super().do_HEAD()

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/hit":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            self.send_error(400)
            return
        try:
            key = json.loads(self.rfile.read(length)).get("p", "")
        except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
            key = ""
        self.counter.hit(key if isinstance(key, str) else "")
        self.send_response(204)  # same answer either way: nothing to learn from probing
        self.send_header("Content-Length", "0")
        self.end_headers()

    def list_directory(self, path):
        self.send_error(404)
        return None

    def log_request(self, code="-", size="-"):
        if self.path.startswith("/hit"):
            return  # page views are counted, not logged (keeps IPs out of the journal for them)
        super().log_request(code, size)


def report(path, days):
    """Print views per day and the most viewed pages for the last `days` days."""
    data = json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    recent = {d: p for d, p in sorted(data.items()) if d >= since}
    if not recent:
        print(f"No page views recorded in the last {days} days ({path}).")
        return
    total = sum(sum(p.values()) for p in recent.values())
    print(f"Page views, last {days} days: {total}")
    for d, pages in recent.items():
        n = sum(pages.values())
        print(f"  {d}  {n:5d}  {'#' * min(60, n)}")
    by_page = {}
    for pages in recent.values():
        for k, n in pages.items():
            by_page[k] = by_page.get(k, 0) + n
    by_kind = {}
    for k, n in by_page.items():
        by_kind[k.split(":")[0]] = by_kind.get(k.split(":")[0], 0) + n
    print("By page type: " + ", ".join(f"{k} {n}" for k, n in sorted(by_kind.items(), key=lambda kv: -kv[1])))
    print("Most viewed:")
    for k, n in sorted(by_page.items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {n:5d}  {k}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default="transcripts", help="transcript root folder (default: transcripts)")
    p.add_argument("--bind", default="0.0.0.0", help="address to listen on (default: 0.0.0.0)")
    p.add_argument("--port", type=int, default=8765, help="port (default: 8765)")
    p.add_argument("--origin", default="*", help="allowed CORS origin, e.g. https://<user>.github.io (default: *)")
    p.add_argument("--visits", help="JSON file for page-view counts (omit to ignore /hit pings)")
    p.add_argument("--report", action="store_true", help="print page-view counts from --visits and exit")
    p.add_argument("--days", type=int, default=14, help="days to include in --report (default: 14)")
    args = p.parse_args()

    if args.report:
        if not args.visits:
            p.error("--report needs --visits")
        report(args.visits, args.days)
        return 0

    TranscriptHandler.origin = args.origin
    TranscriptHandler.counter = VisitCounter(args.visits)
    server = ThreadingHTTPServer((args.bind, args.port), partial(TranscriptHandler, directory=args.dir))

    def stop(*_):
        TranscriptHandler.counter.flush()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)  # systemctl stop/restart: keep the counts
    print(f"Serving {args.dir}/ on http://{args.bind}:{args.port} (CORS origin: {args.origin}; "
          f"visits: {args.visits or 'off'})", file=sys.stderr)
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        TranscriptHandler.counter.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
