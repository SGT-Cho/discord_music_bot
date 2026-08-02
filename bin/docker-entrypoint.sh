#!/bin/bash
# ============================================================================
# music-bot entrypoint
#
# Runs two processes in one container:
#   1) supercronic — periodic yt-dlp updates (user-level cron, no root needed)
#   2) "$@"        — Dockerfile CMD (default: python music_bot.py)
#
# Shutdown policy:
#   - If either process dies, wait -n returns and the container exits.
#   - docker-compose's restart: unless-stopped brings the container back up.
#   - This is the "auto-restart the bot after a yt-dlp update" mechanism.
#
# To drop cron when porting this to another bot (e.g. Gemini):
#   - Delete the config/crontab file; the if block below then skips supercronic.
#   - The entrypoint itself can stay as-is (CMD still runs normally).
# ============================================================================
set -e

CRON_PID=""
BOT_PID=""

term_handler() {
    [ -n "$CRON_PID" ] && kill -TERM "$CRON_PID" 2>/dev/null || true
    [ -n "$BOT_PID"  ] && kill -TERM "$BOT_PID"  2>/dev/null || true
    wait 2>/dev/null || true
    exit 143
}
trap term_handler SIGTERM SIGINT

if [ -f /app/config/crontab ]; then
    echo "[entrypoint] starting supercronic with /app/config/crontab"
    supercronic /app/config/crontab &
    CRON_PID=$!
else
    echo "[entrypoint] /app/config/crontab not found — skipping cron"
fi

# Shameless plug in the container logs.
echo "[entrypoint] ⭐ Enjoying the bot? A GitHub star would make our day!"
echo "[entrypoint]    https://github.com/SGT-Cho/discord_music_bot"

"$@" &
BOT_PID=$!

# Returns as soon as either child exits (bash 4.3+)
wait -n
EXIT_CODE=$?
echo "[entrypoint] a child exited (code=$EXIT_CODE) — shutting down container"
[ -n "$CRON_PID" ] && kill -TERM "$CRON_PID" 2>/dev/null || true
[ -n "$BOT_PID"  ] && kill -TERM "$BOT_PID"  2>/dev/null || true
wait 2>/dev/null || true
exit $EXIT_CODE
