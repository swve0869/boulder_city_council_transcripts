"""Serve full transcripts from the host machine so the GitHub Pages site can load them.

The site's meeting page fetches <host>/<city-slug>/<stem>.json when `transcriptHost` is set
in docs/config.js. This server answers only those requests: read-only, .json files under
the transcript folder, no directory listings, with a CORS header for the site's origin.

GitHub Pages is HTTPS, so browsers block plain-HTTP fetches from it. Put this behind a
TLS reverse proxy on your dynamic-DNS name (e.g. Caddy: `council.example.ddns.net {
reverse_proxy localhost:8765 }`) and set transcriptHost to that https:// URL.

Usage:
    python serve_transcripts.py --origin https://<user>.github.io
    python serve_transcripts.py --port 8765 --dir transcripts --origin '*'
"""

import argparse
import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

SAFE_PATH = re.compile(r"^/[a-z0-9-]+/[A-Za-z0-9_-]+\.json$")


class TranscriptHandler(SimpleHTTPRequestHandler):
    origin = "*"

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

    def list_directory(self, path):
        self.send_error(404)
        return None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default="transcripts", help="transcript root folder (default: transcripts)")
    p.add_argument("--bind", default="0.0.0.0", help="address to listen on (default: 0.0.0.0)")
    p.add_argument("--port", type=int, default=8765, help="port (default: 8765)")
    p.add_argument("--origin", default="*", help="allowed CORS origin, e.g. https://<user>.github.io (default: *)")
    args = p.parse_args()

    TranscriptHandler.origin = args.origin
    server = ThreadingHTTPServer((args.bind, args.port), partial(TranscriptHandler, directory=args.dir))
    print(f"Serving {args.dir}/ on http://{args.bind}:{args.port} (CORS origin: {args.origin})", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
