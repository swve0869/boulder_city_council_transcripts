"""Summarize saved council transcripts with the models configured in summarizer.toml.

For each transcript, the models listed under [summarize] models are tried in order. Each
gets the configured prompt (with summary_prompt_JSON_format.json inserted) plus the
transcript; the reply must pass a structure check before it is saved to
docs/summaries/<city-slug>/<stem>.json for the GitHub Pages site. Invalid replies are
retried, then the next model is tried. Nothing is written unless a reply passes.

Providers can be Anthropic or any OpenAI-compatible API (OpenAI, DeepSeek, Gemini,
OpenRouter, Ollama, ...). API keys come from the environment variable each provider names.

Usage:
    python summarize.py --city Boulder --limit 1        # newest Boulder transcripts lacking a summary
    python summarize.py --city Boulder --model deepseek --limit 1
    python summarize.py transcripts/boulder/2026-09-17_SS0nUFcZFq0.json --overwrite
    python summarize.py --city Boulder --missing-tags --jobs 4  # add tags to older summaries
    python summarize.py --check docs/summaries/boulder/*.json     # validate only, no API calls
    python summarize.py --list-models
"""

import argparse
import json
import math
import os
import re
import sys
import threading
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).parent
CONFIG_FILE = ROOT / "summarizer.toml"
PROVENANCE_FILE = "models.json"  # in output_dir: {"<city>/<stem>": "<model label>"}

MODEL_DEFAULT_KEYS = ("prompt", "max_attempts", "transcript_format", "max_input_chars", "timeout_seconds",
                      "structured_output")
PROVIDER_TYPES = ("anthropic", "openai")
TOPIC_KEY = re.compile(r"^Topic\d+$")
SENTENCE_END = re.compile(r"[.!?](?:\s|$)")
NON_RETRYABLE_STATUS = {400, 401, 403, 404, 422}


class SummaryError(Exception):
    pass


# ---- Configuration --------------------------------------------------------------------

def load_config(path=CONFIG_FILE):
    """Read summarizer.toml and check that it is internally consistent."""
    with open(path, "rb") as f:
        config = tomllib.load(f)
    settings = config.get("summarize", {})
    providers, models, prompts = (config.get(k, {}) for k in ("providers", "models", "prompts"))
    problems = []
    for name, provider in providers.items():
        if provider.get("type") not in PROVIDER_TYPES:
            problems.append(f"providers.{name}: type must be one of {PROVIDER_TYPES}")
    for name, model in models.items():
        if model.get("provider") not in providers:
            problems.append(f"models.{name}: unknown provider {model.get('provider')!r}")
        if not model.get("model"):
            problems.append(f"models.{name}: missing model ID")
        prompt = model.get("prompt", settings.get("prompt", "default"))
        if prompt not in prompts:
            problems.append(f"models.{name}: unknown prompt {prompt!r}")
        fmt = model.get("transcript_format", settings.get("transcript_format", "json"))
        if fmt not in ("json", "lines"):
            problems.append(f"models.{name}: transcript_format must be 'json' or 'lines'")
    for name in settings.get("models", []):
        if name not in models:
            problems.append(f"summarize.models: unknown model {name!r}")
    for name, prompt in prompts.items():
        if "{format}" not in prompt.get("text", ""):
            problems.append(f"prompts.{name}: text must contain {{format}}")
    if problems:
        raise SystemExit(f"{Path(path).name} has problems:\n  " + "\n  ".join(problems))
    return config


def model_settings(config, name):
    """Merged settings for one model: [summarize] defaults, then the [models.<name>] entry."""
    settings = config.get("summarize", {})
    model = {k: settings[k] for k in MODEL_DEFAULT_KEYS if k in settings}
    model.update(config["models"][name])
    model.setdefault("prompt", "default")
    model.setdefault("max_attempts", 3)
    model.setdefault("transcript_format", "json")
    model.setdefault("max_input_chars", 0)
    model.setdefault("timeout_seconds", 900)
    model.setdefault("structured_output", True)
    model.setdefault("label", model["model"])
    model["name"] = name
    model["provider_config"] = config["providers"][model["provider"]]
    return model


def output_dir(config):
    return ROOT / config.get("summarize", {}).get("output_dir", "docs/summaries")


def summary_path(transcript_path, city_slug, out_root):
    return Path(out_root) / city_slug / f"{Path(transcript_path).stem}.json"


