"""Build a static website from saved council transcripts.

Writes an index page linking every meeting, plus one page per meeting showing the
transcript as plain text without timestamps. A new paragraph starts at each ">>"
(the caption marker for a change of speaker).

Usage:
    python build_site.py                      # transcripts/ -> site/
    python build_site.py --dir transcripts --out site
"""

import argparse
import html
import json
import re
import sys
from datetime import datetime
from itertools import groupby
from pathlib import Path

from boulder_council_transcripts import DATE_PATTERN, SHORT_DATE_PATTERN, parse_title_date

SPEAKER_BREAK = re.compile(r"\s*>>\s*")

STYLE = """
:root { --bg: #fdfdfc; --fg: #1d1d1b; --muted: #6b6b66; --link: #1a5fb4; --rule: #e2e2dc; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #161615; --fg: #e8e8e3; --muted: #9a9a93; --link: #8fb8f0; --rule: #33332f; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
       font: 17px/1.65 Georgia, "Times New Roman", serif; }
main { max-width: 42rem; margin: 0 auto; padding: 2.5rem 16px 4rem; }
h1 { font-size: 1.6rem; line-height: 1.3; margin: 0 0 .5rem; }
h2 { font-size: 1.15rem; margin: 2.2rem 0 .6rem; padding-bottom: .3rem; border-bottom: 1px solid var(--rule); }
a { color: var(--link); }
.meta, .note { color: var(--muted); font: 14px/1.5 system-ui, sans-serif; }
.note { margin-bottom: 1.5rem; }
ul { list-style: none; padding: 0; margin: 0; }
li { padding: .35rem 0; }
li .date { display: inline-block; min-width: 6.5rem; color: var(--muted); font: 14px system-ui, sans-serif; }
article p { margin: 0 0 1.1em; }
"""

DISCLAIMER = ("Transcripts are taken from YouTube captions, mostly auto-generated. "
              "Names, numbers and terms are often wrong; check the video before quoting. "
              "This is not an official city record.")


def page(title, body):
    return (f'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{html.escape(title)}</title>\n<style>{STYLE}</style>\n</head>\n"
            f"<body>\n<main>\n{body}\n</main>\n</body>\n</html>\n")


def paragraphs(segments):
    text = " ".join(seg["text"] for seg in segments)
    return [p for p in SPEAKER_BREAK.split(text) if p.strip()]


def undated_title(title):
    """'October 1, 2026 City of Boulder City Council Meeting' -> 'City of Boulder City Council Meeting'."""
    for pattern in (SHORT_DATE_PATTERN, DATE_PATTERN):
        title = pattern.sub("", title)
    return re.sub(r"\s{2,}", " ", title).strip(" -,|")


def load_meetings(src):
    meetings = []
    for f in src.glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        # Date from the JSON, else the title, else a renamed file's YYYY-MM-DD prefix.
        date = data["date"] or (f.name[:10] if re.match(r"\d{4}-\d{2}-\d{2}_", f.name) else None)
        date = datetime.strptime(date, "%Y-%m-%d").date() if date else parse_title_date(data["title"])
        title = data["title"]
        if title == data["video_id"] and date:  # fetched with --video: no real title
            title = f"{date:%B} {date.day}, {date.year} City of Boulder City Council Meeting"
        meetings.append({**data, "title": title, "date": date, "slug": f.stem})
    meetings.sort(key=lambda m: (m["date"] or datetime.min.date(), m["slug"]), reverse=True)
    return meetings


def meeting_page(m):
    captions = "auto-generated" if m.get("auto_generated") else "uploaded"
    body = "\n".join(f"<p>{html.escape(p)}</p>" for p in paragraphs(m["segments"]))
    return page(m["title"], f"""<p class="meta"><a href="index.html">&larr; All meetings</a></p>
<h1>{html.escape(m['title'])}</h1>
<p class="meta"><a href="{html.escape(m['url'])}">Watch on YouTube</a> &middot; Captions: {captions}</p>
<p class="note">{DISCLAIMER}</p>
<article>
{body}
</article>""")


def index_page(meetings):
    sections = []
    for year, group in groupby(meetings, key=lambda m: m["date"].year if m["date"] else None):
        items = "\n".join(
            f'<li><span class="date">{m["date"].strftime("%b %d") if m["date"] else "Undated"}</span>'
            f'<a href="{m["slug"]}.html">{html.escape(undated_title(m["title"]))}</a></li>'
            for m in group)
        sections.append(f"<h2>{year or 'Undated'}</h2>\n<ul>\n{items}\n</ul>")
    return page("Boulder City Council Transcripts", f"""<h1>Boulder City Council Transcripts</h1>
<p class="note">{len(meetings)} meetings. {DISCLAIMER}</p>
{chr(10).join(sections)}""")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default="transcripts", help="folder of saved transcripts (default: transcripts)")
    p.add_argument("--out", default="site", help="output folder (default: site)")
    args = p.parse_args()

    meetings = load_meetings(Path(args.dir))
    if not meetings:
        print(f"No transcripts found in {args.dir}/", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for m in meetings:
        (out / f"{m['slug']}.html").write_text(meeting_page(m), encoding="utf-8")
    (out / "index.html").write_text(index_page(meetings), encoding="utf-8")
    print(f"Wrote {len(meetings)} meeting page(s) and index.html to {out}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
