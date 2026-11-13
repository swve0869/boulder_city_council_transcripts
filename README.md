# City Council Meeting Summaries

Watches city council YouTube channels, saves each new meeting's English captions as a transcript, summarizes the transcript with an LLM (Claude Sonnet 5.5 by default; configurable in `summarizer.toml`), and publishes the summaries to a static site on GitHub Pages.

Cities don't publish written transcripts, so this uses the captions on the meeting videos: the city's uploaded captions when they exist, otherwise YouTube's auto-generated ones.

## How it fits together

```
host machine (private network, dynamic DNS)                   GitHub Pages (docs/)
──────────────────────────────────────────                    ────────────────────
cron → scripts/cron_watch.sh
        └ watch_council_videos.py --once --fetch --summarize --publish
            ├ new video on a channel in national_city_councils.json
            ├ transcript → transcripts/<city>/<date>_<id>.json   (stays here)
            ├ summarize.py → models from summarizer.toml → structure check
            └ docs/summaries/<city>/<date>_<id>.json ── git push ──→ summaries/…json
                                                                  index.html, meeting.html
serve_transcripts.py (optional) ←── browser fetch (HTTPS, CORS) ── "Load full transcript"
```

- **GitHub Pages stores only summaries.** `transcripts/` is gitignored and never leaves the host.
- The site is static HTML and JavaScript. The browser fetches each summary's JSON file and renders it, so there's no per-meeting HTML. Clicking a timestamp jumps the embedded YouTube player to that moment.
- If the host runs `serve_transcripts.py`, the meeting page can also load the full transcript from it and scroll to each timestamp.

| File | Purpose |
| --- | --- |
| `national_city_councils.json` | `{"City name": "YouTube channel URL"}` for every watched city |
| `watch_council_videos.py` | Checks each city's channel for new council videos; fetches, summarizes, publishes |
| `council_transcripts.py` | Lists a channel's council videos and saves transcripts (also the shared library) |
| `summarize.py` | Summarizes transcripts with the configured models and validates the result |
| `summarizer.toml` | Model providers, models, prompts, fallback order and validation settings |
| `summary_prompt_JSON_format.json` | The JSON format the summary prompt asks for |
| `build_site.py` | Writes `docs/summaries/index.json`; `--publish` commits and pushes summaries |
| `serve_transcripts.py` | Read-only server for full transcripts on the host |
| `combine_transcripts.py` | Joins a city's transcripts for a year or month into one text file |
| `scripts/cron_watch.sh` | Cron wrapper: loads `.env`, prevents overlapping runs, runs one watch cycle |
| `docs/` | The GitHub Pages site (`config.js`, `app.js`, `index.html`, `meeting.html`, `summaries/`) |

## Raspberry Pi setup (recommended host)

Use Raspberry Pi OS Bookworm **64-bit** or newer (Python 3.11+). On the Pi:

```
git clone git@github.com:<you>/boulder_city_council_transcripts.git && cd boulder_city_council_transcripts
scripts/setup_pi.sh            # --dry-run to preview, --no-timer to skip the watcher timer
```

The script installs packages, creates `.venv`, and creates `.env` if it's missing. It then installs and starts two systemd units:

- **`council-transcripts.service`** runs `serve_transcripts.py` on `127.0.0.1:8765`. The allowed site origin is taken from the git remote.
- **`council-watch.timer`** runs `scripts/cron_watch.sh` every 20 minutes. It catches up after the Pi was off, so you don't need a crontab entry.

It also adds log rotation for `watch_council_videos.log`, and warns if the Pi can't push to GitHub or has no `transcripts/` folder yet.

Then:

1. **Secrets and data:** put your keys in `.env`. Copy over `transcripts/` (and `seen_videos.json`, if you have one) from the old machine, e.g. `rsync -a old:boulder_city_council_transcripts/transcripts ./`.
2. **Push access:** create a deploy key on the Pi (`ssh-keygen -t ed25519 -f ~/.ssh/council_deploy`). Add the `.pub` under the repo's Settings → Deploy keys with write access, and point `~/.ssh/config` at it for `github.com`.
3. **HTTPS without the router:** install Tailscale (`curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`), then run `sudo tailscale funnel --bg 8765`. Set `transcriptHost` in `docs/config.js` to the `https://….ts.net` address it prints, then commit and push.
4. **One watcher only:** stop any cron job or watcher on other machines, so two hosts don't push the same summaries.