# ---- Tags, prompt, schema -------------------------------------------------------------

def load_tags(config):
    """The tag vocabulary from tags.json: {"groups": [...], "tags": [{id, label, group, description}]}."""
    path = ROOT / config.get("summarize", {}).get("tags_file", "tags.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"groups": [], "tags": []}


def tag_ids(config):
    return [t["id"] for t in load_tags(config)["tags"]]


def tag_keywords(config):
    """{tag id: compiled whole-word regex} for tags that list keywords in tags.json."""
    return {t["id"]: re.compile(r"\b(?:" + "|".join(re.escape(k) for k in t["keywords"]) + r")\b", re.IGNORECASE)
            for t in load_tags(config)["tags"] if t.get("keywords")}


def build_prompt(config, model):
    fmt_file = ROOT / config.get("summarize", {}).get("format_file", "summary_prompt_JSON_format.json")
    fmt = json.dumps(json.loads(fmt_file.read_text(encoding="utf-8")))
    tags = "\n".join(f"- {t['id']}: {t['description']}" for t in load_tags(config)["tags"])
    prompt = config["prompts"][model["prompt"]]
    # str.replace, not str.format: the JSON format itself is full of braces.
    return prompt.get("system", ""), prompt["text"].replace("{format}", fmt).replace("{tags}", tags)


def build_schema(config):
    """JSON schema for structured outputs that matches summary_prompt_JSON_format.json.

    Structured outputs need additionalProperties: false on every object and allow at most
    24 optional properties, so "an object keyed Topic<N>" is written as: each Topics item is
    anyOf N single-key objects {"Topic1": topic} ... {"TopicN": topic}. Tags are an enum, so
    the model can only use tags from tags.json. The compiled grammar has a size limit: with
    38 tags, N=30 compiles and N=40 is rejected ("compiled grammar is too large").
    """
    ids = tag_ids(config)
    tag_list = {"type": "array", "items": {"type": "string", "enum": ids}} if ids else {"type": "array", "items": {"type": "string"}}
    topic = {
        "type": "object", "additionalProperties": False,
        "required": ["Title", "Summary", "related_timestamps", "Tags"],
        "properties": {
            "Title": {"type": "string"},
            "Summary": {"type": "string"},
            "related_timestamps": {"type": "array", "items": {"type": "number"}},
            "Tags": {"$ref": "#/$defs/tagList"},
        },
    }
    n = config.get("summarize", {}).get("max_topics", 30)
    return {
        "type": "object", "additionalProperties": False,
        "required": ["Title", "Overview", "Tags", "Topics"],
        "$defs": {"topic": topic, "tagList": tag_list},
        "properties": {
            "Title": {"type": "string"},
            "Overview": {"type": "string"},
            "Tags": {"$ref": "#/$defs/tagList"},
            "Topics": {"type": "array", "items": {"anyOf": [
                {"type": "object", "additionalProperties": False, "required": [f"Topic{i}"],
                 "properties": {f"Topic{i}": {"$ref": "#/$defs/topic"}}}
                for i in range(1, n + 1)
            ]}},
        },
    }


def render_transcript(transcript, fmt):
    if fmt == "lines":
        head = f"{transcript.get('title', '')}\n{transcript.get('url', '')}\nstart\ttext\n"
        return head + "\n".join(f"{seg['start']}\t{seg['text']}" for seg in transcript["segments"])
    return json.dumps(transcript, separators=(",", ":"), ensure_ascii=False)


# ---- Token usage ----------------------------------------------------------------------

USAGE_LOG = ROOT / "summarize_usage.log"   # gitignored (*.log); one line per API request
_usage_lock = threading.Lock()
_usage_last = threading.local()            # the calling thread's most recent request
usage_totals = {"requests": 0, "input": 0, "output": 0, "cost": 0.0}


def estimate_cost(model, input_tokens, output_tokens):
    """Approximate dollars for one request from the model's optional `price` table ($ per million tokens).

    `long_threshold` switches to the `long_*` rates for prompts above that size (Haiku 5.5's
    pricing does this at 100K). Cache discounts are ignored, so this is an upper bound.
    """
    price = model.get("price")
    if not price:
        return None
    long = price.get("long_threshold") and input_tokens > price["long_threshold"]
    rate_in = price.get("long_input", price["input"]) if long else price["input"]
    rate_out = price.get("long_output", price["output"]) if long else price["output"]
    return (input_tokens * rate_in + output_tokens * rate_out) / 1_000_000


def record_usage(model, input_tokens, output_tokens, cached=0):
    """Note one request's token counts: keep them for the caller's log line, the run total and the usage log."""
    cost = estimate_cost(model, input_tokens, output_tokens)
    _usage_last.value = {"input": input_tokens, "output": output_tokens, "cached": cached, "cost": cost}
    with _usage_lock:
        usage_totals["requests"] += 1
        usage_totals["input"] += input_tokens
        usage_totals["output"] += output_tokens
        usage_totals["cost"] += cost or 0
        try:
            from datetime import datetime
            with USAGE_LOG.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{model['model']}\tin={input_tokens}\tcached={cached}\t"
                        f"out={output_tokens}\tcost={'' if cost is None else f'{cost:.4f}'}\n")
        except OSError:
            pass  # usage logging must never fail a summary


