# Deployment

## Trust boundary

YouTube frequently rejects GitHub-hosted Azure IPs even when yt-dlp works on
the bot's real network. For that reason, GitHub's scheduled canary is an
observational signal only. It can report a hosted-runner regression, but it
cannot publish an image.

The release gate runs in an isolated container on the deployment Mac and uses
the same WAN as the bot. It receives no `.env`, Discord token, browser cookies,
SSH key, host directories, or Docker socket. Only exit code `0` authorizes a
release; hard failures and inconclusive probes both leave the running bot
untouched.

```text
push / PR -> GitHub CI -> lint + offline tests + multi-arch build

04:00 / 16:00 local
  -> bin/release-ytdlp.sh
  -> archive the fetched origin/main commit
  -> resolve exact yt-dlp PEP 440 version
  -> build an isolated native candidate
  -> extraction + short media read + FFmpeg decode + full download
  -> pass: SSH-sign and push deploy-ytdlp-v<version>--<full-commit>

authorization tag
  -> verify tag, commit ancestry, exact version, lint, and offline tests
  -> publish multi-arch GHCR image with a version + full-SHA tag

00:00 / 06:00 / 12:00 / 18:00 local
  -> bin/update.sh chooses the newest authorization on main
  -> verify OCI revision/version labels and pin the pulled sha256 digest
  -> run the release gate again against that exact digest on the deployment WAN
  -> recreate -> wait for Discord ready + exact yt-dlp version
  -> pass: keep running; fail: recreate the previous image
```

The convenience `latest`, `sha-<short>`, and `ytdlp-<version>` image tags are
published for humans. Automated deployment resolves the full authorization tag
once, records its first observed digest, and runs Compose with
`repository@sha256:...`. Before activation, that exact platform image must pass
the same live YouTube checks on the deployment WAN. A later registry-tag
mutation is rejected. The lookup tag is:

```text
ghcr.io/sgt-cho/discord_music_bot:ytdlp-<PEP440-version>-sha-<full-40-char-commit>
```

The GitHub workflow also requires the signed tag to point at the exact current
`main` tip. Together these checks prevent a slow or replayed workflow from
moving a host backward.

## Canary checks

`tools/ytdlp_smoke.py` performs four release-gating checks:

| Check | What it proves |
| --- | --- |
| `extraction` | A stable public video resolves to playable audio metadata. |
| `stream_read` | The signed media URL accepts yt-dlp's real headers. |
| `ffmpeg_decode` | FFmpeg can open and decode that URL. |
| `full_download` | yt-dlp and FFmpeg can download and transcode a complete track. |

The short stream read is intentionally bounded. On the deployment WAN, a 21 MB
sequential read takes roughly 11–14 minutes even when healthy. That historical
session-cap check remains available as a non-gating long diagnostic:

```bash
SMOKE_LONG_SESSION_CHECK=1 \
SMOKE_TOTAL_DEADLINE_SECONDS=1500 \
python tools/ytdlp_smoke.py
```

Exit codes are `0` for pass, `1` for a functional yt-dlp failure, and `2` for
an inconclusive environment/canary failure. The launchd gate publishes only on
`0`.

Nightly CLI output omits the `.dev0` suffix required by pip. Release metadata
therefore always comes from `importlib.metadata.version("yt-dlp")`; for example,
the exact distribution pin is `2026.8.27.231323.dev0`, not
`2026.08.27.231323`.

## One-time GitHub setup

The host uses its repository-scoped SSH deploy key only to fetch/push refs and a
separate local Ed25519 key only to sign authorization tags. The signing public
key is pinned in `config/release_allowed_signers`; neither private key is a GHCR
credential.

Before enabling the tag job, configure repository rules in GitHub's Settings:

1. Protect `main` from direct updates and require changes through the normal
   reviewed/status-checked path. Do not give the write deploy key a `main`
   bypass.
2. Add a tag ruleset for `deploy-ytdlp-v*` that blocks tag updates, deletion,
   and force changes. Authorization tags are append-only; never move one.

