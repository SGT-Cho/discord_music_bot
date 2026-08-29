#!/bin/bash
# ============================================================================
# music-bot entrypoint
#
# Runs the bot and nothing else. yt-dlp updates used to happen in-container on
# a cron schedule; they now arrive as a new image built by CI, which verifies
# yt-dlp against real YouTube before publishing. See docs/DEPLOYMENT.md.
#
# `exec` replaces this shell with the bot process, so Python becomes PID 1 and
# receives SIGTERM directly from `docker stop` — no forwarding needed.
# ============================================================================
set -e

# Shameless plug in the container logs.
echo "[entrypoint] ⭐ Enjoying the bot? A GitHub star would make our day!"
echo "[entrypoint]    https://github.com/SGT-Cho/discord_music_bot"
echo "[entrypoint] yt-dlp $(yt-dlp --version 2>/dev/null || echo 'unknown')"

rm -f /tmp/musicbot-ready
exec "$@"
