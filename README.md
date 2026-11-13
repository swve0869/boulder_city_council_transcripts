# City Council Meeting Summaries

Watches city council YouTube channels, saves each new meeting's English captions as a transcript, summarizes the transcript with an LLM (Claude Haiku 5.5 currently; configurable in `summarizer.toml`), and publishes the summaries to a static site on GitHub Pages.

Cities don't publish written transcripts, so this uses the captions on the meeting videos: the city's uploaded captions when they exist, otherwise YouTube's auto-generated ones.

## How it fits together

```
Raspberry Pi (home network)                                   GitHub Pages (docs/)
───────────────────────────                                   ────────────────────
systemd timer → scripts/cron_watch.sh
        └ watch_council_videos.py --once --fetch --summarize --publish
            ├ new video on a channel in national_city_councils.json
            ├ transcript → transcripts/<city>/<date>_<id>.json   (stays here)
            ├ summarize.py → models from summarizer.toml → structure check
            └ docs/summaries/<city>/<date>_<id>.json ── git push ──→ summaries/…json
                                                                  index.html, meeting.html
serve_transcripts.py ← Tailscale Funnel ←── browser fetch (HTTPS, CORS) ── "Load full transcript"
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
| `scripts/cron_watch.sh` | One watch cycle: loads `.env`, prevents overlapping runs (run by the systemd timer or cron) |
| `scripts/setup_pi.sh` | Sets up a Raspberry Pi host (packages, venv, git identity, systemd units, log rotation) |
| `deploy/` | systemd unit and logrotate templates installed by `setup_pi.sh` |
| `docs/` | The GitHub Pages site (`config.js`, `app.js`, `index.html`, `meeting.html`, `summaries/`) |

## Raspberry Pi setup (the production host)

The Pi runs everything that isn't the website: the watcher (every 20 minutes), summarizing, publishing to GitHub, and the full-transcript server. [Tailscale Funnel](https://tailscale.com/kb/1223/funnel) puts that server on a public HTTPS address without router changes or port forwarding. Steps 1–9 are a one-time setup.

### 1. Prepare the Pi

- Flash **Raspberry Pi OS (64-bit), Bookworm or newer** with Raspberry Pi Imager. Its Python 3.11+ is required, and the 64-bit build installs the Python packages without compiling. In the Imager's settings, set a username, enable SSH and set up Wi-Fi (or use Ethernet).
- Boot it, find its address (e.g. on your router's client list, or `ping raspberrypi.local`), and connect: `ssh <user>@<pi-address>`.
- Change the default password (`passwd`). The Pi will hold your API keys.

### 2. Give the Pi push access to GitHub (deploy key)

The Pi commits and pushes summaries, so it needs write access to this one repo:

```
ssh-keygen -t ed25519 -f ~/.ssh/council_deploy -N "" -C "council-transcripts-pi-deploy"
cat >> ~/.ssh/config <<'CFG'
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/council_deploy
  IdentitiesOnly yes
CFG
chmod 600 ~/.ssh/config
cat ~/.ssh/council_deploy.pub
```

On GitHub, go to the repo → **Settings → Deploy keys → Add deploy key**, paste the public key, and tick **Allow write access**. Check it with `ssh -T git@github.com`, which should greet the repo.

### 3. Clone the repo

```
git clone git@github.com:<you>/boulder_city_council_transcripts.git
cd boulder_city_council_transcripts
```

### 4. Copy secrets and transcripts from your old machine

Full transcripts and keys aren't in git. Run this on the old machine, from the repo:

```
tar czf - transcripts .env | ssh <user>@<pi-address> 'cd ~/boulder_city_council_transcripts && tar xzf - && chmod 600 .env'
```

Starting fresh instead? Skip this step; setup creates `.env` from `.env.example`, and you add `ANTHROPIC_API_KEY=sk-ant-...` (plus any other provider keys from `summarizer.toml`) to it.

### 5. Run the setup script

```
scripts/setup_pi.sh            # --dry-run to preview; --no-timer to skip the watcher timer
```

It is safe to re-run (e.g. after `git pull`). It:

- installs system packages (`python3-venv`, `git`, `logrotate`, …) and checks the Python version
- creates `.venv` and installs `requirements.txt`
- creates `.env` from `.env.example` if it's missing
- sets a **repo-local git identity** (`Council Pi <you@users.noreply.github.com>`) so publishing can commit
- installs and starts **`council-transcripts.service`**: `serve_transcripts.py` on `127.0.0.1:8765`, which allows requests only from your `https://<you>.github.io` site and restarts on failure
- installs and starts **`council-watch.timer`**: runs `scripts/cron_watch.sh` every 20 minutes and catches up after the Pi was off, so no crontab is needed
- installs log rotation for `watch_council_videos.log`
- warns if the Pi can't push to GitHub or has no `transcripts/` folder

### 6. Expose the transcript server with Tailscale Funnel

```
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up                      # open the printed link and sign in
sudo tailscale funnel --bg 8765        # prints https://<pi-name>.<tailnet>.ts.net
```

Funnel needs HTTPS certificates and the `funnel` node attribute enabled for your tailnet. The first `tailscale funnel` command offers links to turn both on. Check it worked:

