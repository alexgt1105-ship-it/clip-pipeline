# clip-pipeline

Turns long-form video into captioned vertical clips, sends each one to Telegram for a
yes/no, and posts the yeses to YouTube Shorts.

Source can be anything yt-dlp handles — a YouTube video or channel, a Twitch VOD, a
podcast episode, an X post — or a file already on disk. Nothing is tied to one creator,
so a finished contract just gets replaced by the next one.

## What a run costs

Per hour of source video, which yields roughly 5 clips:

| Step | Cost |
|---|---|
| Download | free |
| Transcript, when the source publishes captions | **free** |
| Transcript, when it doesn't (Whisper fallback) | $0.36 |
| Picking the clips (Opus 5) | ~$0.15 |
| Rendering | free, runs on your Mac |
| Telegram review | free |
| YouTube upload | free |

So **about 15 cents per source video** in the normal case, or **50 cents** for a source
with no captions. That is 3 cents a clip. Setting `CLIP_MODEL=claude-haiku-4-5` in `.env`
drops the picking step to ~3 cents, at some cost in judgement about what is actually
worth clipping.

Transcripts are cached per source, so re-running a source never pays twice.

## Setup

Three things, once.

**1. Telegram bot.** Message [@BotFather](https://t.me/botfather), `/newbot`, follow the
prompts, paste the token into `.env` as `TELEGRAM_BOT_TOKEN`. Then send your new bot any
message so it is allowed to DM you. This must be a *different* bot from the
motivation-shorts one — two pollers on one token eat each other's button taps.

**2. YouTube access for the new account.** In
[Google Cloud Console](https://console.cloud.google.com/), create a project, enable
**YouTube Data API v3**, create an **OAuth client ID** of type **Desktop app**, download
the JSON and save it here as `youtube_client_secret.json`. Then:

```bash
./venv/bin/python src/youtube_auth.py
```

A browser opens. **Sign in with the new clipping account, not the motivation-shorts one.**
It prints which channel it authorised so you can confirm. If it names the wrong channel,
delete `youtube_token.json` and run it again.

**3. Your first contract.**

```bash
./venv/bin/python src/pipeline.py campaign add acme-podcast \
    "https://www.youtube.com/@somechannel" --target 100 --per-video 5
```

## Running it

```bash
# Clip one thing right now
./venv/bin/python src/pipeline.py clip "<url or file path>"

# See what it would pick, without rendering or spending render time
./venv/bin/python src/pipeline.py clip "<url>" --dry-run

# Advance the active contract by one source video
./venv/bin/python src/pipeline.py auto

# Watch Telegram for taps and commands
./venv/bin/python src/listener.py
```

Then hand it to launchd so nothing needs starting by hand:

```bash
./launchd/install.sh
```

That keeps the listener alive permanently and fires `auto` at 08:30 and 18:30.

## Running it on GitHub Actions (no Mac needed)

`.github/workflows/clip.yml` runs at 08:30 and 18:30 Pacific, or on demand from the Actions
tab. Each run is `src/batch.py`: handle the taps and commands that came in since the last
run, clip one source from the active contract, stay on Telegram 10 minutes so the new
clips can be tapped right away, then exit. A tap after that waits in Telegram until the
next run picks it up.

The repo is public for free unlimited minutes, so nothing about the contracts shows:

- `state.enc` holds the watchlist, clip log and Telegram offset, AES-256 under `STATE_KEY`.
  `scripts/state_crypt.py unpack` / `pack` open and close it.
- Clips waiting for a tap ride between runs in the Actions cache as `pending.enc`, under
  the same key.
- The run's output goes to a file, not the public log page. If a run fails, the tail of
  that file is sent to you on Telegram.
- Commits are all named `update`.

Repo secrets: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, `STATE_KEY` (all from `.env`), and `YOUTUBE_TOKEN_JSON` (the contents
of `youtube_token.json`). Set the repo variable `YOUTUBE_PRIVACY_STATUS=public` once the
uploads look right; it defaults to private.

Only the owner's chat (`TELEGRAM_CHAT_ID`) can command the bot. Anyone else is ignored.

Don't run the Mac listener and the GitHub runs together. They share one bot and would take
each other's taps.

## How contracts advance on their own

A contract is an entry in `data/watchlist.json`: a name, one or more source URLs, and
optionally a target number of posted clips.

Exactly one contract is `active` at a time. Every scheduled run:

1. takes the active contract,
2. lists the videos behind its sources and picks the newest one not already clipped,
3. clips it and sends the results to Telegram.

When the active contract **hits its target** or **runs out of unclipped source video**,
the runner closes it, promotes the next `queued` contract to active, and texts you that
it happened. The next scheduled run is already working the new contract. You do not click
anything when a contract ends.

Starting the *next* contract is the one thing that needs you, because only you know what
you signed. It takes one text message to the bot:

```
/add nextclient https://www.youtube.com/@theirchannel
```

If a contract is already running, that one queues behind it and starts by itself when the
current one finishes. So the steady state is: a contract ends, you text one line, and the
machine keeps going.

### Telegram commands

```
/add <name> <url...>   start (or queue) a contract
/done <name>           close a contract early; the next queued one takes over
/campaigns             what is running and how many clips it has posted
/status                clip counts
/run                   process one more source right now
<any url>              clip that one thing
```

Plus **Post it** / **Skip** buttons under every clip.

## Things worth knowing

- **YouTube allows 6 uploads a day** on the default API quota (each upload costs 1600 of
  10,000 daily units). The 08:30/18:30 schedule at 5 clips a run is deliberately close to
  that ceiling, not over it.
- **Uploads default to private** (`YOUTUBE_PRIVACY_STATUS` in `.env`). Set it to `public`
  once you have watched a few come out right.
- **Every clip credits the source** in its description. Most paid clipping programs
  require that, and it is the decent thing regardless.
- **Two layouts.** `blur` centres the original over a blurred copy of itself and never
  crops anyone out of frame — right for two-person podcasts and screen shares. `crop` is
  full-bleed 9:16 and hits harder on a single talking head. Set per contract with
  `--layout`, or globally in `.env`.
- **Source videos are deleted after their clips render.** They run 100-500 MB each and
  the transcript beside them is what a re-cut actually needs. Set `KEEP_SOURCES=1` in
  `.env` to keep them.
- **If downloads start failing** ("The page needs to be reloaded", HTTP 403), YouTube has
  changed something and yt-dlp is stale. Run `./scripts/update-ytdlp.sh`. If that alone
  does not fix it, set `COOKIES_FROM_BROWSER=chrome` in `.env` so downloads use your
  logged-in session.

## Layout

```
src/
  pipeline.py      orchestrator + CLI
  listener.py      Telegram daemon: button taps and commands
  batch.py         one run for GitHub Actions: taps, one source, listen, exit
  campaigns.py     contracts, and the auto-advance when one ends
  ingest.py        yt-dlp download / local file adoption
  subs.py          free word-timed transcript from the source's own captions
  transcribe.py    Whisper fallback when there are no captions
  select_clips.py  Claude picks the moments
  render.py        cut, reframe to 9:16, burn captions, normalise audio
  captions.py      karaoke-style ASS subtitles
  upload.py        YouTube Shorts upload
  state.py         clips.json
data/
  watchlist.json   your contracts
  clips.json       every clip and its status
  pending/ posted/ rejected/
```

Python 3.12 via `uv` (the system's 3.9 is too old for current yt-dlp). ffmpeg comes from
the `imageio-ffmpeg` package — no Homebrew needed.