def usage_note():
    """'12,345 tokens in, 2,100 out, about $0.012' for this thread's last request ('' if unknown)."""
    u = getattr(_usage_last, "value", None)
    if not u:
        return ""
    note = f"{u['input']:,} tokens in, {u['output']:,} out"
    return note + (f", about ${u['cost']:.3f}" if u["cost"] is not None else "")


# ---- Providers ------------------------------------------------------------------------

_clients = {}


def api_key(provider):
    env = provider.get("api_key_env", "")
    if not env:
        return None
    key = os.environ.get(env, "").strip()
    if not key:
        raise SummaryError(f"environment variable {env} is not set")
    return key


def get_client(model):
    provider = model["provider_config"]
    cache_key = (model["provider"], model["timeout_seconds"])
    if cache_key not in _clients:
        if provider["type"] == "anthropic":
            import anthropic
            _clients[cache_key] = anthropic.Anthropic(
                api_key=api_key(provider), base_url=provider.get("base_url"), timeout=model["timeout_seconds"])
        else:
            import openai
            _clients[cache_key] = openai.OpenAI(
                api_key=api_key(provider) or "not-needed", base_url=provider.get("base_url"),
                timeout=model["timeout_seconds"])
    return _clients[cache_key]


def call_anthropic(model, system, prompt, transcript_text, schema=None):
    transcript_block = {"type": "text", "text": transcript_text}
    if model.get("prompt_cache", True):
        # Cached so retries after an invalid reply don't pay full price for the transcript again.
        transcript_block["cache_control"] = {"type": "ephemeral"}
    kwargs = {
        "model": model["model"],
        "max_tokens": model.get("max_tokens", 64000),
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}, transcript_block]}],
        **model.get("params", {}),
    }
    if system:
        kwargs["system"] = system
    if schema is not None:
        # Merge with any output_config from params (e.g. effort).
        kwargs["output_config"] = {**kwargs.get("output_config", {}),
                                   "format": {"type": "json_schema", "schema": schema}}
    if model.get("fallbacks"):
        # On a safety decline, the API retries the request on a fallback model.
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = model["fallbacks"]

    with get_client(model).beta.messages.stream(**kwargs) as stream:
        message = stream.get_final_message()
    usage = message.usage
    cached = (getattr(usage, "cache_read_input_tokens", 0) or 0)
    record_usage(model, usage.input_tokens + cached + (getattr(usage, "cache_creation_input_tokens", 0) or 0),
                 usage.output_tokens, cached)
    if message.stop_reason == "refusal":
        raise SummaryError("request was declined (stop_reason=refusal)")
    if message.stop_reason == "max_tokens":
        raise SummaryError("reply was cut off at max_tokens")
    return "".join(b.text for b in message.content if b.type == "text")


def call_openai(model, system, prompt, transcript_text, schema=None):
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": f"{prompt}\n\n{transcript_text}"})
    kwargs = {"model": model["model"], "messages": messages, **model.get("params", {})}
    if model.get("json_mode"):
        kwargs["response_format"] = {"type": "json_object"}
    response = get_client(model).chat.completions.create(**kwargs)
    if getattr(response, "usage", None):
        record_usage(model, response.usage.prompt_tokens or 0, response.usage.completion_tokens or 0)
    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise SummaryError("reply was cut off at the output token limit")
    if choice.finish_reason == "content_filter":
        raise SummaryError("reply was blocked by the provider's content filter")
    return choice.message.content or ""


CALLERS = {"anthropic": call_anthropic, "openai": call_openai}


