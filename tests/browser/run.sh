#!/usr/bin/env bash
# Browser tests for the site, driven through headless Chromium (no npm packages).
#
#   tests/browser/run.sh                      # test docs/ served locally
#   tests/browser/run.sh https://<user>.github.io/<repo>   # test the live site
#
# Needs: node 20+, chromium (or set CHROME=/path/to/chrome). Local runs need transcripts/
# for the transcript checks. Reuses a Chromium already listening on $CDP_PORT.
set -uo pipefail
cd "$(dirname "$0")/../.."
BASE="${1:-http://localhost:8920}"
export CDP_PORT="${CDP_PORT:-9333}"
CHROME="${CHROME:-$(command -v chromium || command -v chromium-browser || command -v google-chrome || true)}"
NODE_FLAGS=""; node -e 'process.exit(typeof WebSocket === "undefined" ? 1 : 0)' 2>/dev/null || NODE_FLAGS="--experimental-websocket"
PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null; done; }
trap cleanup EXIT

if ! curl -s "http://127.0.0.1:$CDP_PORT/json/version" >/dev/null; then
  [ -n "$CHROME" ] || { echo "No Chromium found; set CHROME=/path/to/chrome" >&2; exit 2; }
  "$CHROME" --headless=new --disable-gpu --remote-debugging-port="$CDP_PORT" --user-data-dir="$(mktemp -d)" about:blank >/dev/null 2>&1 &
  PIDS+=($!)
  for _ in $(seq 1 40); do curl -s "http://127.0.0.1:$CDP_PORT/json/version" >/dev/null && break; sleep 0.25; done
fi
if [[ "$BASE" == http://localhost:8920* ]]; then
  curl -s -o /dev/null http://localhost:8920/index.html || { python3 -m http.server 8920 -d docs >/dev/null 2>&1 & PIDS+=($!); }
  curl -s -o /dev/null http://localhost:8921/ || { python3 serve_transcripts.py --port 8921 --bind 127.0.0.1 --origin '*' >/dev/null 2>&1 & PIDS+=($!); }
  sleep 1
fi

fail=0
for t in tests/browser/*.test.mjs; do
  echo "== $(basename "$t" .test.mjs)  ($BASE)"
  node $NODE_FLAGS "$t" "$BASE" 2>&1 | grep -v "ExperimentalWarning\|--trace-warnings" || true
  [ "${PIPESTATUS[0]}" = 0 ] || fail=1
done
[ "$fail" = 0 ] && echo "ALL BROWSER TESTS PASSED" || echo "BROWSER TESTS FAILED"
exit "$fail"
