"""Poll each city's YouTube channel for new City Council videos.

Cities come from national_city_councils.json. For each one, any council video not seen
before is logged. The first check of a city only records the videos already there.

With --fetch, new videos' transcripts are saved to transcripts/<city-slug>/, retrying on
later checks until captions appear (they can lag a live stream by days). With --summarize,
each new transcript is summarized with Claude (see summarize.py) into docs/summaries/;
failed summaries are retried on later checks. With --publish, new summaries are committed
and pushed so GitHub Pages picks them up.

Usage:
    python watch_council_videos.py
    python watch_council_videos.py --once --fetch --summarize --publish   # what cron runs
    python watch_council_videos.py --city Boulder --fetch --cookies-from-browser chrome
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from council_transcripts import (fetch_transcript, list_council_videos, save_transcript, select_cities,
                                 slugify, ydl_options)


def log(msg, log_file):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, file=sys.stderr)
    with log_file.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state(path):
    if not path.exists():
        return {}
    state = json.loads(path.read_text(encoding="utf-8"))
    if "seen" in state:  # single-city format from before national_city_councils.json
        state = {"boulder": state}
    return state


def save_state(path, state):
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def check_city(name, channel_url, args, ydl_opts, state, save, log):
    """Look for new videos and (with --fetch) save transcripts. Returns paths of newly saved transcripts."""
    slug = slugify(name)
    # Only the newest entries of each channel tab are needed to spot new uploads.
    videos = list_council_videos({**ydl_opts, "playlistend": args.scan}, channel_url)

    if slug not in state:
        state[slug] = {"seen": {v["id"]: v["title"] for v in videos}, "pending": [], "unsummarized": []}
        save()
        log(f"{name}: first run, recorded {len(videos)} existing council video(s); watching for new ones")
        return []

    city = state[slug]
    city.setdefault("pending", [])
    city.setdefault("unsummarized", [])
    new = [v for v in videos if v["id"] not in city["seen"]]
    for v in new:
        city["seen"][v["id"]] = v["title"]
        log(f"{name}: NEW    {v['title']}  https://www.youtube.com/watch?v={v['id']}")
        if args.fetch:
            city["pending"].append({**v, "date": str(v["date"]) if v["date"] else None})
    if not new:
        log(f"{name}: no new council videos")
    save()
    return fetch_pending(name, args, ydl_opts, city, save, log) if args.fetch else []


def fetch_pending(name, args, ydl_opts, city, save, log):
    out_dir = Path(args.out) / slugify(name)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved, still_pending = [], []
    for v in city["pending"]:
        video = {**v, "date": datetime.strptime(v["date"], "%Y-%m-%d").date() if v["date"] else None}
        try:
            lines, auto = fetch_transcript(video["id"], ydl_opts)
        except Exception as e:  # retry next check
            log(f"{name}: ERROR  {video['title']}: {e}")
            still_pending.append(v)
            continue
        if not lines:
            log(f"{name}: none   {video['title']} (no English captions yet; will retry)")
            still_pending.append(v)
            continue
        path = save_transcript(video, lines, auto, out_dir)
        log(f"{name}: saved  {path.name} ({len(lines)} lines)")
        saved.append(path)
    city["pending"] = still_pending
    save()
    return saved


def summarize_new(name, new_paths, city, save, log):
    """Summarize new transcripts plus earlier failures. Returns the number of summaries written."""
    from summarize import summarize_transcript

    queue = list(dict.fromkeys(city["unsummarized"] + [str(p) for p in new_paths]))
    done, failed = 0, []
    for path in queue:
        if not Path(path).exists():
            log(f"{name}: skip   {path} (transcript file is gone)")
            continue
        try:
            out = summarize_transcript(path, slugify(name), log=lambda m: log(f"{name}: {m}"))
            log(f"{name}: summary docs/summaries/{out.parent.name}/{out.name}")
            done += 1
        except Exception as e:  # retry next check
            log(f"{name}: ERROR  summarizing {Path(path).name}: {e}")
            failed.append(path)
    city["unsummarized"] = failed
    save()
    return done


STATUS_FILE = Path(__file__).parent / "docs" / "summaries" / "status.json"


def write_status(state, cities, path=STATUS_FILE):
    """Publishable list of meetings that are known but not summarized yet. Returns True if it changed.

    The site shows these as "awaiting captions" / "awaiting summary", so visitors can see a
    new meeting is coming. Deliberately has no timestamp: the file only changes when the list does.
    """
    awaiting = []
    for name, _ in cities:
        city = state.get(slugify(name)) or {}
        for v in city.get("pending", []):
            awaiting.append({"city": slugify(name), "video_id": v["id"], "title": v["title"], "date": v.get("date"),
                             "waiting_for": "captions"})
        for transcript in city.get("unsummarized", []):
            stem = Path(transcript).stem
            date, _, video_id = stem.partition("_")
            try:
                title = json.loads(Path(transcript).read_text(encoding="utf-8")).get("title") or stem
            except (OSError, json.JSONDecodeError):
                title = stem
            awaiting.append({"city": slugify(name), "video_id": video_id, "title": title,
                             "date": None if date == "undated" else date, "waiting_for": "summary"})
    text = json.dumps({"awaiting": awaiting}, indent=1) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def run_once(args, ydl_opts, cities, state_path, log_file):
    """One check of every city. Returns False if any city's check failed."""
    say = lambda m: log(m, log_file)
    state = load_state(state_path)
    save = lambda: save_state(state_path, state)
    ok, summarized = True, 0

    for name, channel_url in cities:
        try:
            new_paths = check_city(name, channel_url, args, ydl_opts, state, save, say)
        except Exception as e:  # network/YouTube errors: keep going with the other cities
            say(f"{name}: ERROR  check failed: {e}")
            ok, new_paths = False, []
        city = state.get(slugify(name))
        if args.summarize and city and (new_paths or city.get("unsummarized")):
            summarized += summarize_new(name, new_paths, city, save, say)

    status_changed = args.fetch and write_status(state, cities)
    if summarized or status_changed:
        from build_site import build_manifest, publish
        if summarized:
            build_manifest()
        if args.publish:
            message = (f"Add {summarized} meeting summar{'y' if summarized == 1 else 'ies'}" if summarized
                       else "Update meetings awaiting captions")
            try:
                if publish(message):
                    say(f"published: {message.lower()}")
            except Exception as e:
                say(f"ERROR  publish failed: {e}")
                ok = False
    return ok


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--city", action="append", help="only watch this city (repeatable; default: all in national_city_councils.json)")
    p.add_argument("--cities-file", default="national_city_councils.json", help="city list (default: national_city_councils.json)")
    p.add_argument("--interval", type=float, default=20, help="minutes between checks (default: 20)")
    p.add_argument("--once", action="store_true", help="check once and exit (for cron)")
    p.add_argument("--fetch", action="store_true", help="also save transcripts of new videos")
    p.add_argument("--summarize", action="store_true", help="summarize newly saved transcripts with Claude (needs --fetch)")
    p.add_argument("--publish", action="store_true", help="git commit and push new summaries (needs --summarize)")
    p.add_argument("--out", default="transcripts", help="transcript root folder (default: transcripts)")
    p.add_argument("--scan", type=int, default=30, help="newest videos to scan per channel tab (default: 30)")
    p.add_argument("--state", default="seen_videos.json", help="file of already-seen videos (default: seen_videos.json)")
    p.add_argument("--log", default="watch_council_videos.log", help="log file (default: watch_council_videos.log)")
    p.add_argument("--cookies-from-browser", help="browser to load YouTube cookies from (chrome, edge, firefox...)")
    p.add_argument("--cookies", help="path to a Netscape-format cookies.txt")
    args = p.parse_args()
    if args.summarize and not args.fetch:
        p.error("--summarize needs --fetch")
    if args.publish and not args.summarize:
        p.error("--publish needs --summarize")

    cities = select_cities(args.city, args.cities_file)
    ydl_opts = ydl_options(args.cookies_from_browser, args.cookies)
    state_path, log_file = Path(args.state), Path(args.log)
    while True:
        ok = run_once(args, ydl_opts, cities, state_path, log_file)
        if args.once:
            return 0 if ok else 1
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
