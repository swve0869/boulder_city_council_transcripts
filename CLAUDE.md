# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Watches city council YouTube channels (listed in `national_city_councils.json` as `{"City": "channel URL"}`), saves caption transcripts on a private host machine, summarizes them with LLMs configured in `summarizer.toml` (currently Claude Haiku 5.5), and publishes **only the summaries** to GitHub Pages (`docs/`, served from `main` /docs). README.md has full CLI usage and host setup.

Python 3.11+ (`tomllib`). Dependencies: `pip install -r requirements.txt` (yt-dlp, anthropic, openai). There are no tests or linter. Scripts are standalone CLIs run from the repo root. API keys come from `.env` (gitignored), which `scripts/cron_watch.sh` loads. Each provider in `summarizer.toml` names its own env var.

## Commands

```
scripts/cron_watch.sh                                        # what cron runs: one full cycle for all cities
python watch_council_videos.py --once --fetch --summarize    # same, without pushing
python summarize.py --list-models                            # models, fallback order, keys present
python summarize.py --city Boulder --limit 1 [--model NAME]  # summarize transcripts lacking a summary (costs API $)
python summarize.py --check docs/summaries/boulder/*.json    # validate summaries, no API call
python build_site.py [--publish]                             # rebuild docs/summaries/index.json [and git push]
python -m http.server -d docs 8000                           # preview site (fetch() fails on file://)
```

## Architecture

Pipeline: `watch_council_videos.py` → `council_transcripts.py` (yt-dlp: list channel, fetch json3 captions) → `transcripts/<city-slug>/<stem>.{json,txt}` → `summarize.py` (models from `summarizer.toml`) → `docs/summaries/<city-slug>/<stem>.json` → `build_site.py` (manifest + git push) → browser.

- **`council_transcripts.py` is also the shared library** (`list_council_videos`, `fetch_transcript`, `save_transcript`, `select_cities`, `slugify`, date regexes). It imports yt-dlp lazily so `summarize`/`build_site` work without it.
- **Naming is load-bearing.** `<stem>` = `<YYYY-MM-DD|undated>_<videoid>`, and video IDs can contain `_`, so split on the first `_` only. City folders are `slugify(name)`. The site and `build_site.py` get date and video ID from the stem, and the city from the folder name. Summaries carry no metadata of their own.
- **Transcripts never go in git.** `transcripts/` is gitignored, and `publish()` stages only `docs/summaries`. Keep it that way: the site must not receive full transcripts.
- **Summary format** is defined by `summary_prompt_JSON_format.json`, which replaces `{format}` in the prompt text in `summarizer.toml` (`[prompts.*]`). `Topics` is a list of objects keyed `Topic1`, `Topic2`, …. Both `validate_summary()` and `topicsOf()` in `docs/app.js` accept one key per object or many keys in one object. If you change the format, update the JSON file, `validate_summary`, and `topicsOf` together. `related_timestamps` are transcript `start` values in seconds (number or numeric string).
- **Tags:** `tags.json` is the controlled vocabulary (ids, labels, groups, descriptions). `build_prompt` fills `{tags}` from it, and `build_schema` turns it into a structured-output JSON schema: Topics items are `anyOf` single-key `{"TopicN": topic}` objects, with N=`max_topics` (30; 40 exceeds the grammar size limit), and tags are an enum. `normalize_summary` renumbers topics and drops unknown tags before `validate_summary`. Tags are optional in validation so pre-tag summaries still pass. `build_site.meeting_entry` derives per-meeting `topics` [{title, tags, start, end}] and `tag_seconds` for the site; the site reads only the manifest for lists and charts, and the summary file for the meeting page. Tag-group colors are the validated 8-slot palette (`--series-N` in style.css, mapped by group order in tags.json); if you add a 9th group, fold it into an existing one rather than inventing a color.
- **Models (`summarizer.toml`):** providers are `type = "anthropic"` (`call_anthropic`: `client.beta.messages.stream`, transcript in a `cache_control` block, optional `fallbacks` + `server-side-fallback-2026-07-01` beta) or `type = "openai"` (`call_openai`: `chat.completions`, which covers OpenAI, DeepSeek, Gemini, OpenRouter and Ollama via `base_url`). To add a provider type, add a caller to `CALLERS` and to `PROVIDER_TYPES`. `[summarize] models` is a fallback chain: each model gets `max_attempts` tries on invalid output or transient errors, while 4xx and missing keys skip straight to the next model. Nothing is written unless validation passes. `load_config()` checks cross-references at startup. The winning model's `label` is recorded in `docs/summaries/models.json`, and `build_site.py` copies it into the manifest for the site to display.
- **Deployment:** the intended host is a Raspberry Pi set up by `scripts/setup_pi.sh`. It fills the `@USER@`/`@REPO@`/`@PORT@`/`@ORIGIN@` placeholders in `deploy/systemd/*` and `deploy/logrotate.conf`, then installs a systemd service for the transcript server (bound to 127.0.0.1 and exposed through Tailscale Funnel) and a 20-minute timer that runs `scripts/cron_watch.sh`.
- **Watcher state** (`seen_videos.json`) is keyed by city slug: `seen`, `pending` (no captions yet, e.g. upcoming live streams), `unsummarized` (failed summaries). Both lists retry every run. The first run for a city only records existing videos. `scripts/cron_watch.sh` uses `flock` so runs don't overlap.
- **Site** (`docs/`) is hand-written static files, not generated: `app.js` renders `index.html` (tag filters/search/sort, state in the URL: `?tags=a,b&match=all&sort=relevance&q=`) and `meeting.html?city=&id=` (fetches the summary JSON). `trends.js` renders `trends.html` from the manifest using helpers that `app.js` exports as `window.Council`. Timestamps seek a YouTube IFrame API player (fallback: a plain embed with `?start=`), and `#t=<sec>` deep-links. Summary text is LLM output, so insert it only via `textContent`. If `transcriptHost` in `docs/config.js` is set, the page fetches full transcripts from `serve_transcripts.py` on the host, which needs HTTPS (behind a TLS proxy) and CORS.