The first successful publish creates the GHCR package as private. This
code-only public repository uses no private payload, so set the package
visibility to **Public** once under **Packages -> Package settings -> Change
visibility**. The host can then pull anonymously without storing a broad PAT.

If a tag was authorized but the publish workflow failed, use **Actions ->
Publish verified image -> Re-run failed jobs**. Do not delete, recreate, or
force-update the tag.

## macOS host installation

The checked-in plists contain the actual checkout path and Colima Docker
context for this headless host. They explicitly target the per-user
`Background` session because an SSH-managed Mac has no `gui/<uid>` launchd
domain. Install both jobs with modern launchd commands:

```bash
mkdir -p ~/Library/LaunchAgents ~/Library/Logs ~/Library/Caches/musicbot \
  ~/Library/"Application Support"/musicbot
chmod 700 ~/Library/Caches/musicbot ~/Library/"Application Support"/musicbot
cp config/com.musicbot.canary.plist ~/Library/LaunchAgents/
cp config/com.musicbot.update.plist ~/Library/LaunchAgents/

launchctl bootstrap user/$(id -u) ~/Library/LaunchAgents/com.musicbot.canary.plist
launchctl bootstrap user/$(id -u) ~/Library/LaunchAgents/com.musicbot.update.plist
```

Run either once without waiting for its calendar:

```bash
launchctl kickstart -k user/$(id -u)/com.musicbot.canary
launchctl kickstart -k user/$(id -u)/com.musicbot.update
```

Inspect status and logs:

```bash
launchctl print user/$(id -u)/com.musicbot.canary
launchctl print user/$(id -u)/com.musicbot.update
tail -f ~/Library/Logs/musicbot-canary.log
tail -f ~/Library/Logs/musicbot-update.log
```

To uninstall:

```bash
launchctl bootout user/$(id -u)/com.musicbot.canary
launchctl bootout user/$(id -u)/com.musicbot.update
```

The canary runs under `caffeinate`, so an idle sleep does not interrupt a
candidate build or probe. Both scripts use kernel-backed `lockf` locks to make
manual and scheduled invocations mutually exclusive.

## Manual operations

Run a release probe or update immediately:

```bash
./bin/release-ytdlp.sh nightly
./bin/update.sh
```

`./bin/update.sh --force` recreates the newest authorized digest even when its
image ID is unchanged. Before activation it writes an atomic transaction record
and keeps a unique local rollback tag. A successful candidate must remain
healthy for a stability window with the exact yt-dlp version and no restart.
Failure restores the prior image and quarantines the rejected digest; an
interrupted transaction is recovered on the next run.

The shared process lock lives in `~/Library/Caches/musicbot/`. Durable updater
state (the transaction journal, authorization-to-digest pins, and rejected
digest quarantine) lives in `~/Library/Application Support/musicbot/`, which
macOS does not treat as purgeable cache data. To deliberately retry a
previously rejected digest after investigating it, remove only its exact line
from `rejected-digests.txt`; do not delete or move the signed Git tag.

The image build arguments remain available for local development:

| Argument | Effect |
| --- | --- |
| `YT_DLP_VERSION` | Exact stable/nightly PEP 440 pin; wins over channel. |
| `YT_DLP_CHANNEL` | `nightly` or `stable` floating development build. |

```bash
YT_DLP_VERSION=2026.8.27.231323.dev0 \
  docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

For a fork, set `BOT_IMAGE_REPOSITORY=ghcr.io/<owner>/<repo>` in the host
environment and give the checkout a repository-scoped SSH deploy key for that
fork. A floating channel build can reuse a Docker cache layer; use
`docker compose build --no-cache` when intentionally refreshing it. Release
builds use an exact version and do not have that ambiguity.

## Security notes

- Never add Google cookies, browser sessions, PO tokens, proxies, or bypass
  providers to this public repository.
- Neither candidate containers nor the bot receive the SSH deploy key or
  Docker socket.
- No deployment credential is stored in GitHub; publication uses the scoped
  workflow `GITHUB_TOKEN`, and deployment pulls a public code image.
- Watchtower is intentionally absent. `bin/update.sh` is the complete,
  auditable Docker-socket consumer and preserves a bounded rollback path.
