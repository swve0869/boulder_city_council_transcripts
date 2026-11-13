"""Tests for serve_transcripts.py: transcript serving and the page-view counter (local server, no network)."""

import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import serve_transcripts as st


class Server(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "transcripts" / "boulder").mkdir(parents=True)
        (self.dir / "transcripts" / "boulder" / "2026-01-01_ab_c.json").write_text('{"segments": []}')
        (self.dir / "transcripts" / "secret.txt").write_text("no")
        self.visits = self.dir / "state" / "visits.json"
        st.TranscriptHandler.origin = "https://example.github.io"
        st.TranscriptHandler.counter = st.VisitCounter(self.visits)
        st.TranscriptHandler.log_message = lambda *a: None  # quiet test output
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(st.TranscriptHandler, directory=str(self.dir / "transcripts")))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, b""

    def post(self, path, body, content_type="text/plain"):
        req = urllib.request.Request(self.base + path, data=body, method="POST", headers={"Content-Type": content_type})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_serves_only_transcript_json(self):
        status, headers, body = self.get("/boulder/2026-01-01_ab_c.json")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "https://example.github.io")
        self.assertEqual(json.loads(body), {"segments": []})
        for path in ("/", "/boulder/", "/secret.txt", "/../serve_transcripts.py", "/boulder/missing.json"):
            self.assertEqual(self.get(path)[0], 404, path)

    def test_counts_valid_page_keys_only(self):
        for key in ("index", "meeting:boulder/2026-01-01_ab_c", "meeting:boulder/2026-01-01_ab_c", "issue:boulder/police", "trends:boulder"):
            self.assertEqual(self.post("/hit", json.dumps({"p": key}).encode()), 204)
        for bad in (b'{"p": "../../etc"}', b'{"p": "index<script>"}', b'{"p": 5}', b"not json", b'{"q": "index"}'):
            self.assertEqual(self.post("/hit", bad), 204, "rejected pings get the same answer")
        self.assertEqual(self.post("/hit", b"x" * (st.MAX_BODY + 1)), 400)
        self.assertEqual(self.post("/other", b'{"p": "index"}'), 404)
        st.TranscriptHandler.counter.flush()
        days = json.loads(self.visits.read_text())
        (pages,) = days.values()
        self.assertEqual(pages, {"index": 1, "meeting:boulder/2026-01-01_ab_c": 2, "issue:boulder/police": 1, "trends:boulder": 1})

    def test_counts_survive_restart_and_report(self):
        self.post("/hit", b'{"p": "about"}')
        st.TranscriptHandler.counter.flush()
        again = st.VisitCounter(self.visits)
        self.assertTrue(again.hit("about"))
        again.flush()
        out = io.StringIO()
        with redirect_stdout(out):
            st.report(self.visits, 7)
        self.assertIn("Page views, last 7 days: 2", out.getvalue())
        self.assertIn("about", out.getvalue())


class Counter(unittest.TestCase):
    def test_caps_distinct_keys_per_day_and_drops_old_days(self):
        c = st.VisitCounter(Path(tempfile.mkdtemp()) / "v.json")
        for i in range(st.MAX_KEYS_PER_DAY):
            self.assertTrue(c.hit(f"meeting:x/id{i}", day="2026-11-13"))
        self.assertFalse(c.hit("meeting:x/one-more", day="2026-11-13"), "new keys past the cap are ignored")
        self.assertTrue(c.hit("meeting:x/id0", day="2026-11-13"), "existing keys still count")
        c.hit("index", day="2000-01-01")
        c.flush()
        self.assertNotIn("2000-01-01", json.loads(c.path.read_text()))

    def test_disabled_without_a_file(self):
        self.assertFalse(st.VisitCounter(None).hit("index"))


if __name__ == "__main__":
    unittest.main()
