#!/usr/bin/env bash
# Run one watch cycle for every city in national_city_councils.json:
# find new council videos, save transcripts, summarize them with Claude, push summaries.
#
# crontab -e:
#   */20 * * * * /path/to/repo/scripts/cron_watch.sh >> /path/to/repo/cron.log 2>&1
#
# The API key comes from ANTHROPIC_API_KEY in .env, or else from the file
# "API Keys/city_council_summary_API_key" (both gitignored).
# Extra arguments are passed through, e.g. --cookies /path/to/cookies.txt
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a; . ./.env; set +a
fi
KEY_FILE="API Keys/city_council_summary_API_key"
if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -s "$KEY_FILE" ]; then
  ANTHROPIC_API_KEY="$(tr -d '[:space:]' < "$KEY_FILE")"
  export ANTHROPIC_API_KEY
fi

PYTHON="${PYTHON:-python3}"
[ -x .venv/bin/python ] && PYTHON=.venv/bin/python

# flock: skip this run if the previous one (e.g. a long summary) is still going.
exec flock -n .watch.lock "$PYTHON" watch_council_videos.py --once --fetch --summarize --publish "$@"
