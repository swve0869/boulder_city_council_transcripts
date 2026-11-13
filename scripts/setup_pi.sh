#!/usr/bin/env bash
# Set up this repo on a Raspberry Pi (or any Debian/Ubuntu machine with systemd):
#   - installs system packages, creates .venv and installs requirements
#   - installs and starts the transcript server (council-transcripts.service)
#   - installs and starts the 20-minute watcher timer (council-watch.timer)
#   - installs log rotation for the watcher's log
#
# Run from the repo as your normal user (it uses sudo where needed); safe to re-run.
#
#   scripts/setup_pi.sh              # full setup
#   scripts/setup_pi.sh --no-timer   # skip the watcher timer (e.g. you use cron instead)
#   scripts/setup_pi.sh --dry-run    # show what would be installed, change nothing
#
# Environment overrides: PORT (default 8765), TRANSCRIPT_ORIGIN (default: the
# https://<user>.github.io origin derived from the git remote).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PORT="${PORT:-8765}"
TIMER=1
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --no-timer) TIMER=0 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '\n== %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
run()  { if [ "$DRY_RUN" = 1 ]; then echo "+ $*"; else "$@"; fi; }

if [ "$(id -u)" = 0 ]; then
  echo "Run this as the user that will own the services, not as root (it calls sudo itself)." >&2
  exit 1
fi
USER_NAME="$(id -un)"

say "Checking the system"
ARCH="$(uname -m)"
if [ "$ARCH" != aarch64 ] && [ "$ARCH" != x86_64 ]; then
  warn "architecture is $ARCH. A 64-bit OS is recommended; some Python packages may have to compile from source."
fi

export DEBIAN_FRONTEND=noninteractive
run sudo -E apt-get update -qq
run sudo -E apt-get install -y -qq python3 python3-venv python3-pip git rsync logrotate util-linux

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo "Python 3.11+ is required (found $(python3 --version)). Use Raspberry Pi OS Bookworm (64-bit) or newer." >&2
  exit 1
fi

say "Python environment (.venv)"
if [ ! -x .venv/bin/python ]; then
  run python3 -m venv .venv
fi
run .venv/bin/pip install -q --upgrade pip
run .venv/bin/pip install -q -r requirements.txt

say "Secrets (.env)"
if [ ! -f .env ]; then
  run cp .env.example .env
  run chmod 600 .env
  warn "created .env from .env.example: add your ANTHROPIC_API_KEY (and any other provider keys) to $REPO/.env"
elif ! grep -qE '^ANTHROPIC_API_KEY=.+' .env && [ ! -s "API Keys/city_council_summary_API_key" ]; then
  warn "no ANTHROPIC_API_KEY in .env; summaries will fail until you add one"
fi

if [ -z "${TRANSCRIPT_ORIGIN:-}" ]; then
  REMOTE="$(git remote get-url origin 2>/dev/null || true)"
  GH_USER="$(printf '%s' "$REMOTE" | sed -nE 's#.*github\.com[:/]+([^/]+)/.*#\1#p' | tr 'A-Z' 'a-z')"
  if [ -n "$GH_USER" ]; then
    TRANSCRIPT_ORIGIN="https://$GH_USER.github.io"
  else
    TRANSCRIPT_ORIGIN="*"
    warn "couldn't derive the site origin from the git remote; allowing any origin (set TRANSCRIPT_ORIGIN to restrict it)"
  fi
fi

say "systemd services (user $USER_NAME, origin $TRANSCRIPT_ORIGIN, port $PORT)"
RENDERED="$(mktemp -d)"
trap 'rm -rf "$RENDERED"' EXIT
render() {
  sed -e "s#@USER@#$USER_NAME#g" -e "s#@REPO@#$REPO#g" -e "s#@PORT@#$PORT#g" \
      -e "s#@ORIGIN@#$TRANSCRIPT_ORIGIN#g" "$1" > "$RENDERED/$(basename "$1")"
}
for f in deploy/systemd/*; do render "$f"; done
render deploy/logrotate.conf

if [ "$DRY_RUN" = 1 ]; then
  for f in "$RENDERED"/*; do printf -- '--- %s\n' "$(basename "$f")"; cat "$f"; done
fi
run sudo install -m 644 "$RENDERED"/council-*.service "$RENDERED"/council-*.timer /etc/systemd/system/
run sudo install -m 644 "$RENDERED/logrotate.conf" /etc/logrotate.d/council-transcripts
run sudo systemctl daemon-reload
run sudo systemctl enable --now council-transcripts.service
if [ "$TIMER" = 1 ]; then
  run sudo systemctl enable --now council-watch.timer
else
  run sudo systemctl disable --now council-watch.timer 2>/dev/null || true
fi

say "Checking GitHub push access (needed to publish summaries)"
if [ "$DRY_RUN" = 0 ] && ! git push --dry-run origin HEAD >/dev/null 2>&1; then
  warn "this machine can't push to $(git remote get-url origin). Add a deploy key with write access:
    ssh-keygen -t ed25519 -f ~/.ssh/council_deploy -N ''
    then paste ~/.ssh/council_deploy.pub into GitHub > repo Settings > Deploy keys (tick 'Allow write access')
    and add to ~/.ssh/config:
      Host github.com
        IdentityFile ~/.ssh/council_deploy"
fi

[ -d transcripts ] || warn "no transcripts/ folder yet. Copy it from your old machine, e.g.
    rsync -a old-machine:boulder_city_council_transcripts/transcripts ./"

cat <<EOF

Done. Next steps:
  1. Expose the transcript server over HTTPS (no router changes needed):
       curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
       sudo tailscale funnel --bg $PORT
     Put the https://...ts.net address it prints in docs/config.js (transcriptHost), then commit and push.
  2. Make sure no other machine is still running the watcher (cron or the old session).
  3. Check on things:
       systemctl status council-transcripts council-watch.timer
       journalctl -u council-watch -n 50        # last watcher run
       sudo systemctl start council-watch       # run a cycle now
EOF
