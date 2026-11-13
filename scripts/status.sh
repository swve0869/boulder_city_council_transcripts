#!/usr/bin/env bash
# One-screen health check for the host (read-only). Run on the Pi:  scripts/status.sh
# Exits 1 if something needs attention, so it can be used from a monitor or cron mail.
cd "$(dirname "$0")/.."
ok=0
row()  { printf '%-22s %s\n' "$1" "$2"; }
flag() { ok=1; row "$1" "!! $2"; }

echo "== Services"
for unit in council-transcripts.service council-watch.timer; do
  state="$(systemctl is-active "$unit" 2>/dev/null)"
  [ "$state" = active ] && row "$unit" "active" || flag "$unit" "${state:-not installed}"
done
next="$(systemctl list-timers council-watch.timer --no-pager 2>/dev/null | sed -n 2p | awk '{print $1, $2, $3}')"
row "next watcher run" "${next:-unknown}"
if systemctl cat council-watch.service >/dev/null 2>&1; then
  result="$(systemctl show council-watch.service -p Result --value 2>/dev/null)"
  [ "$result" = success ] && row "last watcher result" "success" || flag "last watcher result" "${result:-unknown} (journalctl -u council-watch -n 30)"
fi

echo; echo "== Watcher (last run)"
journalctl -u council-watch -n 40 --no-pager -o cat 2>/dev/null | grep -E '^\[' | tail -5 | sed 's/^/  /'
if [ -f seen_videos.json ]; then
  python3 - <<'PY'
import json
state = json.load(open("seen_videos.json"))
for city, s in state.items():
    print(f"  {city}: {len(s.get('seen', {}))} videos seen, {len(s.get('pending', []))} awaiting captions, "
          f"{len(s.get('unsummarized', []))} awaiting summary")
    for p in s.get("pending", []):
        print(f"      captions pending: {p['title']}")
    for p in s.get("unsummarized", []):
        print(f"      summary pending:  {p}")
PY
else
  row "seen_videos.json" "missing (the watcher hasn't run yet)"
fi

echo; echo "== Site data"
row "summaries" "$(ls docs/summaries/*/*.json 2>/dev/null | wc -l) meetings"
row "transcripts" "$(ls transcripts/*/*.json 2>/dev/null | wc -l) meetings, $(du -sh transcripts 2>/dev/null | cut -f1)"
row "last commit" "$(git log -1 --format='%h %cr: %s' 2>/dev/null)"
git fetch -q origin 2>/dev/null
behind="$(git rev-list --count HEAD..origin/main 2>/dev/null)"; ahead="$(git rev-list --count origin/main..HEAD 2>/dev/null)"
[ "${ahead:-0}" = 0 ] && row "unpushed commits" "none" || flag "unpushed commits" "$ahead (publishing may be failing: check push access)"
[ "${behind:-0}" = 0 ] || row "behind origin/main" "$behind commit(s): run git pull --rebase"
[ -z "$(git status --porcelain -- docs 2>/dev/null)" ] || flag "docs/ changes" "uncommitted files in docs/ (git status)"

echo; echo "== Transcript server and Funnel"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:8765/$(ls transcripts/*/*.json 2>/dev/null | head -1 | sed 's#^transcripts/##')")"
[ "$code" = 200 ] && row "local server" "serving transcripts" || flag "local server" "HTTP $code"
host="$(sed -nE 's/.*transcriptHost: *"([^"]*)".*/\1/p' docs/config.js)"
if [ -n "$host" ] && command -v tailscale >/dev/null; then
  tailscale funnel status 2>/dev/null | grep -q "Funnel on" && row "tailscale funnel" "on ($host)" || flag "tailscale funnel" "not on: sudo tailscale funnel --bg 8765"
else
  row "tailscale funnel" "not configured"
fi

echo; echo "== API usage"
if [ -f summarize_usage.log ]; then
  awk -F'\t' -v today="$(date +%F)" '{ split($3,i,"="); split($5,o,"="); split($6,c,"="); n++; tin+=i[2]; tout+=o[2]; cost+=c[2];
       if (index($1, today) == 1) { tn++; tcost+=c[2] } }
     END { printf "  all time: %d requests, %d tokens in, %d out, about $%.2f\n  today:    %d requests, about $%.2f\n", n, tin, tout, cost, tn, tcost }' summarize_usage.log
else
  row "summarize_usage.log" "no requests logged yet"
fi
grep -q '^ANTHROPIC_API_KEY=.\+' .env 2>/dev/null && row "API key" "set in .env" || flag "API key" "ANTHROPIC_API_KEY missing from .env"

echo; echo "== Disk"
df -h . | awk 'NR==2 { printf "  %s used of %s (%s), %s free\n", $3, $2, $5, $4 }'
use="$(df . | awk 'NR==2 { gsub("%","",$5); print $5 }')"; [ "${use:-0}" -lt 90 ] || flag "disk" "over 90% full"

echo; [ "$ok" = 0 ] && echo "All good." || echo "Needs attention: see the !! lines above."
exit "$ok"
