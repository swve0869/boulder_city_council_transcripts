"""Poll the City of Boulder YouTube channel for new City Council videos.

Checks every 20 minutes (by default) and reports any council video it hasn't seen
before. The first run only records the videos already on the channel. With --fetch,
it also saves each new video's transcript, retrying on later checks until captions
appear (they can lag a live stream by days).

Usage:
    python watch_council_videos.py
    python watch_council_videos.py --fetch --cookies-from-browser chrome
    python watch_council_videos.py --once        # single check, e.g. from Task Scheduler
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from boulder_council_transcripts import fetch_transcript, list_council_videos, save_transcript


def log(msg, log_file):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, file=sys.stderr)
    with log_file.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state(path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_state(path, state):
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def check(args, ydl_opts, state_path, log_file):
    state = load_state(state_path)
    # Only the newest entries of each channel tab are needed to spot new uploads.
    videos = list_council_videos({**ydl_opts, "playlistend": args.scan})

    if state is None:
        state = {"seen": {v["id"]: v["title"] for v in videos}, "pending": []}
        save_state(state_path, state)
        log(f"First run: recorded {len(videos)} existing council video(s); watching for new ones", log_file)
        return

    new = [v for v in videos if v["id"] not in state["seen"]]
    for v in new:
        state["seen"][v["id"]] = v["title"]
        log(f"NEW    {v['title']}  https://www.youtube.com/watch?v={v['id']}", log_file)
        if args.fetch:
            state["pending"].append({**v, "date": str(v["date"]) if v["date"] else None})
    if not new:
        log("No new council videos", log_file)
    save_state(state_path, state)

    if args.fetch:
        fetch_pending(args, ydl_opts, state, state_path, log_file)


def fetch_pending(args, ydl_opts, state, state_path, log_file):
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    still_pending = []
    for v in state["pending"]:
        video = {**v, "date": datetime.strptime(v["date"], "%Y-%m-%d").date() if v["date"] else None}
        try:
            lines, auto = fetch_transcript(video["id"], ydl_opts)
        except Exception as e:  # retry next check
            log(f"ERROR  {video['title']}: {e}", log_file)
            still_pending.append(v)
            continue
        if not lines:
            log(f"none   {video['title']} (no English captions yet; will retry)", log_file)
            still_pending.append(v)
            continue
        stem = save_transcript(video, lines, auto, out_dir, timestamps=True)
        log(f"saved  {stem}.txt ({len(lines)} lines)", log_file)
    state["pending"] = still_pending
    save_state(state_path, state)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--interval", type=float, default=20, help="minutes between checks (default: 20)")
    p.add_argument("--once", action="store_true", help="check once and exit")
    p.add_argument("--fetch", action="store_true", help="also save transcripts of new videos")
    p.add_argument("--out", default="transcripts", help="transcript output directory (default: transcripts)")
    p.add_argument("--scan", type=int, default=30, help="newest videos to scan per channel tab (default: 30)")
    p.add_argument("--state", default="seen_videos.json", help="file of already-seen videos (default: seen_videos.json)")
    p.add_argument("--log", default="watch_council_videos.log", help="log file (default: watch_council_videos.log)")
    p.add_argument("--cookies-from-browser", help="browser to load YouTube cookies from (chrome, edge, firefox...)")
    p.add_argument("--cookies", help="path to a Netscape-format cookies.txt")
    args = p.parse_args()

    ydl_opts = {"no_warnings": True}
    if args.cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (args.cookies_from_browser,)
    if args.cookies:
        ydl_opts["cookiefile"] = args.cookies

    state_path, log_file = Path(args.state), Path(args.log)
    while True:
        try:
            check(args, ydl_opts, state_path, log_file)
        except Exception as e:  # network/YouTube errors: keep watching
            log(f"ERROR  check failed: {e}", log_file)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