```
sudo tailscale funnel status                                          # "Funnel on" ... proxy http://127.0.0.1:8765
sudo journalctl -u tailscaled -o cat | grep IngressEnabled | tail -1  # must say "changed to true"
```

Then, from any machine *off* your tailnet (e.g. a phone on cellular), open `https://<pi-name>.<tailnet>.ts.net/boulder/<some-stem>.json`; it should return the transcript JSON.

### 7. Point the website at the Pi

In `docs/config.js`, set `transcriptHost: "https://<pi-name>.<tailnet>.ts.net"`, then commit and push. Meeting pages then show **Load full transcript**.

### 8. Seed the watcher (avoid skipping a meeting)

The watcher's first run only *records* the videos already on each channel. A meeting that is listed but has no transcript yet, such as a live stream that hasn't started, would be marked seen and never fetched. To avoid that, record the existing videos without fetching, then queue any that are missing a transcript:

```
.venv/bin/python watch_council_videos.py --once     # records existing videos in seen_videos.json
.venv/bin/python - <<'PY'
import json
from pathlib import Path
state = json.load(open("seen_videos.json"))
for city, s in state.items():
    # stem is <date>_<videoid>; ids can contain "_", so split on the first one only
    have = {f.stem.split("_", 1)[1] for f in Path("transcripts", city).glob("*.json")}
    for vid, title in s["seen"].items():
        if vid not in have and vid not in {p["id"] for p in s["pending"]}:
            print("queueing", city, vid, title)
            s["pending"].append({"id": vid, "title": title, "date": None})
json.dump(state, open("seen_videos.json", "w"), indent=2)
PY
```

Queued videos are fetched once captions exist, then summarized and published. Rename any `undated_` transcript this produces to `YYYY-MM-DD_<id>` if you want it dated.

### 9. Backfill summaries for existing transcripts

The watcher summarizes only *new* meetings. To summarize everything already in `transcripts/`, run this as a background job so it survives logging out:

```
sudo systemd-run --unit=council-backfill --uid=$USER --gid=$USER --working-directory=$PWD --setenv=HOME=$HOME --nice=10 \
  /bin/bash -c 'set -a; . ./.env; set +a; .venv/bin/python summarize.py --city Boulder && .venv/bin/python build_site.py --publish'
journalctl -u council-backfill -f       # follow progress (Ctrl+C stops following, not the job)
```

With Claude Haiku 5.5 this costs a few cents per meeting and takes about 10–20 seconds each. Add `--limit N` to `summarize.py` to go in batches.

### Make sure only the Pi runs the watcher

Stop any cron job, `watch_council_videos.py`, or `serve_transcripts.py` on other machines, so two hosts don't fetch and push the same meetings.

### Day to day

```
systemctl status council-transcripts council-watch.timer     # both should be active
systemctl list-timers council-watch.timer                    # next/last watcher run
journalctl -u council-watch -n 50 -o cat                     # what the last runs did
sudo systemctl start council-watch                           # run a cycle now
sudo tailscale funnel status                                 # public transcript server
cd ~/boulder_city_council_transcripts && git pull && scripts/setup_pi.sh   # update the Pi
```

### Pi troubleshooting

- **The site says "The full transcript is unavailable right now":** check `sudo tailscale funnel status`. If the server isn't listed, or the `IngressEnabled` log line says `false`, rerun `sudo tailscale funnel --bg 8765`. A Funnel reset or reinstall clears the config.
- **Publishing fails with "unable to auto-detect email address":** the repo has no git identity. Rerun `scripts/setup_pi.sh`, or run `git config user.name` / `git config user.email` in the repo.
- **"Permission denied (publickey)" on push:** the deploy key is missing, lacks write access, or `~/.ssh/config` doesn't point to it (step 2).
- **"Sign in to confirm you're not a bot":** rare on a home connection. Export `cookies.txt` from a signed-in browser elsewhere and copy it to the Pi. Add `--cookies /path/cookies.txt` to `ExecStart` in `/etc/systemd/system/council-watch.service`, then run `sudo systemctl daemon-reload`.
- **A summary failed:** it's listed under `unsummarized` in `seen_videos.json` and retried on every run. `journalctl -u council-watch` shows why it failed (missing key, invalid output, …).

## Other hosts (manual setup on any Linux machine)

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

Claude models use prompt caching, so retries cost much less. Sonnet and Opus also use server-side refusal fallback (`fallbacks = "default"`). Haiku has none, so list a second model after it if you want refusals covered.

Cost: a long meeting transcript runs to roughly 100–200k input tokens. That's a few cents per meeting with Claude Haiku 5.5 (the current model) and about $0.20–0.40 with Sonnet 5.5.

```
python summarize.py --list-models                # configured models, fallback order, which keys are set
python summarize.py --city Boulder --limit 3      # backfill: newest transcripts that lack a summary
python summarize.py --city Boulder --limit 1 --model deepseek   # use a specific model
python summarize.py transcripts/boulder/2026-09-17_SS0nUFcZFq0.json --overwrite
python summarize.py --check docs/summaries/boulder/*.json    # validate saved summaries
python build_site.py --publish                    # rebuild the index and push
```

`summarize.py` rebuilds `docs/summaries/index.json` itself, but it doesn't push.

## Full transcripts from the host (optional; the Pi uses Tailscale Funnel, above)

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
