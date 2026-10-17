# Boulder City Council Transcript Scraper

Tools for downloading and combining transcripts of City of Boulder (Colorado) City Council meetings.

The city doesn't publish written transcripts. Meeting videos are posted to the city's YouTube channel ([youtube.com/user/bouldercoloradogov](https://www.youtube.com/user/bouldercoloradogov)), and these scripts save the English captions from those videos. The city's uploaded captions are used when they exist; otherwise YouTube's auto-generated captions are used.

| Script | Purpose |
| --- | --- |
| `boulder_council_transcripts.py` | Finds council meeting videos and saves each one's transcript |
| `combine_transcripts.py` | Joins saved transcripts for a year or month into one dated text file |
| `watch_council_videos.py` | Checks the channel every 20 minutes for new council videos |
| `build_site.py` | Builds a static website of all saved transcripts |

## Setup

Requires Python 3.9+.

```
pip install yt-dlp
```

## 1. Download transcripts

```
python boulder_council_transcripts.py [options]
```

The script lists every video on the channel whose title contains "City Council": regular meetings, study sessions and special meetings. It reads the meeting date from the title and saves the newest meetings first.

### Options

| Option | Description |
| --- | --- |
| `--since YYYY-MM-DD` | Only meetings on or after this date |
| `--limit N` | Fetch at most N meetings |
| `--video ID` | Fetch a specific YouTube video ID instead of listing the channel. Can be repeated. |
| `--out DIR` | Output folder (default: `transcripts`) |
| `--no-timestamps` | Write the `.txt` transcript as plain prose without timestamps |
| `--overwrite` | Re-fetch meetings that are already saved (they're skipped by default) |
| `--delay SECONDS` | Pause between videos (default: 3) |
| `--cookies-from-browser NAME` | Load YouTube cookies from `chrome`, `edge`, `firefox`, etc. |
| `--cookies FILE` | Load cookies from a Netscape-format `cookies.txt` |

### Examples

```
# Every 2026 meeting
python boulder_council_transcripts.py --since 2026-01-01 --cookies-from-browser chrome

# The five most recent meetings
python boulder_council_transcripts.py --limit 5 --cookies-from-browser chrome

# One specific meeting
python boulder_council_transcripts.py --video SS0nUFcZFq0 --cookies-from-browser chrome
```

### Output

Two files are saved for each meeting in `transcripts/`, named `<meeting date>_<video id>`:

- **`2026-09-17_SS0nUFcZFq0.txt`**: a header (title, video URL, caption type) followed by timestamped lines:
  ```
  [00:03:52] Thursday, September 3rd, 2026 special
  ```
- **`2026-09-17_SS0nUFcZFq0.json`**: the same content in structured form:
  ```json
  {
    "video_id": "SS0nUFcZFq0",
    "title": "September 17, 2026 City of Boulder City Council Meeting",
    "date": "2026-09-17",
    "url": "https://www.youtube.com/watch?v=SS0nUFcZFq0",
    "auto_generated": true,
    "segments": [{"start": 228.4, "text": "..."}]
  }
  ```

Meetings fetched with `--video` have no title date, so their files start with `undated_`. Rename them to `YYYY-MM-DD_<id>` so `combine_transcripts.py` includes them.

## 2. Combine transcripts

```
python combine_transcripts.py PERIOD [--dir DIR] [--out FILE]
```

| Argument | Description |
| --- | --- |
| `PERIOD` | `YYYY` for a whole year or `YYYY-MM` for one month |
| `--dir DIR` | Folder of saved transcripts (default: `transcripts`) |
| `--out FILE` | Output file (default: `boulder_council_transcripts_<PERIOD>.txt`) |

### Examples

```
python combine_transcripts.py 2026       # -> boulder_council_transcripts_2026.txt
python combine_transcripts.py 2026-09    # -> boulder_council_transcripts_2026-09.txt
```

Meetings are written in date order. Each meeting starts with a header block, and every line carries the meeting date and the time into the video:

```
================================================================================
September 3, 2026 City of Boulder City Council Special Meeting and Regular Meeting
https://www.youtube.com/watch?v=wKTZP4C5Csw
Captions: auto-generated
================================================================================
[2026-09-03 00:03:48] go ahead um and call us to order for the
[2026-09-03 00:03:52] Thursday, September 3rd, 2026 special
```

The script reads the `.json` files, so `--no-timestamps` in step 1 doesn't affect it. An existing output file is overwritten.

## 3. Watch for new meetings

```
python watch_council_videos.py [options]
```

Checks the channel every 20 minutes and logs any council video it hasn't seen before. The first run only records the videos already on the channel. Seen videos are kept in `seen_videos.json` and every check is logged to `watch_council_videos.log`. Stop it with Ctrl+C.

| Option | Description |
| --- | --- |
| `--interval MINUTES` | Minutes between checks (default: 20) |
| `--once` | Check once and exit, e.g. when run from Windows Task Scheduler |
| `--fetch` | Also save each new video's transcript to `--out`. Videos without captions yet are retried on later checks. |
| `--out DIR` | Transcript folder for `--fetch` (default: `transcripts`) |
| `--scan N` | Newest videos to scan per channel tab (default: 30) |
| `--state FILE` / `--log FILE` | Seen-videos file and log file |
| `--cookies-from-browser NAME` / `--cookies FILE` | Same as in step 1 |

```
python watch_council_videos.py --fetch --cookies-from-browser chrome
```

## 4. Build the website

```
python build_site.py [--dir DIR] [--out DIR]
```

Writes a static site to `docs/` (default): `index.html` links every meeting, grouped by year and newest first, and each meeting gets its own page with the transcript as plain text. Timestamps are removed, and a new paragraph starts at each `>>` (the captions' speaker-change marker). GitHub Pages serves it from `main` / `/docs`; any static host works.

## Troubleshooting

**"Sign in to confirm you're not a bot"**: YouTube is blocking anonymous requests from your network. Pass `--cookies-from-browser` with a browser where you're signed in to YouTube. Close Chrome or Edge first if they lock their cookie database.

**"HTTP Error 429: Too Many Requests"**: you're being rate-limited. Wait a while, raise `--delay`, and run the same command again. Meetings that were already saved are skipped.

**`none ... (no English captions yet)`**: captions for very recent meetings, especially live streams, can take a few days to appear. Run the script again later.

**Exit code 1**: at least one video failed. The errors are printed for each video, and the other meetings are still saved.

## Caveats

- Most transcripts are YouTube auto-generated captions. Names, numbers and technical terms are often wrong. Check quotes against the video before relying on them.
- A transcript's timestamp is the time into the video, not the time of day.
- Titles are matched on "City Council", so any other channel video with that phrase in its title, such as a candidate forum, would also be included.
