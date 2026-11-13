"""Build the GitHub Pages manifest from saved summaries, and optionally publish it.

The site in docs/ is static HTML + JavaScript. The browser loads docs/summaries/index.json
(written here) to list meetings, then fetches each summary's JSON as-is and renders it.
docs/summaries/search.json (also written here) holds topic summary text for search.
docs/summaries/models.json (written by summarize.py) says which model wrote each summary.
Only summaries are published; full transcripts stay on the host machine.

Usage:
    python build_site.py              # rewrite docs/summaries/index.json
    python build_site.py --publish    # ...then git commit and push docs/summaries
"""

import argparse
import json
import re
import subprocess
import sys
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
    for f in summaries_dir.glob("*/*.json"):
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
    return len(meetings)


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def publish(message="Update meeting summaries"):
    """Commit docs/summaries and push. Returns True if something was pushed."""
    git("add", "--", str(SUMMARIES_DIR.relative_to(ROOT)))
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
    if args.publish:
        print("pushed" if publish() else "nothing to publish", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
