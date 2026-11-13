"""Tests for summarize.py that need no API access."""

import json
import unittest

import summarize as s


def topic(**over):
    t = {"Title": "Budget hearing", "Summary": "Council held a hearing.", "related_timestamps": [10.0, 300.0], "Tags": ["budget"]}
    t.update(over)
    return t


class ParseReply(unittest.TestCase):
    def test_plain_fenced_and_wrapped(self):
        obj = {"Title": "x", "Topics": []}
        for text in (json.dumps(obj), "```json\n" + json.dumps(obj) + "\n```", "Here you go:\n" + json.dumps(obj) + "\nDone."):
            self.assertEqual(s.parse_reply(text), obj)

    def test_not_json(self):
        with self.assertRaises(s.SummaryError):
            s.parse_reply("no json here")
        with self.assertRaises(s.SummaryError):
            s.parse_reply('{"Title": "unterminated}')


class Normalize(unittest.TestCase):
    def test_renumbers_and_flattens_both_shapes(self):
        one_object = {"Title": "m", "Topics": [{"Topic2": topic(Title="B"), "Topic1": topic(Title="A")}]}
        out, _ = s.normalize_summary(one_object)
        self.assertEqual([list(e)[0] for e in out["Topics"]], ["Topic1", "Topic2"])
        self.assertEqual([list(e.values())[0]["Title"] for e in out["Topics"]], ["A", "B"])

        skipped = {"Title": "m", "Topics": [{"Topic3": topic(Title="A")}, {"Topic7": topic(Title="B")}]}
        out, _ = s.normalize_summary(skipped)
        self.assertEqual([list(e)[0] for e in out["Topics"]], ["Topic1", "Topic2"])

    def test_drops_unknown_and_duplicate_tags(self):
        summary = {"Title": "m", "Tags": ["budget", "nope", "budget"],
                   "Topics": [{"Topic1": topic(Tags=["police", "made-up", "police"])}]}
        out, warnings = s.normalize_summary(summary, ["budget", "police"])
        self.assertEqual(out["Tags"], ["budget"])
        self.assertEqual(out["Topics"][0]["Topic1"]["Tags"], ["police"])
        self.assertEqual(len(warnings), 2)

    def test_keyword_rules_add_missing_tags_once(self):
        import re
        keywords = {"flock": re.compile(r"\b(?:flock|license plate reader)\b", re.IGNORECASE)}
        summary = {"Title": "m", "Tags": [], "Topics": [
            {"Topic1": topic(Summary="Staff paused the Flock contract.", Tags=["police"])},
            {"Topic2": topic(Summary="A flocking behavior study of geese.", Tags=[])},
            {"Topic3": topic(Title="License plate reader policy", Tags=["flock"])}]}
        out, notes = s.normalize_summary(summary, ["police", "flock"], keywords)
        tags = [list(e.values())[0]["Tags"] for e in out["Topics"]]
        self.assertEqual(tags, [["police", "flock"], [], ["flock"]])   # whole words only; no duplicates
        self.assertEqual(len(notes), 1)
        self.assertEqual(s.normalize_summary(out, ["police", "flock"], keywords)[1], [])  # idempotent

    def test_configured_keywords_compile(self):
        patterns = s.tag_keywords(s.load_config())
        self.assertIn("flock", patterns)
        self.assertTrue(patterns["flock"].search("the ALPR pilot"))

    def test_leaves_malformed_input_alone(self):
        for bad in ("text", {"Title": "x"}, {"Title": "x", "Topics": ["not an object"]}):
            out, warnings = s.normalize_summary(bad)
            self.assertIs(out, bad)
            self.assertEqual(warnings, [])