Day to day:

```
systemctl status council-transcripts council-watch.timer
journalctl -u council-watch -n 50          # last watcher run
sudo systemctl start council-watch         # run a cycle now
```

YouTube rarely challenges home connections. If it does, export `cookies.txt` from a signed-in browser elsewhere, copy it to the Pi, and add `--cookies /path/cookies.txt` to `ExecStart` in `/etc/systemd/system/council-watch.service`. Then run `sudo systemctl daemon-reload`.

## Host machine setup (manual / other Linux)

Requires Python 3.11+ and git with push access to this repo.

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # then set ANTHROPIC_API_KEY=sk-ant-... in it
# or: put the bare key in "API Keys/city_council_summary_API_key"
```

`scripts/cron_watch.sh` uses `.venv/bin/python` if it exists, otherwise `python3` (override with `PYTHON=`).

### Cron

```
crontab -e
*/20 * * * * /path/to/repo/scripts/cron_watch.sh >> /path/to/repo/cron.log 2>&1
```

Each run checks every city in `national_city_councils.json`. The first run for a city only records the videos already on its channel, so it doesn't backfill. Extra arguments are passed through, for example `scripts/cron_watch.sh --cookies /path/to/cookies.txt` when YouTube asks you to sign in.

### Adding a city

Add an entry to `national_city_councils.json`:

```json
{
  "Boulder": "https://www.youtube.com/user/bouldercoloradogov",
  "Example City": "https://www.youtube.com/@ExampleCityGov"
}
```

The folder name is the city name lowercased with dashes (`example-city`). Videos are matched by "City Council" in the title.

## Summaries

`summarize.py` sends the transcript to the models configured in [summarizer.toml](summarizer.toml). The default prompt is the one below; the `{format}` placeholder is filled from `summary_prompt_JSON_format.json`:

> summarize this city council transcript into this JSON format {…} where related_timestamps are "start" fields from the JSON transcript where the topic begins and ends. Summary sections should not be longer than 5 sentences. DO NOT return anything else but the JSON formatted text

A reply is accepted only if its structure checks pass:

- The top level has exactly `Title` (a string) and `Topics` (a non-empty list).
- Each topic key looks like `Topic<N>`. Each topic has non-empty `Title` and `Summary` strings and a non-empty `related_timestamps` list.
- Each timestamp is a number of seconds that falls within the transcript.

Timestamps that aren't exact segment starts, and summaries longer than five sentences, are logged as warnings. Both checks can be adjusted under `[summarize.validation]`.

### Choosing models (`summarizer.toml`)

| Section | What it sets |
| --- | --- |
| `[summarize]` | `models`, an ordered list of models to try. The first valid reply wins, and a model that errors, refuses or keeps returning invalid JSON hands off to the next. Also the format file, the output folder, and per-model defaults: `prompt`, `max_attempts`, `transcript_format`, `max_input_chars`, `timeout_seconds`. |
| `[prompts.<name>]` | `text` (must contain `{format}`) and an optional `system` prompt. |
| `[providers.<name>]` | `type` (`anthropic`, or `openai` for any OpenAI-compatible API), `base_url`, and `api_key_env`, the environment variable holding the key. |
| `[models.<name>]` | `provider`, `model` ID, `label` (shown on the site), `max_tokens`, `json_mode`, `prompt_cache`, `fallbacks`, and `params`, which is passed straight to the API (e.g. `temperature`). |

The config ships with Anthropic, OpenAI, DeepSeek, Gemini, OpenRouter and Ollama (local) providers. Put their keys in `.env` (`OPENAI_API_KEY=...`, `DEEPSEEK_API_KEY=...`). Model IDs other than Claude's are examples; check each provider's current model list. For small-context models, set `transcript_format = "lines"` (about 30% fewer tokens) and `max_input_chars`, so too-long transcripts skip that model instead of being cut off.

The model that wrote each summary is recorded in `docs/summaries/models.json` and credited on the meeting page.

Claude models use prompt caching, so retries cost much less, and server-side refusal fallback (`fallbacks = "default"`).

Cost with Claude Sonnet 5.5: a long meeting transcript runs to roughly 100–200k input tokens, about $0.20–0.40 per meeting.

```
python summarize.py --list-models                # configured models, fallback order, which keys are set
python summarize.py --city Boulder --limit 3      # backfill: newest transcripts that lack a summary
python summarize.py --city Boulder --limit 1 --model deepseek   # use a specific model
python summarize.py transcripts/boulder/2026-09-17_SS0nUFcZFq0.json --overwrite
python summarize.py --check docs/summaries/boulder/*.json    # validate saved summaries
python build_site.py --publish                    # rebuild the index and push
```

`summarize.py` rebuilds `docs/summaries/index.json` itself, but it doesn't push.

## Full transcripts from the host (optional)

```
python serve_transcripts.py --origin https://<user>.github.io     # listens on :8765
```

The server answers only `GET /<city>/<stem>.json`: no directory listings and no other file types. GitHub Pages is HTTPS, so browsers block plain-HTTP requests to your host. Put the server behind a TLS reverse proxy on your dynamic-DNS name, for example Caddy:

```
council.example.ddns.net {
    reverse_proxy localhost:8765
}
```

Then set `transcriptHost: "https://council.example.ddns.net"` in `docs/config.js` and commit it. With it left empty, the site shows summaries and video timestamps only.

## Manual tools

```
# Download transcripts for a city
python council_transcripts.py --city Boulder --since 2026-01-01 --cookies-from-browser chrome
python council_transcripts.py --city Boulder --limit 5
python council_transcripts.py --city Boulder --video SS0nUFcZFq0

# Watch without cron (checks every 20 minutes; Ctrl+C to stop)
python watch_council_videos.py --fetch --summarize

# Combine a city's transcripts for a year or month
python combine_transcripts.py 2026 --city Boulder          # -> boulder_council_transcripts_2026.txt

# Preview the site locally (fetch() doesn't work from file://)
python -m http.server -d docs 8000
```

`council_transcripts.py` options: `--since YYYY-MM-DD`, `--limit N`, `--video ID` (repeatable), `--out DIR`, `--no-timestamps`, `--overwrite`, `--delay SECONDS`, `--cookies-from-browser NAME`, `--cookies FILE`.

`watch_council_videos.py` options: `--city NAME` (repeatable), `--once`, `--fetch`, `--summarize`, `--publish`, `--interval MINUTES`, `--scan N`, `--state FILE`, `--log FILE`, and the cookie options. State lives in `seen_videos.json`, keyed by city. It holds the videos already seen, videos still waiting for captions, and transcripts whose summary failed. Both waiting lists are retried on every run.

### Transcript files

Each meeting is saved as `transcripts/<city>/<meeting date>_<video id>.{txt,json}`. The `.json` has `video_id`, `title`, `date`, `url`, `auto_generated` and `segments: [{"start": seconds, "text": ...}]`. Meetings fetched with `--video` have no title date and are saved as `undated_<id>`. Rename them to `YYYY-MM-DD_<id>` to date them.

## Troubleshooting

- **"Sign in to confirm you're not a bot"**: pass `--cookies-from-browser` or `--cookies`. On a headless host, export a `cookies.txt` from a signed-in browser.
- **"HTTP Error 429"**: you're rate-limited by YouTube. Wait, raise `--delay`, and rerun; already-saved meetings are skipped.
- **"This live event will begin in …" / no English captions yet**: captions for live streams appear hours to days later. The watcher keeps retrying.
- **"environment variable … is not set"**: the provider's key is missing. Put it in `.env`. Failed summaries are retried on the next run.

## Caveats

- Most transcripts are auto-generated captions, so names, numbers and technical terms are often wrong, and summaries inherit those errors. Check against the video before relying on them.
- Timestamps are the time into the video, not the time of day.
- Any video whose title contains "City Council" is included, such as a candidate forum.
