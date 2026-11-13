"""Build the GitHub Pages manifest from saved summaries, and optionally publish it.

The site in docs/ is static HTML + JavaScript. The browser loads docs/summaries/index.json
(written here) to list meetings, then fetches each summary's JSON as-is and renders it.
docs/summaries/search.json (also written here) holds topic summary text for search, and
docs/feed.xml is an Atom feed of the newest meetings.
docs/summaries/models.json (written by summarize.py) says which model wrote each summary.
Only summaries are published; full transcripts stay on the host machine.

Usage:
    python build_site.py              # rewrite index/search/feed and asset versions in docs/*.html
    python build_site.py --publish    # ...then git commit and push docs/summaries
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from html import escape
from pathlib import Path

from council_transcripts import load_cities, slugify

ROOT = Path(__file__).parent
SUMMARIES_DIR = ROOT / "docs" / "summaries"
STEM = re.compile(r"^(\d{4}-\d{2}-\d{2}|undated)_(.+)$")
TAGS_FILE = ROOT / "tags.json"
MIN_TOPIC_SECONDS = 60  # a topic with a single timestamp still counts as some discussion


def topics_of(summary):
    """[(title, tags, timestamps)] in order; accepts one topic per list item or several."""
    out = []
    for entry in summary.get("Topics") or []:
        if not isinstance(entry, dict):
            continue
        for key in sorted(entry, key=lambda k: int(re.sub(r"\D", "", k) or 0)):
            t = entry[key]
            if not isinstance(t, dict):
                continue
            stamps = []
            for x in t.get("related_timestamps") or []:
                try:
                    stamps.append(float(x))
                except (TypeError, ValueError):
                    pass
            out.append((str(t.get("Title", key)), [g for g in t.get("Tags") or [] if isinstance(g, str)], sorted(stamps),
                        str(t.get("Summary", ""))))
    return out


def topic_span(stamps, next_start):
    """(start, end) seconds of video a topic covers, estimated from its timestamps.

    Uses the first to last timestamp. A topic with a single timestamp runs until the
    next topic starts, capped at MIN_TOPIC_SECONDS.
    """
    if not stamps:
        return None, None
    start, end = stamps[0], stamps[-1]
    if end - start < MIN_TOPIC_SECONDS:
        end = start + MIN_TOPIC_SECONDS if next_start is None else min(start + MIN_TOPIC_SECONDS, max(next_start, start))
    return round(start, 2), round(end, 2)


def credit_tags(tags, seconds):
    """How a topic's estimated duration is credited to its tags: {tag: seconds}.

    Feeds tag_seconds, which drives the Trends charts and the "most time on selected
    tags" sort. Tags arrive most specific first (the prompt asks for that order).
    """
    # Split the duration with weights 1, 1/2, 1/3, ... by tag order, normalized to sum to 1.
    # A 60-minute topic tagged [zoning, housing, public-comment] credits about 33 / 16 / 11
    # minutes, so per-tag times add up to real meeting time and the main subject leads.
    weights = [1 / (i + 1) for i in range(len(tags))]
    total = sum(weights)
    return {tag: seconds * w / total for tag, w in zip(tags, weights)}


def meeting_entry(summary):
    """Per-meeting data the site uses for tags, filtering and charts."""
    topics, tag_seconds = [], {}
    raw = topics_of(summary)
    starts = [stamps[0] if stamps else None for _, _, stamps, _ in raw]
    for i, (title, tags, stamps, _) in enumerate(raw):
        later = [s for s in starts[i + 1:] if s is not None and stamps and s > stamps[0]]
        start, end = topic_span(stamps, min(later) if later else None)
        topics.append({"title": title, "tags": tags, "start": start, "end": end})
        for tag, secs in credit_tags(tags, (end - start) if start is not None else 0).items():
            tag_seconds[tag] = tag_seconds.get(tag, 0) + secs
    main = [g for g in summary.get("Tags") or [] if isinstance(g, str)]
    return {
        "tags": main,
        "tag_seconds": {t: round(v) for t, v in sorted(tag_seconds.items(), key=lambda kv: -kv[1])},
        "topics": topics,
    }


def build_manifest(summaries_dir=SUMMARIES_DIR):
    """Write <summaries_dir>/index.json listing every summary; returns the meeting count."""
    summaries_dir = Path(summaries_dir)
    names = {slugify(name): name for name in load_cities()}
    provenance_file = summaries_dir / "models.json"
    provenance = json.loads(provenance_file.read_text(encoding="utf-8")) if provenance_file.exists() else {}
    meetings, search = [], {}
    for f in sorted(summaries_dir.glob("*/*.json")):  # sorted: output must not depend on directory order
        m = STEM.match(f.stem)
        if not m:
            continue
        date, video_id = m.groups()
        try:
            summary = json.loads(f.read_text(encoding="utf-8"))
            title = summary.get("Title", "")
        except (json.JSONDecodeError, AttributeError):
            print(f"skip   {f} (not a valid summary)", file=sys.stderr)
            continue
        meetings.append({
            "city": f.parent.name,
            "id": f.stem,
            "date": None if date == "undated" else date,
            "video_id": video_id,
            "title": title,
            "overview": summary.get("Overview") or None,
            "model": provenance.get(f"{f.parent.name}/{f.stem}"),
            **meeting_entry(summary),
        })
        # Topic summary text, in topic order, for the site's full-text search (loaded on demand).
        search[f"{f.parent.name}/{f.stem}"] = [text for _, _, _, text in topics_of(summary)]
    meetings.sort(key=lambda m: (m["date"] or "", m["id"]), reverse=True)
    cities = sorted({m["city"] for m in meetings})
    vocabulary = json.loads(TAGS_FILE.read_text(encoding="utf-8")) if TAGS_FILE.exists() else {"groups": [], "tags": []}
    manifest = {
        "cities": [{"slug": s, "name": names.get(s, s.replace("-", " ").title())} for s in cities],
        "tag_groups": vocabulary["groups"],
        "tags": [{k: t[k] for k in ("id", "label", "group", "description")} for t in vocabulary["tags"]],
        "meetings": meetings,
    }
    summaries_dir.mkdir(parents=True, exist_ok=True)
    (summaries_dir / "index.json").write_text(json.dumps(manifest, separators=(",", ":")) + "\n", encoding="utf-8")
    (summaries_dir / "search.json").write_text(json.dumps(search, separators=(",", ":"), ensure_ascii=False) + "\n",
                                                encoding="utf-8")
    build_feed(manifest, search, summaries_dir)
    return len(meetings)


def build_feed(manifest, search, summaries_dir):
    """Write docs/feed.xml: an Atom feed of the newest dated meetings, for feed readers."""
    config_file = ROOT / "summarizer.toml"
    site = tomllib.loads(config_file.read_text(encoding="utf-8")).get("site", {}) if config_file.exists() else {}
    base = site.get("url", "").rstrip("/") + "/"
    if base == "/":
        return None  # no public URL configured: skip the feed
    labels = {t["id"]: t["label"] for t in manifest["tags"]}
    cities = {c["slug"]: c["name"] for c in manifest["cities"]}
    dated = [m for m in manifest["meetings"] if m["date"]][: site.get("feed_entries", 40)]
    updated = f"{max((m['date'] for m in dated), default='1970-01-01')}T00:00:00Z"
    out = ['<?xml version="1.0" encoding="utf-8"?>', '<feed xmlns="http://www.w3.org/2005/Atom">',
           f"<title>{escape(site.get('title', 'City Council Meeting Summaries'))}</title>",
           f'<link href="{escape(base)}"/>', f'<link rel="self" href="{escape(base)}feed.xml"/>',
           f"<id>{escape(base)}</id>", f"<updated>{updated}</updated>"]
    for m in dated:
        link = f"{base}meeting.html?city={m['city']}&id={m['id']}"
        texts = search.get(f"{m['city']}/{m['id']}", [])
        items = "".join(
            f"<li><strong>{escape(t['title'])}</strong>" + (f": {escape(texts[i])}" if i < len(texts) and texts[i] else "") + "</li>"
            for i, t in enumerate(m["topics"]))
        tags = ", ".join(labels.get(t, t) for t in list(m["tag_seconds"])[:6])
        html = ((f"<p>{escape(m['overview'])}</p>" if m.get("overview") else "")
                + (f"<p>Most discussed: {escape(tags)}</p>" if tags else "") + f"<ul>{items}</ul>")
        out += ["<entry>", f"<title>{escape(cities.get(m['city'], m['city']))}: {escape(m['title'])}</title>",
                f'<link href="{escape(link)}"/>', f"<id>{escape(link)}</id>", f"<updated>{m['date']}T00:00:00Z</updated>",
                *(f'<category term="{escape(t)}" label="{escape(labels.get(t, t))}"/>' for t in m["tag_seconds"]),
                f'<content type="html">{escape(html)}</content>', "</entry>"]
    out.append("</feed>")
    path = summaries_dir.parent / "feed.xml"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


ASSET_REF = re.compile(r'((?:src|href)=")([\w.-]+\.(?:js|css))(?:\?v=[0-9a-f]+)?(")')


def stamp_assets(docs_dir=SUMMARIES_DIR.parent):
    """Point each page at its scripts and stylesheet by content hash: app.js -> app.js?v=1a2b3c4d.

    Browsers may cache files for a while after a deploy; a versioned URL means a page
    never runs against a stale script. Returns the HTML files that changed.
    """
    docs_dir = Path(docs_dir)
    changed = []
    for page in sorted(docs_dir.glob("*.html")):
        html = page.read_text(encoding="utf-8")

        def stamp(m):
            asset = docs_dir / m.group(2)
            if not asset.exists():
                return m.group(0)
            return f"{m.group(1)}{m.group(2)}?v={hashlib.sha256(asset.read_bytes()).hexdigest()[:8]}{m.group(3)}"

        new = ASSET_REF.sub(stamp, html)
        if new != html:
            page.write_text(new, encoding="utf-8")
            changed.append(page.name)
    return changed


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def publish(message="Update meeting summaries"):
    """Commit docs/summaries and push. Returns True if something was pushed."""
    git("add", "--", str(SUMMARIES_DIR.relative_to(ROOT)), str((SUMMARIES_DIR.parent / "feed.xml").relative_to(ROOT)))
    if git("diff", "--cached", "--quiet").returncode == 0:
        return False
    for step in (("commit", "-m", message), ("pull", "--rebase", "--autostash"), ("push",)):
        result = git(*step)
        if result.returncode != 0:
            raise RuntimeError(f"git {step[0]} failed: {(result.stderr or result.stdout).strip()}")
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--publish", action="store_true", help="git commit and push docs/summaries after building")
    args = p.parse_args()

    count = build_manifest()
    print(f"Wrote {SUMMARIES_DIR / 'index.json'} ({count} meeting(s))", file=sys.stderr)
    stamped = stamp_assets()
    if stamped:
        print(f"Updated asset versions in {', '.join(stamped)} (commit these)", file=sys.stderr)
    if args.publish:
        print("pushed" if publish() else "nothing to publish", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