class Validate(unittest.TestCase):
    def good(self):
        return {"Title": "Meeting", "Tags": ["budget"], "Topics": [{"Topic1": topic()}, {"Topic2": topic(Title="Other")}]}

    def test_valid_summary(self):
        errors, warnings = s.validate_summary(self.good(), {10.0, 300.0}, known_tags=["budget"])
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_tags_are_optional_for_old_summaries(self):
        old = {"Title": "Meeting", "Topics": [{"Topic1": {"Title": "A", "Summary": "B.", "related_timestamps": ["12.5"]}}]}
        self.assertEqual(s.validate_summary(old, known_tags=["budget"])[0], [])

    def test_structure_errors(self):
        cases = {
            "top level": [],
            "Title": {"Topics": [{"Topic1": topic()}]},
            "Topics": {"Title": "x", "Topics": []},
            "unexpected top-level": {"Title": "x", "Topics": [{"Topic1": topic()}], "Extra": 1},
            "key must look like": {"Title": "x", "Topics": [{"Subject1": topic()}]},
            "unexpected keys": {"Title": "x", "Topics": [{"Topic1": topic(Speaker="a")}]},
            "Summary": {"Title": "x", "Topics": [{"Topic1": topic(Summary="")}]},
            "related_timestamps": {"Title": "x", "Topics": [{"Topic1": topic(related_timestamps=[])}]},
            "not a number": {"Title": "x", "Topics": [{"Topic1": topic(related_timestamps=["soon"])}]},
            "unknown tags": {"Title": "x", "Topics": [{"Topic1": topic(Tags=["nope"])}]},
            "list of strings": {"Title": "x", "Tags": "budget", "Topics": [{"Topic1": topic()}]},
        }
        for expected, summary in cases.items():
            errors, _ = s.validate_summary(summary, known_tags=["budget"])
            self.assertTrue(any(expected in e for e in errors), f"{expected!r} not reported: {errors}")

    def test_timestamp_range_and_exactness(self):
        past_end = {"Title": "x", "Topics": [{"Topic1": topic(related_timestamps=[99999])}]}
        self.assertTrue(any("past the end" in e for e in s.validate_summary(past_end, {10.0, 300.0})[0]))
        inexact = {"Title": "x", "Topics": [{"Topic1": topic(related_timestamps=[11.0])}]}
        errors, warnings = s.validate_summary(inexact, {10.0, 300.0})
        self.assertEqual(errors, [])
        self.assertTrue(any("exact segment start" in w for w in warnings))
        errors, _ = s.validate_summary(inexact, {10.0, 300.0}, exact_timestamps=True)
        self.assertTrue(errors)

    def test_long_summary_warns(self):
        long = {"Title": "x", "Topics": [{"Topic1": topic(Summary="One. Two. Three. Four. Five. Six.")}]}
        errors, warnings = s.validate_summary(long)
        self.assertEqual(errors, [])
        self.assertTrue(any("longer than 5 sentences" in w for w in warnings))


class ConfigAndSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = s.load_config()

    def test_config_loads_and_models_resolve(self):
        for name in self.config["summarize"]["models"]:
            model = s.model_settings(self.config, name)
            self.assertIn(model["provider_config"]["type"], s.PROVIDER_TYPES)

    def test_prompt_has_format_and_tags_filled_in(self):
        _, prompt = s.build_prompt(self.config, s.model_settings(self.config, self.config["summarize"]["models"][0]))
        self.assertNotIn("{format}", prompt)
        self.assertNotIn("{tags}", prompt)
        self.assertIn('"Topic1"', prompt)
        self.assertIn("- police:", prompt)

    def test_schema_obeys_structured_output_limits(self):
        schema = s.build_schema(self.config)
        ids = s.tag_ids(self.config)
        self.assertEqual(schema["$defs"]["tagList"]["items"]["enum"], ids)
        branches = schema["properties"]["Topics"]["items"]["anyOf"]
        self.assertEqual(len(branches), self.config["summarize"]["max_topics"])
        self.assertLessEqual(len(branches), 30, "40 topic branches exceed the API's grammar size limit")

        def objects(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    yield node
                for v in node.values():
                    yield from objects(v)
            elif isinstance(node, list):
                for v in node:
                    yield from objects(v)

        for obj in objects(schema):
            self.assertIs(obj.get("additionalProperties"), False)
            self.assertEqual(sorted(obj["required"]), sorted(obj["properties"]), "optional properties are capped at 24")

    def test_tag_vocabulary_is_consistent(self):
        vocab = s.load_tags(self.config)
        groups = [g["id"] for g in vocab["groups"]]
        self.assertLessEqual(len(groups), 8, "the site has 8 validated group colors")
        ids = [t["id"] for t in vocab["tags"]]
        self.assertEqual(len(ids), len(set(ids)))
        for t in vocab["tags"]:
            self.assertIn(t["group"], groups)
            self.assertRegex(t["id"], r"^[a-z0-9-]+$")
            self.assertTrue(t["label"] and t["description"])

    def test_render_transcript_formats(self):
        tr = {"title": "T", "url": "u", "segments": [{"start": 1.5, "text": "hello"}]}
        self.assertEqual(json.loads(s.render_transcript(tr, "json")), tr)
        self.assertIn("1.5\thello", s.render_transcript(tr, "lines"))


if __name__ == "__main__":
    unittest.main()