def request_summary(model, system, prompt, transcript_text, schema=None):
    """One API call; returns the reply text. schema (anthropic only) constrains the reply."""
    text = CALLERS[model["provider_config"]["type"]](model, system, prompt, transcript_text, schema)
    if not text.strip():
        raise SummaryError("empty reply")
    return text


def retryable(exc):
    """False for errors a retry can't fix (bad key, unknown model, bad request)."""
    if isinstance(exc, ImportError):
        return False
    if isinstance(exc, SummaryError):
        return "not set" not in str(exc)
    return getattr(exc, "status_code", None) not in NON_RETRYABLE_STATUS


# ---- Validation -----------------------------------------------------------------------

def parse_reply(text):
    """JSON object from the model's reply; tolerates a ```json fence or stray text around it."""
    text = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise SummaryError("reply is not JSON")
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError as e:
            raise SummaryError(f"reply is not valid JSON: {e}") from None


def normalize_summary(summary, known_tags=None, keywords=None):
    """Tidy a parsed reply before validation; returns (summary, warnings).

    keywords ({tag: regex}, from tags.json) adds a tag to any topic whose title or summary
    mentions it but that the model left untagged: precise terms like "Flock" shouldn't
    depend on the model remembering the tag.

    - Topics become one object per list item, numbered Topic1, Topic2, ... in reply order
      (the model may use either shape, or skip numbers).
    - Tags are de-duplicated; tags not in the vocabulary are dropped with a warning
      (only possible for providers without schema enforcement).
    Leaves anything it doesn't understand alone for validate_summary to report.
    """
    warnings = []
    if not isinstance(summary, dict) or not isinstance(summary.get("Topics"), list):
        return summary, warnings
    known = set(known_tags or [])

    def clean(tags, where):
        if not isinstance(tags, list):
            return tags
        out = []
        for t in tags:
            if known and t not in known:
                warnings.append(f"{where}: dropped unknown tag {t!r}")
            elif t not in out:
                out.append(t)
        return out

    topics = []
    for entry in summary["Topics"]:
        if not isinstance(entry, dict):
            return summary, warnings
        topics.extend(entry[k] for k in sorted(entry, key=lambda k: int(k[5:]) if TOPIC_KEY.match(k) else 0))
    for i, topic in enumerate(topics, 1):
        if isinstance(topic, dict) and "Tags" in topic:
            topic["Tags"] = clean(topic["Tags"], f"Topic{i}")
        if isinstance(topic, dict) and isinstance(topic.get("Tags"), list) and keywords:
            text = f"{topic.get('Title', '')} {topic.get('Summary', '')}"
            for tag, pattern in keywords.items():
                if tag not in topic["Tags"] and pattern.search(text):
                    topic["Tags"].append(tag)
                    warnings.append(f"Topic{i}: added tag {tag!r} (keyword match)")
    summary["Topics"] = [{f"Topic{i}": topic} for i, topic in enumerate(topics, 1)]
    if "Tags" in summary:
        summary["Tags"] = clean(summary["Tags"], "Tags")
    return summary, warnings


def check_tags(tags, where, known, errors):
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        errors.append(f'{where}: "Tags" must be a list of strings')
    elif known:
        unknown = [t for t in tags if t not in known]
        if unknown:
            errors.append(f"{where}: unknown tags {unknown} (not in tags.json)")


