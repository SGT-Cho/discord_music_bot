# Discord Music Bot

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-AGPL--3.0--only-blue)
![Docker](https://img.shields.io/badge/docker-compose%20ready-2496ED?logo=docker&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-2.7%2B-5865F2?logo=discord&logoColor=white)

**English** | [한국어](docs/i18n/README.ko.md) | [中文](docs/i18n/README.zh-CN.md) | [日本語](docs/i18n/README.ja.md) | [Español](docs/i18n/README.es.md) | [Русский](docs/i18n/README.ru.md) | [Deutsch](docs/i18n/README.de.md) | [Français](docs/i18n/README.fr.md) | [Italiano](docs/i18n/README.it.md)

A portfolio-oriented Discord music bot reference implementation built with
`discord.py`, `yt-dlp`, and FFmpeg.

This repository contains source code only. It is not a hosted bot service and
does not include credentials, cookies, downloaded media, or a running Discord
deployment.

> [!NOTE]
> In-Discord responses are localized: English by default, Korean with
> `BOT_LANGUAGE="ko"` in your `.env`.

## Features

- YouTube and YouTube Music playback
- Spotify and SoundCloud metadata resolution to YouTube searches
- Apple Music URL detection with a search fallback
- Direct audio URL playback through FFmpeg
- Autoplay through YouTube Mix results
- Playlist processing with bounded concurrency
- Local audio caching support for private deployments
- Adaptive bitrate selection and manual overrides
- Stream recovery and voice connection monitoring
- Performance metrics and command error handling

## Commands

| Command | Description |
| --- | --- |
| `/play` | Play a song or playlist from a URL or search query |
| `/join` | Summon the bot to your voice channel |
| `/skip` | Skip the current track |
| `/pause` / `/resume` | Pause or resume playback |
| `/stop` | Stop playback and disconnect the bot |
| `/volume` | Set playback volume (0–100) |
| `/queue` | Show the current queue |
| `/remove` | Remove a track from the queue by position |
| `/nowplaying` | Show details about the current track |
| `/autoplay` | Toggle autoplay of recommended tracks |
| `/bitrate` | Set the audio bitrate (64–384 kbps) |
| `/bitrate-auto` | Automatically match the channel's maximum bitrate |
| `/performance` | Show bot performance metrics |
| `/cache-info` | Show audio cache status |
| `/help` | Show usage help |

## Quick Start

### Requirements

- Python 3.11 or newer
- A system `ffmpeg` executable on `PATH`
- A system Opus library supported by `discord.py`
- Deno, Node.js, or another JavaScript runtime supported by yt-dlp
- A Discord application and bot token for local execution

On macOS, install the system tools with:

```bash
brew install ffmpeg opus deno
```

The `yt-dlp[default]` dependency installs the local EJS helper package. A
supported JavaScript runtime is required at runtime for YouTube signature
processing; the bot exits with a clear setup error if either dependency is
missing. The project does not fetch executable EJS components from GitHub at
runtime.

### Install and Run

```bash
python -m pip install -r requirements.txt
python setup.py        # guided setup: checks dependencies and writes .env
python music_bot.py
```

Prefer manual configuration? Copy `.env.example` to `.env` and fill in the
values yourself (see [Configuration](#configuration)). `python setup.py
--check` verifies dependencies without touching any files.

## Docker

The included Compose setup runs the bot as a non-root user with signed,
deployment-WAN-verified `yt-dlp` image updates:

```bash
cp .env.example .env   # then fill in your Discord bot token
docker compose up -d
```

- Audio is cached to `./music_library` on the host (mounted at
  `/app/cache/audio` in the container).
- `yt-dlp` updates arrive as a new image that CI has already verified against
  real YouTube; `bin/update.sh` pulls it on a timer. See
  [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
- The container runs as UID/GID `1001` by default; override with the
  `APP_UID` / `APP_GID` build args to match your host user.
- The healthcheck becomes healthy only after Discord's `on_ready`; the updater
  requires that state and the exact authorized yt-dlp version or rolls back.

## Configuration

Run `python setup.py` for a guided configuration, or copy `.env.example` to
`.env` and fill in only the values you need:

| Variable | Required | Description |
| --- | --- | --- |
| `DISCORD_TOKEN` | Yes | Bot token from the Discord Developer Portal |
| `BOT_LANGUAGE` | No | Language for in-Discord responses: `en` (default) or `ko` |
| `SPOTIFY_CLIENT_ID` | No | Enables Spotify link resolution via the Spotify Web API |
| `SPOTIFY_CLIENT_SECRET` | No | Paired with `SPOTIFY_CLIENT_ID`; without both, Spotify links fall back to a YouTube search |
| `AUDIO_CACHE_DIR` | No | Audio cache directory (default: `cache/audio`) |
| `OPS_CHANNEL_ID` | No | Channel for operator-only notifications (cache and yt-dlp failures); unset keeps them in the logs |

Never commit `.env`, bot tokens, service credentials, cookies, downloaded
media, or the local `music_library/` cache.

## Project Layout

```text
music_bot.py             # application entry point and Discord commands
setup.py                 # interactive setup wizard (deps check + .env)
src/audio/               # FFmpeg, bitrate, and stream recovery helpers
src/cache/               # optional local audio cache implementation
src/sources/             # source detection and metadata resolvers
src/utils/               # error handling, monitoring, and yt-dlp lifecycle
tests/                   # standalone test scripts
Dockerfile               # container image (non-root, Deno for JS challenges)
docker-compose.yml       # single-service deployment with healthcheck
bin/docker-entrypoint.sh # launches the bot
bin/update.sh            # pulls the published image and restarts
tools/ytdlp_smoke.py     # canary: checks yt-dlp against real YouTube
requirements.txt         # runtime Python dependencies
```

## Scope and Responsible Use

This project is provided as a technical portfolio example. Operators are
responsible for complying with Discord, YouTube, and other service terms, as
well as applicable copyright and privacy laws. The project does not grant
permission to copy, download, or redistribute copyrighted content.

## License

The original code in this repository is licensed under the GNU Affero General
Public License v3.0 only. See [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the dependency notices.
