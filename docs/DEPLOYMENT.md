# Deployment

## Why it works this way

The bot's hardest dependency is not a library version, it is YouTube. Extraction
breaks when YouTube changes something, on YouTube's schedule, with no relation
to anything in this repository. A build that passed last week can fail today
without a single line changing.

That shapes the whole pipeline:

- **CI** answers "did this change break the code?" — it never touches the
  network, so a red build means a real regression.
- **The yt-dlp canary** answers "does the current yt-dlp still work against real
  YouTube?" — it runs on a schedule rather than on commits, because that is when
  the answer changes.
- **Deployment is pull-based.** The bot runs on a laptop that GitHub cannot
  reach, so CI publishes an image and the machine fetches it.

Only a verified image is ever published, so the running bot moves from one
known-good extractor to the next instead of gambling on whatever was newest.

## The pipeline

```
push / PR ──► CI ──► lint · compile · 30 offline tests · multi-arch build
                     (no network: failures mean the code broke)

schedule ──► canary ──► install newest yt-dlp
              │         run tools/ytdlp_smoke.py against real YouTube
              │
              ├─ pass ────► publish ──► ghcr.io/…:latest
              │                          ghcr.io/…:ytdlp-<version>
              │                          ghcr.io/…:sha-<commit>
              │
              ├─ fail ────► publish nothing, report
              │             (the running bot keeps the version it has)
              │
              └─ inconclusive ─► publish nothing, report
                                 (canary needs attention — not a verdict)

every 6h ──► bin/update.sh on the host ──► docker compose pull && up -d
```

The three canary outcomes are deliberately distinct. A removed test video and a
genuine yt-dlp regression both stop a deployment, but only one of them means
anything is wrong with yt-dlp, and treating them the same trains you to ignore
the alert.

## What the canary actually checks

`tools/ytdlp_smoke.py` exercises the two paths the bot uses, because they fail
independently:

| Check | Mirrors | Catches |
| --- | --- | --- |
| `extraction` | metadata lookup | extractor breakage |
| `stream_read` | FFmpeg reading a stream URL during playback | session caps, 403s on the media URL |
| `ffmpeg_decode` | `FFmpegOptimizer` header passing | header/UA mismatches that 403 the CDN |
| `full_download` | `AudioCacheManager` | download + transcode regressions |

`stream_read` deliberately reads past 20 MB. YouTube has cut media sessions
around that boundary, and FFmpeg cannot re-issue an expired URL — the symptom is
a long track stopping partway with nothing in the logs the listener can act on.

Run it by hand any time playback breaks and you need to know whether the cause
is upstream:

```bash
python tools/ytdlp_smoke.py
```

Exit codes: `0` pass, `1` yt-dlp regressed, `2` the canary itself needs fixing.

## Host setup

One-time, on the machine that runs the bot:

```bash
cp .env.example .env    # fill in DISCORD_TOKEN
docker compose up -d
```

Then install the update timer:

```bash
cp config/com.musicbot.update.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.musicbot.update.plist
```

The plist has an absolute path to `bin/update.sh` baked in, because launchd
expands neither `~` nor `$HOME`. If your checkout lives somewhere other than
`/Users/winter/Documents/discord-bot/music-bot`, edit that path first. Check on
it with `launchctl list | grep musicbot` and `tail -f /tmp/musicbot-update.log`.

launchd rather than cron because this runs on a laptop — if the machine is
asleep at the scheduled time, launchd runs the job once on wake instead of
skipping the window.

On a Linux host, a `cron` entry does the same job:

```
0 */6 * * * /path/to/music-bot/bin/update.sh >> /var/log/musicbot-update.log 2>&1
```

### Why not Watchtower

Watchtower is the usual answer for pull-based updates, and it needs
`/var/run/docker.sock` mounted into a third-party container — which grants that
container full control of Docker, and through it the host. `bin/update.sh` does
the same job in about thirty lines you can read in full, running as your own
user, with nothing extra listening.

## Updating by hand

```bash
./bin/update.sh
```

Safe to run any time; without a new image it does nothing. Add `--force` to
restart even when the image has not changed.

## Rolling back

Every published image keeps a `ytdlp-<version>` tag, so a rollback is a tag
change rather than a rebuild:

```bash
# in .env
BOT_IMAGE="ghcr.io/sgt-cho/discord_music_bot:ytdlp-2026.08.18.122307"
```

```bash
docker compose up -d
```

To go back to tracking the newest verified build, remove `BOT_IMAGE` and run
`./bin/update.sh`.

## Choosing a yt-dlp version locally

The image takes two build args:

| Arg | Effect |
| --- | --- |
| `YT_DLP_VERSION` | Exact pin, stable or nightly. Wins over the channel. |
| `YT_DLP_CHANNEL` | `nightly` for the newest pre-release, `stable` (default) otherwise. |

```bash
YT_DLP_VERSION=2026.08.18.122307 \
  docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

Nightly is not a taste for the bleeding edge. YouTube breaks extraction faster
than the stable channel ships fixes, so a stable release that cannot fetch audio
is worth less than a nightly that can. The canary is what makes this safe: a
nightly only reaches the bot after passing against real YouTube.

## Optional: notifications

Two mechanisms, because they run in different places — GitHub Actions has no
access to the bot's Discord connection, and the bot has no idea CI exists:

| Where | Setting | Covers |
| --- | --- | --- |
| From the bot | `OPS_CHANNEL_ID` in `.env` | cache download failures, playback recovery errors |
| From CI | repository secrets, below | canary verdicts, publish outcomes |

Both are optional. Without them the information stays in the logs and in the
workflow run summary.

### Choosing how CI reaches you

`tools/notify_discord.py` takes the first route that is configured:

| Route | Secrets | Notes |
| --- | --- | --- |
| Webhook → channel | `OPS_DISCORD_WEBHOOK` | Preferred. A leaked webhook can only post to that one channel. |
| Bot → channel | `OPS_DISCORD_BOT_TOKEN` + `OPS_DISCORD_CHANNEL_ID` | |
| Bot → DM | `OPS_DISCORD_BOT_TOKEN` + `OPS_DISCORD_USER_ID` | The only way to get a DM: Discord has no webhook that can DM. |

Create a webhook in **Server Settings → Integrations → Webhooks**; the URL is
the whole configuration.

For a DM you need `OPS_DISCORD_USER_ID`, your own numeric Discord user ID —
enable **Settings → Advanced → Developer Mode**, then right-click your name and
*Copy User ID*. The bot must share a server with you for the DM to go through.

Weigh the DM route before taking it. A webhook URL that leaks lets someone post
in one channel; a bot token that leaks lets someone act as that bot in every
server it has joined. If you want DMs, a second bot that exists only to deliver
these notifications is a better trade than handing CI the token the music bot
runs on.

## Repository secrets

| Secret | Required | Purpose |
| --- | --- | --- |
| `GITHUB_TOKEN` | automatic | pushes images to GHCR |
| `OPS_DISCORD_WEBHOOK` | no | canary and publish notifications, to a channel |
| `OPS_DISCORD_BOT_TOKEN` | no | alternative to the webhook; needed for DMs |
| `OPS_DISCORD_CHANNEL_ID` | no | with the bot token, posts to this channel |
| `OPS_DISCORD_USER_ID` | no | with the bot token, sends a DM to this user |

Set them with `gh secret set OPS_DISCORD_WEBHOOK`, or under **Settings →
Secrets and variables → Actions**.

No deployment credentials are stored in GitHub. Because the flow is pull-based,
CI never needs access to the machine running the bot — which also means a
compromised workflow cannot reach it directly.