def validate_summary(summary, segment_starts=None, max_sentences=5, exact_timestamps=False, known_tags=None):
    """Return (errors, warnings). Errors mean the structure doesn't match summary_prompt_JSON_format.json.

    "Overview" and "Tags" (meeting level and per topic) are optional so older summaries
    still validate; when present, tags must come from known_tags.
    """
    errors, warnings = [], []
    known = set(known_tags or [])
    if not isinstance(summary, dict):
        return ["top level is not a JSON object"], warnings
    if "Tags" in summary:
        check_tags(summary["Tags"], "Tags", known, errors)
    if "Overview" in summary and (not isinstance(summary["Overview"], str) or not summary["Overview"].strip()):
        errors.append('"Overview" must be a non-empty string')
    extra = set(summary) - {"Title", "Overview", "Topics", "Tags"}
    if extra:
        errors.append(f"unexpected top-level keys: {sorted(extra)}")
    if not isinstance(summary.get("Title"), str) or not summary["Title"].strip():
        errors.append('"Title" must be a non-empty string')
    topics = summary.get("Topics")
    if not isinstance(topics, list) or not topics:
        errors.append('"Topics" must be a non-empty list')
        return errors, warnings

    last_start = max(segment_starts) if segment_starts else None
    count = 0
    for i, entry in enumerate(topics):
        if not isinstance(entry, dict) or not entry:
            errors.append(f"Topics[{i}] must be a non-empty object")
            continue
        for key, topic in entry.items():
            where = f"Topics[{i}].{key}"
            if not TOPIC_KEY.match(key):
                errors.append(f'{where}: key must look like "Topic1", "Topic2", ...')
                continue
            if not isinstance(topic, dict):
                errors.append(f"{where} must be an object")
                continue
            count += 1
            extra = set(topic) - {"Title", "Summary", "related_timestamps", "Tags"}
            if extra:
                errors.append(f"{where}: unexpected keys {sorted(extra)}")
            if "Tags" in topic:
                check_tags(topic["Tags"], where, known, errors)
            for field in ("Title", "Summary"):
                if not isinstance(topic.get(field), str) or not topic[field].strip():
                    errors.append(f'{where}: "{field}" must be a non-empty string')
            stamps = topic.get("related_timestamps")
            if not isinstance(stamps, list) or not stamps:
                errors.append(f'{where}: "related_timestamps" must be a non-empty list')
                continue
            for t in stamps:
                try:
                    value = float(t)
                except (TypeError, ValueError):
                    errors.append(f"{where}: timestamp {t!r} is not a number")
                    continue
                if isinstance(t, bool) or not math.isfinite(value) or value < 0:
                    errors.append(f"{where}: timestamp {t!r} is not a valid time")
                elif last_start is not None and value > last_start + 1:
                    errors.append(f"{where}: timestamp {t!r} is past the end of the transcript")
                elif segment_starts and value not in segment_starts:
                    (errors if exact_timestamps else warnings).append(
                        f"{where}: timestamp {t!r} is not an exact segment start")
            if isinstance(topic.get("Summary"), str) and len(SENTENCE_END.findall(topic["Summary"])) > max_sentences:
                warnings.append(f"{where}: summary looks longer than {max_sentences} sentences")
    if not count:
        errors.append("no topics found")
    return errors, warnings


# ---- Overviews for existing summaries -------------------------------------------------

OVERVIEW_PROMPT = (
    "Below are the topic summaries of one city council meeting. Write an overview of the meeting in 2 to 3 "
    "sentences: what it mainly covered and what was decided. If nothing was decided, describe what was "
    "discussed. Write about the meeting itself; never mention the summaries or this task. Plain text only, "
    "no preamble, no markdown."
)


def add_overview(path, model, log=print):
    """Add "Overview" to a saved summary from its topic summaries (a small, cheap request).

    New summaries get an Overview in the main call; this backfills ones made before that.
    Returns True if the file was updated.
    """
    summary = json.loads(Path(path).read_text(encoding="utf-8"))
    if summary.get("Overview") or not isinstance(summary.get("Topics"), list):
        return False
    lines = [f"Meeting: {summary.get('Title', '')}"]
    for entry in summary["Topics"]:
        for topic in entry.values():
            lines.append(f"- {topic.get('Title', '')}: {topic.get('Summary', '')}")
    text = request_summary(model, "", OVERVIEW_PROMPT, "\n".join(lines)).strip()
    if not text or len(text) > 1200:
        raise SummaryError(f"unusable overview ({len(text)} chars)")
    ordered = {"Title": summary.get("Title"), "Overview": text}
    ordered.update({k: v for k, v in summary.items() if k not in ordered})
    Path(path).write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"overview {Path(path).parent.name}/{Path(path).stem}")
    return True


# ---- Summarizing ----------------------------------------------------------------------

_provenance_lock = threading.Lock()  # --jobs runs summaries in parallel threads


def record_provenance(out_root, city_slug, stem, label):
    path = Path(out_root) / PROVENANCE_FILE
    with _provenance_lock:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data[f"{city_slug}/{stem}"] = label
        path.write_text(json.dumps(dict(sorted(data.items())), indent=1) + "\n", encoding="utf-8")


