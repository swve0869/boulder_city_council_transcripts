"""Give `undated_<id>` transcripts and summaries their meeting date.

Transcripts fetched with --video (or whose listing had no date) are saved as
undated_<videoid>. When the saved YouTube title contains the date, this renames the
transcript (.json/.txt), its summary and its models.json entry to <YYYY-MM-DD>_<id>, and
sets "date" inside the transcript JSON. Safe to re-run; files that are already dated,
or whose title has no date, are left alone.

On a machine without the summaries' transcripts (or vice versa) it renames what it finds:
run it on the host for transcripts, and wherever summaries are committed.

Usage:
    python date_undated.py              # all cities
    python date_undated.py --dry-run
"""

import argparse
import json
import sys
from pathlib import Path

from council_transcripts import parse_title_date

ROOT = Path(__file__).parent


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default="transcripts", help="transcript root folder (default: transcripts)")
    p.add_argument("--summaries", default=str(ROOT / "docs" / "summaries"), help="summaries folder")
    p.add_argument("--dry-run", action="store_true", help="show what would be renamed")
    args = p.parse_args()

    summaries = Path(args.summaries)
    models_file = summaries / "models.json"
    models = json.loads(models_file.read_text(encoding="utf-8")) if models_file.exists() else {}
    renamed = 0

    for transcript in sorted(Path(args.dir).glob("*/undated_*.json")):
        city, video_id = transcript.parent.name, transcript.stem.split("_", 1)[1]
        data = json.loads(transcript.read_text(encoding="utf-8"))
        date = parse_title_date(data.get("title") or "")
        if not date:
            print(f"skip   {city}/{transcript.stem}: no date in title {data.get('title')!r}", file=sys.stderr)
            continue
        new_stem = f"{date}_{video_id}"
        print(f"{city}/{transcript.stem} -> {new_stem}", file=sys.stderr)
        renamed += 1
        if args.dry_run:
            continue
        data["date"] = str(date)
        transcript.write_text(json.dumps(data, indent=2), encoding="utf-8")
        transcript.rename(transcript.with_name(f"{new_stem}.json"))
        txt = transcript.with_suffix(".txt")
        if txt.exists():
            txt.rename(txt.with_name(f"{new_stem}.txt"))
        summary = summaries / city / f"{transcript.stem}.json"
        if summary.exists():
            summary.rename(summary.with_name(f"{new_stem}.json"))
        old_key = f"{city}/{transcript.stem}"
        if old_key in models:
            models[f"{city}/{new_stem}"] = models.pop(old_key)

    if renamed and not args.dry_run:
        if models_file.exists():
            models_file.write_text(json.dumps(dict(sorted(models.items())), indent=1) + "\n", encoding="utf-8")
        from build_site import build_manifest
        build_manifest(summaries)
    print(f"{renamed} meeting(s) {'would be ' if args.dry_run else ''}dated", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
