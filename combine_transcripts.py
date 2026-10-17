"""Combine saved council transcripts for a year or month into one text file.

Each line is prefixed with the meeting date and timestamp, e.g.
    [2026-09-17 01:23:45] Good evening and welcome...

Usage:
    python combine_transcripts.py 2026            # all meetings in 2026
    python combine_transcripts.py 2026-09         # September 2026 only
    python combine_transcripts.py 2026-09 --out sept.txt --dir transcripts
"""

import argparse
import json
import re
import sys
from pathlib import Path

from boulder_council_transcripts import fmt_ts

SEPARATOR = "=" * 80


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("period", help="YYYY or YYYY-MM")
    p.add_argument("--dir", default="transcripts", help="folder of saved transcripts (default: transcripts)")
    p.add_argument("--out", help="output file (default: boulder_council_transcripts_<period>.txt)")
    args = p.parse_args()

    if not re.fullmatch(r"\d{4}(-(0[1-9]|1[0-2]))?", args.period):
        p.error("period must be YYYY or YYYY-MM, e.g. 2026 or 2026-09")

    files = sorted(Path(args.dir).glob(f"{args.period}-*.json"))
    if not files:
        print(f"No transcripts found for {args.period} in {args.dir}/", file=sys.stderr)
        return 1

    out_path = Path(args.out or f"boulder_council_transcripts_{args.period}.txt")
    total = 0
    with out_path.open("w", encoding="utf-8") as out:
        for f in files:
            data = json.loads(f.read_text(encoding="utf-8"))
            date = data["date"] or f.name[:10]
            captions = "auto-generated" if data.get("auto_generated") else "uploaded"
            out.write(f"{SEPARATOR}\n{data['title']}\n{data['url']}\nCaptions: {captions}\n{SEPARATOR}\n")
            for seg in data["segments"]:
                out.write(f"[{date} {fmt_ts(seg['start'])}] {seg['text']}\n")
            out.write("\n")
            total += len(data["segments"])

    print(f"Wrote {len(files)} meeting(s), {total} lines to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
