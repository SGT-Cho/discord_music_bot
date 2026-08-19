#!/bin/bash
# ============================================================================
# Pull the published image and restart the bot if it changed.
#
# This is the deploy half of CI/CD. GitHub cannot reach this machine, so the
# flow is inverted: CI publishes a verified image and this pulls it.
#
# Deliberately not Watchtower. Doing it here means nothing gets access to the
# Docker socket, no third-party agent runs next to the bot, and the whole
# update path is this file.
#
# Run it from a timer (see docs/DEPLOYMENT.md) or by hand at any time. It is
# safe to run when nothing has changed: without a new image it does nothing.
#
#   ./bin/update.sh            # pull and restart if the image changed
#   ./bin/update.sh --force    # restart even when the image is unchanged
# ============================================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

if ! docker info >/dev/null 2>&1; then
    # Expected on a laptop: Docker Desktop may not be running yet after a
    # reboot. Not an error worth alerting on; the next run will catch up.
    log "Docker is not available; skipping this run."
    exit 0
fi

SERVICE="music-bot"
image_id() {
    docker inspect --format '{{.Image}}' discord-music-bot 2>/dev/null || echo "none"
}

before="$(image_id)"

log "Pulling the published image..."
docker compose pull "$SERVICE"

# `up -d` alone would be enough, but comparing image IDs keeps the logs honest
# about whether anything actually changed.
log "Applying..."
docker compose up -d "$SERVICE"

after="$(image_id)"

if [ "$before" != "$after" ]; then
    log "Updated: ${before:0:19} -> ${after:0:19}"
    log "yt-dlp now: $(docker exec discord-music-bot yt-dlp --version 2>/dev/null || echo 'unknown')"
elif [ "$FORCE" = "1" ]; then
    log "No new image; restarting anyway (--force)."
    docker compose restart "$SERVICE"
else
    log "Already up to date."
fi

# Drop images the update superseded. Keeps a laptop from slowly filling up
# without touching anything still referenced by a container.
docker image prune -f >/dev/null 2>&1 || true

log "Done."
