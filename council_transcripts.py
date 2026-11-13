"""Scrape transcripts of city council meetings from YouTube.

Cities and their YouTube channels are listed in national_city_councils.json
({"City name": "channel URL"}). This script lists a city's council videos and saves
each one's English captions (uploaded captions if present, otherwise YouTube's
auto-generated ones) to transcripts/<city-slug>/.

Usage:
    pip install -r requirements.txt
    python council_transcripts.py --city Boulder --limit 5
    python council_transcripts.py --city Boulder --since 2026-01-01 --cookies-from-browser chrome

If YouTube answers "Sign in to confirm you're not a bot", pass --cookies-from-browser
(chrome, edge, firefox, ...) or --cookies <cookies.txt>.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

CITIES_FILE = Path(__file__).with_name("national_city_councils.json")
TITLE_PATTERN = re.compile(r"city council", re.IGNORECASE)
DATE_PATTERN = re.compile(r"([A-Z][a-z]+)\.? (\d{1,2}),? (\d{4})")
SHORT_DATE_PATTERN = re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{2})\b")


def load_cities(path=CITIES_FILE):
    """{city name: YouTube channel URL} from national_city_councils.json."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def slugify(name):
    """'Boulder' -> 'boulder', 'Fort Collins, CO' -> 'fort-collins-co'. Used for folder names."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def select_cities(names, path=CITIES_FILE):
    """[(name, url)] for the requested city names (case-insensitive), or every city if none."""
    cities = load_cities(path)
    if not names:
        return list(cities.items())
    by_lower = {k.lower(): k for k in cities}
    missing = [n for n in names if n.lower() not in by_lower]
    if missing:
        raise SystemExit(f"Unknown city {missing}; known: {', '.join(cities)} (see {Path(path).name})")
    return [(by_lower[n.lower()], cities[by_lower[n.lower()]]) for n in names]


def ydl_options(cookies_from_browser=None, cookies=None):
    opts = {"no_warnings": True}
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    if cookies:
        opts["cookiefile"] = cookies
    return opts


def parse_title_date(title):
    """Meeting date from titles like 'September 17, 2026 City of Boulder City Council Meeting'."""
    m = SHORT_DATE_PATTERN.search(title)  # older titles: 'Boulder City Council Meeting 6-22-23'
    if m:
        try:
            return datetime.strptime("-".join(m.groups()), "%m-%d-%y").date()
        except ValueError:
            pass
    m = DATE_PATTERN.search(title)
    if not m:
        return None
    month, day, year = m.groups()
    month = "Sep" if month == "Sept" else month  # strptime knows "Sep" and "September" only
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(f"{month} {day} {year}", fmt).date()
        except ValueError:
            pass
    return None


def list_council_videos(ydl_opts, channel_url, limit=None, since=None):
    """Return [{id, title, date}] for council meetings on a channel, newest first."""
    import yt_dlp

    opts = {**ydl_opts, "extract_flat": True, "quiet": True}
    channel_url = channel_url.rstrip("/")
    videos, seen = [], set()
    with yt_dlp.YoutubeDL(opts) as ydl:
        for tab in ("streams", "videos"):
            info = ydl.extract_info(f"{channel_url}/{tab}", download=False)
            for entry in info.get("entries") or []:
                title = entry.get("title") or ""
                if entry["id"] in seen or not TITLE_PATTERN.search(title):
                    continue
                date = parse_title_date(title)
                if since and date and date < since:
                    continue
                seen.add(entry["id"])
                videos.append({"id": entry["id"], "title": title, "date": date})
    videos.sort(key=lambda v: v["date"] or datetime.min.date(), reverse=True)
    return videos[:limit] if limit else videos


def pick_caption_track(info):
    """Prefer human-uploaded English captions, fall back to auto-generated."""
    for key in ("subtitles", "automatic_captions"):
        tracks = info.get(key) or {}
        for lang in ("en", "en-US", "en-orig"):
            for fmt in tracks.get(lang, []):
                if fmt.get("ext") == "json3":
                    return fmt["url"], key == "automatic_captions"
        for lang, fmts in tracks.items():
            if lang.startswith("en"):
                for fmt in fmts:
                    if fmt.get("ext") == "json3":
                        return fmt["url"], key == "automatic_captions"
    return None, None


def json3_to_lines(data):
    """Convert YouTube json3 captions into [(start_seconds, text)]."""
    lines = []
    for event in data.get("events", []):
        segs = event.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).replace("\n", " ").strip()
        if text:
            lines.append((event.get("tStartMs", 0) / 1000, text))
    return lines


def fmt_ts(seconds):
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def fetch_transcript(video_id, ydl_opts):
    import yt_dlp

    with yt_dlp.YoutubeDL({**ydl_opts, "quiet": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        url, auto = pick_caption_track(info)
        if not url:
            return None, None
        # Reuse yt-dlp's session (cookies, headers) for the caption request.
        raw = ydl.urlopen(url).read()
    return json3_to_lines(json.loads(raw)), auto


def save_transcript(video, lines, auto, out_dir, timestamps=True):
    """Write <date>_<id>.txt and .json to out_dir; returns the .json path."""
    stem = f"{video['date'] or 'undated'}_{video['id']}"
    url = f"https://www.youtube.com/watch?v={video['id']}"
    header = [video["title"], url, f"Captions: {'auto-generated' if auto else 'uploaded'}", ""]
    if timestamps:
        body = [f"[{fmt_ts(t)}] {text}" for t, text in lines]
    else:
        body = [" ".join(text for _, text in lines)]
    (out_dir / f"{stem}.txt").write_text("\n".join(header + body) + "\n", encoding="utf-8")
    json_path = out_dir / f"{stem}.json"
    json_path.write_text(
        json.dumps(
            {
                "video_id": video["id"],
                "title": video["title"],
                "date": str(video["date"]) if video["date"] else None,
                "url": url,
                "auto_generated": auto,
                "segments": [{"start": t, "text": text} for t, text in lines],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return json_path


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--city", default="Boulder", help="city name from national_city_councils.json (default: Boulder)")
    p.add_argument("--out", default="transcripts", help="transcript root; files go in <out>/<city-slug>/ (default: transcripts)")
    p.add_argument("--limit", type=int, help="max number of meetings to fetch")
    p.add_argument("--since", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                   help="only meetings on/after this date (YYYY-MM-DD)")
    p.add_argument("--video", action="append", help="specific YouTube video ID(s) to fetch")
    p.add_argument("--no-timestamps", action="store_true", help="write the .txt as plain prose instead of timestamped lines")
    p.add_argument("--overwrite", action="store_true", help="re-fetch transcripts already saved")
    p.add_argument("--delay", type=float, default=3.0, help="seconds between requests (default: 3)")
    p.add_argument("--cookies-from-browser", help="browser to load YouTube cookies from (chrome, edge, firefox...)")
    p.add_argument("--cookies", help="path to a Netscape-format cookies.txt")
    args = p.parse_args()

    [(city, channel_url)] = select_cities([args.city])
    ydl_opts = ydl_options(args.cookies_from_browser, args.cookies)
    out_dir = Path(args.out) / slugify(city)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.video:
        videos = [{"id": v, "title": v, "date": None} for v in args.video]
    else:
        print(f"Listing {city} City Council videos...", file=sys.stderr)
        videos = list_council_videos(ydl_opts, channel_url, args.limit, args.since)
    print(f"Found {len(videos)} meeting(s)", file=sys.stderr)

    failures = 0
    for i, video in enumerate(videos):
        if not args.overwrite and list(out_dir.glob(f"*_{video['id']}.txt")):
            print(f"skip   {video['title']} (already saved)", file=sys.stderr)
            continue
        if i:
            time.sleep(args.delay)
        try:
            lines, auto = fetch_transcript(video["id"], ydl_opts)
        except Exception as e:  # keep going on per-video errors
            failures += 1
            print(f"ERROR  {video['title']}: {e}", file=sys.stderr)
            continue
        if not lines:
            print(f"none   {video['title']} (no English captions yet)", file=sys.stderr)
            continue
        path = save_transcript(video, lines, auto, out_dir, not args.no_timestamps)
        print(f"saved  {path.stem} ({len(lines)} lines)", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
