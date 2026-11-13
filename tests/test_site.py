"""Tests for build_site.py, council_transcripts.py and date parsing: no network needed."""

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import build_site as b
from council_transcripts import fmt_ts, json3_to_lines, parse_title_date, slugify


def summary(*topics, tags=("budget",)):
    return {"Title": "Meeting", "Tags": list(tags), "Topics": [{f"Topic{i}": t} for i, t in enumerate(topics, 1)]}


def topic(title, stamps, tags, text="Something happened."):
    return {"Title": title, "Summary": text, "related_timestamps": stamps, "Tags": tags}


class Spans(unittest.TestCase):
    def test_topic_span(self):
        self.assertEqual(b.topic_span([100, 400], None), (100, 400))
        self.assertEqual(b.topic_span([100], 130), (100, 130))      # runs to the next topic
        self.assertEqual(b.topic_span([100], None), (100, 160))     # last topic: the minimum
        self.assertEqual(b.topic_span([100], 5000), (100, 160))     # capped at the minimum
        self.assertEqual(b.topic_span([], 5), (None, None))

    def test_credit_tags_sums_to_duration_and_favors_first(self):
        credit = b.credit_tags(["zoning", "housing", "public-comment"], 3600)
        self.assertAlmostEqual(sum(credit.values()), 3600)
        self.assertGreater(credit["zoning"], credit["housing"])
        self.assertGreater(credit["housing"], credit["public-comment"])
        self.assertEqual(b.credit_tags(["only"], 90), {"only": 90})
        self.assertEqual(b.credit_tags([], 90), {})

    def test_meeting_entry(self):
        entry = b.meeting_entry(summary(topic("A", [10, 310], ["police", "budget"]), topic("B", ["320.5"], ["police"])))
        self.assertEqual([t["title"] for t in entry["topics"]], ["A", "B"])
        self.assertEqual(entry["topics"][0]["start"], 10)
        self.assertEqual(entry["topics"][1]["end"], 380.5)
        self.assertEqual(list(entry["tag_seconds"])[0], "police")   # most discussed first
        self.assertAlmostEqual(sum(entry["tag_seconds"].values()), 300 + 60, delta=1)

    def test_both_topic_shapes_and_bad_timestamps(self):
        one_object = {"Title": "m", "Topics": [{"Topic2": topic("B", [50], []), "Topic1": topic("A", ["x", 5], [])}]}
        self.assertEqual([t[0] for t in b.topics_of(one_object)], ["A", "B"])
        self.assertEqual(b.topics_of(one_object)[0][2], [5.0])


class Manifest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        city = self.dir / "summaries" / "boulder"
        city.mkdir(parents=True)
        (city / "2026-09-17_ab_c-D1.json").write_text(json.dumps(summary(topic("Budget <vote>", [0, 600], ["budget"], "Passed 9-0 & done."))))
        (city / "2026-10-01_xyz.json").write_text(json.dumps(summary(topic("Zoning", [5, 65], ["zoning"]))))
        (city / "undated_q_1.json").write_text(json.dumps(summary(topic("Old", [1], []))))
        (city / "notes.json").write_text("{}")  # not a meeting file: ignored
        (self.dir / "summaries" / "models.json").write_text(json.dumps({"boulder/2026-10-01_xyz": "Test Model"}))
        self.count = b.build_manifest(self.dir / "summaries")
        self.manifest = json.loads((self.dir / "summaries" / "index.json").read_text())

    def test_meetings_listed_newest_first_with_ids_split_on_first_underscore(self):
        self.assertEqual(self.count, 3)
        self.assertEqual([m["id"] for m in self.manifest["meetings"]], ["2026-10-01_xyz", "2026-09-17_ab_c-D1", "undated_q_1"])
        m = self.manifest["meetings"][1]
        self.assertEqual((m["date"], m["video_id"]), ("2026-09-17", "ab_c-D1"))
        self.assertIsNone(self.manifest["meetings"][2]["date"])
        self.assertEqual(self.manifest["meetings"][0]["model"], "Test Model")

    def test_vocabulary_and_search_text(self):
        self.assertTrue(any(t["id"] == "police" and t["description"] for t in self.manifest["tags"]))
        search = json.loads((self.dir / "summaries" / "search.json").read_text())
        self.assertEqual(search["boulder/2026-09-17_ab_c-D1"], ["Passed 9-0 & done."])

    def test_feed_is_well_formed_and_escaped(self):
        root = ET.parse(self.dir / "feed.xml").getroot()
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entries = root.findall("a:entry", ns)
        self.assertEqual(len(entries), 2, "undated meetings are left out of the feed")
        self.assertIn("meeting.html?city=boulder&id=2026-10-01_xyz", entries[0].find("a:link", ns).get("href"))
        self.assertIn("Budget &lt;vote&gt;", entries[1].find("a:content", ns).text)


class Transcripts(unittest.TestCase):
    def test_parse_title_date(self):
        self.assertEqual(parse_title_date("September 17, 2026 City of Boulder City Council Meeting"), date(2026, 9, 17))
        self.assertEqual(parse_title_date("Boulder City Council Meeting 6-22-23"), date(2023, 6, 22))
        self.assertEqual(parse_title_date("Sept. 3, 2026 City Council"), date(2026, 9, 3))
        self.assertIsNone(parse_title_date("Smarch 3, 2026 City Council"))
        self.assertEqual(parse_title_date("Oct 1, 2026 City Council Study Session"), date(2026, 10, 1))
        self.assertIsNone(parse_title_date("City Council Candidate Forum"))

    def test_slugify_and_timestamps(self):
        self.assertEqual(slugify("Boulder"), "boulder")
        self.assertEqual(slugify("Fort Collins, CO"), "fort-collins-co")
        self.assertEqual(fmt_ts(3725.9), "01:02:05")

    def test_json3_to_lines(self):
        data = {"events": [{"tStartMs": 1500, "segs": [{"utf8": "hello "}, {"utf8": "there\n"}]}, {"tStartMs": 3000},
                           {"tStartMs": 4000, "segs": [{"utf8": "\n"}]}]}
        self.assertEqual(json3_to_lines(data), [(1.5, "hello there")])


if __name__ == "__main__":
    unittest.main()