def summarize_with_model(model, config, transcript, starts, log):
    """Try one model up to its max_attempts. Returns a valid summary dict or raises SummaryError."""
    rules = config.get("summarize", {}).get("validation", {})
    system, prompt = build_prompt(config, model)
    transcript_text = render_transcript(transcript, model["transcript_format"])
    known, keywords = tag_ids(config), tag_keywords(config)
    structured = model["structured_output"] and model["provider_config"]["type"] == "anthropic"
    schema = build_schema(config) if structured else None
    limit = model["max_input_chars"]
    if limit and len(transcript_text) > limit:
        raise SummaryError(f"transcript is {len(transcript_text):,} chars, over this model's "
                           f"max_input_chars ({limit:,})")

    problems = []
    for attempt in range(1, model["max_attempts"] + 1):
        try:
            summary, notes = normalize_summary(
                parse_reply(request_summary(model, system, prompt, transcript_text, schema)), known, keywords)
            errors, warnings = validate_summary(
                summary, starts, rules.get("max_summary_sentences", 5), rules.get("exact_timestamps_required", False),
                known)
            warnings = notes + warnings
        except Exception as e:
            if not retryable(e):
                raise SummaryError(f"{type(e).__name__}: {e}") from None
            errors, warnings = [f"{type(e).__name__}: {e}"], []
        if not errors:
            for w in warnings:
                log(f"warn   {w}")
            return summary
        problems = errors
        log(f"retry  {model['name']} attempt {attempt}/{model['max_attempts']} failed: {'; '.join(errors[:3])}")
    raise SummaryError(f"no valid summary after {model['max_attempts']} attempts: {'; '.join(problems[:3])}")


def summarize_transcript(transcript_path, city_slug, config=None, models=None, log=print):
    """Summarize one transcript with the configured models (first valid reply wins).

    Saves the summary to <output_dir>/<city>/<stem>.json and returns that path.
    """
    config = config or load_config()
    models = models or config.get("summarize", {}).get("models", [])
    if not models:
        raise SummaryError("no models configured ([summarize] models in summarizer.toml)")
    transcript_path = Path(transcript_path)
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    if not transcript.get("segments"):
        raise SummaryError(f"{transcript_path.name} has no segments")
    starts = {seg["start"] for seg in transcript["segments"]}

    failures = []
    for name in models:
        model = model_settings(config, name)
        try:
            summary = summarize_with_model(model, config, transcript, starts,
                                           lambda m: log(f"{transcript_path.stem}: {m}"))
        except SummaryError as e:
            log(f"{transcript_path.stem}: {name} failed: {e}")
            failures.append(f"{name}: {e}")
            continue
        out_root = output_dir(config)
        out = summary_path(transcript_path, city_slug, out_root)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        record_provenance(out_root, city_slug, transcript_path.stem, model["label"])
        if usage_note():
            log(f"{transcript_path.stem}: {model['name']} used {usage_note()}")
        return out
    raise SummaryError("all models failed: " + " | ".join(failures))


def main():
    from council_transcripts import select_cities, slugify

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="*", help="transcript .json files (city is taken from the parent folder name)")
    p.add_argument("--city", action="append", help="summarize this city's transcripts that lack a summary (repeatable)")
    p.add_argument("--dir", default="transcripts", help="transcript root folder (default: transcripts)")
    p.add_argument("--limit", type=int, help="summarize at most N transcripts")
    p.add_argument("--overwrite", action="store_true", help="replace existing summaries")
    p.add_argument("--missing-tags", action="store_true",
                   help="re-summarize only existing summaries that have no Tags (made before tagging existed)")
    p.add_argument("--model", action="append", help="model name(s) from summarizer.toml to use instead of [summarize] models")
    p.add_argument("--config", default=str(CONFIG_FILE), help="config file (default: summarizer.toml)")
    p.add_argument("--list-models", action="store_true", help="show configured models and exit")
    p.add_argument("--jobs", type=int, default=1, help="summarize N transcripts in parallel (default: 1)")
    p.add_argument("--add-overviews", action="store_true",
                   help="add an Overview to saved summaries that lack one (small API request each) and exit")
    p.add_argument("--apply-keywords", action="store_true",
                   help="re-apply tags.json keyword rules to existing summaries (no API calls) and exit")
    p.add_argument("--check", metavar="SUMMARY", nargs="+", help="only validate existing summary files and exit")
    args = p.parse_args()

    config = load_config(args.config)
    if args.model:
        unknown = [m for m in args.model if m not in config.get("models", {})]
        if unknown:
            p.error(f"unknown model(s) {unknown}; see --list-models")

    if args.list_models:
        active = config.get("summarize", {}).get("models", [])
        for name in config.get("models", {}):
            m = model_settings(config, name)
            env = m["provider_config"].get("api_key_env") or "-"
            key = "key set" if env == "-" or os.environ.get(env) else f"{env} not set"
            mark = f"[{active.index(name) + 1}]" if name in active else "   "
            print(f"{mark} {name:12} {m['provider']:10} {m['model']:22} prompt={m['prompt']:9} {key}")
        return 0

    if args.add_overviews:
        model = model_settings(config, (args.model or config["summarize"]["models"])[0])
        files = sorted(f for f in output_dir(config).glob("*/*.json"))

        def run_overview(f):
            try:
                return add_overview(f, model, log=lambda m: print(m, file=sys.stderr))
            except Exception as e:
                print(f"ERROR  {f.name}: {e}", file=sys.stderr)
                return None

        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            results = list(pool.map(run_overview, files))
        print(f"{sum(r is True for r in results)} overview(s) added, {sum(r is None for r in results)} failed", file=sys.stderr)
        from build_site import build_manifest
        build_manifest(output_dir(config))
        return 1 if any(r is None for r in results) else 0

    if args.apply_keywords:
        known, keywords, changed = tag_ids(config), tag_keywords(config), 0
        for f in sorted(output_dir(config).glob("*/*.json")):
            summary = json.loads(f.read_text(encoding="utf-8"))
            if "Tags" not in summary or not isinstance(summary.get("Topics"), list):
                continue  # index files and summaries made before tagging
            summary, notes = normalize_summary(summary, known, keywords)
            added = [n for n in notes if "keyword match" in n]
            if added:
                changed += 1
                f.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                print(f"{f.parent.name}/{f.stem}: " + "; ".join(added), file=sys.stderr)
        print(f"{changed} summar{'y' if changed == 1 else 'ies'} updated", file=sys.stderr)
        if changed:
            from build_site import build_manifest
            build_manifest(output_dir(config))
        return 0

    if args.check:
        rules = config.get("summarize", {}).get("validation", {})
        bad = 0
        for f in args.check:
            errors, warnings = validate_summary(json.loads(Path(f).read_text(encoding="utf-8")),
                                                max_sentences=rules.get("max_summary_sentences", 5),
                                                known_tags=tag_ids(config))
            bad += bool(errors)
            print(f"{'INVALID' if errors else 'ok'}  {f}")
            for msg in errors + warnings:
                print(f"    {msg}")
        return 1 if bad else 0

    out_root = output_dir(config)
    jobs = [(Path(f), Path(f).parent.name) for f in args.files]
    if args.city or not args.files:
        for name, _ in select_cities(args.city or []):
            slug = slugify(name)
            # Newest dated meetings first; undated ones last.
            files = sorted((Path(args.dir) / slug).glob("*.json"),
                           key=lambda f: (not f.name.startswith("undated_"), f.name), reverse=True)
            jobs += [(f, slug) for f in files]
    if args.missing_tags:
        def untagged(f, slug):
            path = summary_path(f, slug, out_root)
            return path.exists() and "Tags" not in json.loads(path.read_text(encoding="utf-8"))
        jobs = [(f, slug) for f, slug in jobs if untagged(f, slug)]
    elif not args.overwrite:
        jobs = [(f, slug) for f, slug in jobs if not summary_path(f, slug, out_root).exists()]
    jobs = jobs[:args.limit] if args.limit else jobs
    print(f"{len(jobs)} transcript(s) to summarize with {args.model or config['summarize'].get('models')}",
          file=sys.stderr)

    def run(job):
        f, slug = job
        try:
            out = summarize_transcript(f, slug, config, args.model, log=lambda m: print(m, file=sys.stderr))
            print(f"saved  {out}", file=sys.stderr)
            return True
        except Exception as e:
            print(f"ERROR  {f.name}: {e}", file=sys.stderr)
            return False

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        failures = sum(not ok for ok in pool.map(run, jobs))
    if usage_totals["requests"]:
        print(f"usage  {usage_totals['requests']} request(s), {usage_totals['input']:,} tokens in, {usage_totals['output']:,} out"
              + (f", about ${usage_totals['cost']:.2f}" if usage_totals["cost"] else "") + f" (details: {USAGE_LOG.name})",
              file=sys.stderr)

    if jobs:
        from build_site import build_manifest
        build_manifest(out_root)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
