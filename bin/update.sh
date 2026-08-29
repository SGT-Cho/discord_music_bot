#!/bin/bash
# Pull the newest immutable image authorized by a deployment-WAN canary,
# recreate the bot, and roll back if bounded readiness checks do not pass.
set -euo pipefail
umask 077

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="music-bot"
CONTAINER="discord-music-bot"
IMAGE_REPOSITORY="${BOT_IMAGE_REPOSITORY:-ghcr.io/sgt-cho/discord_music_bot}"
READY_TIMEOUT_SECONDS="${MUSICBOT_READY_TIMEOUT_SECONDS:-180}"
ROLLBACK_IMAGE="music-bot-rollback:last-good"
FORCE=0

if [ "${1:-}" = "--force" ]; then
    FORCE=1
elif [ -n "${1:-}" ]; then
    echo "usage: $0 [--force]" >&2
    exit 2
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

if [ "${MUSICBOT_UPDATE_LOCKED:-0}" != "1" ]; then
    lock_dir="${HOME:?HOME must be set}/Library/Caches/musicbot"
    mkdir -p "$lock_dir"
    chmod 700 "$lock_dir"
    set +e
    MUSICBOT_UPDATE_LOCKED=1 /usr/bin/lockf -s -t 0 -k \
        "$lock_dir/update.lock" "$0" "$@"
    lock_status=$?
    set -e
    if [ "$lock_status" = "75" ]; then
        log "Another update is already running; skipping."
        exit 0
    fi
    exit "$lock_status"
fi

image_id() {
    docker inspect --format '{{.Image}}' "$CONTAINER" 2>/dev/null || echo "none"
}

container_version() {
    docker exec "$CONTAINER" python -c \
        'from importlib.metadata import version; print(version("yt-dlp"))' \
        2>/dev/null || true
}

wait_until_ready() {
    expected_version="$1"
    since="$2"
    deadline=$((SECONDS + READY_TIMEOUT_SECONDS))

    while [ "$SECONDS" -lt "$deadline" ]; do
        status="$(docker inspect --format '{{.State.Status}}' "$CONTAINER" 2>/dev/null || true)"
        health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER" 2>/dev/null || true)"

        if [ "$status" = "running" ]; then
            ready=0
            if [ "$health" = "healthy" ]; then
                ready=1
            elif docker logs --since "$since" "$CONTAINER" 2>&1 | grep -q 'Logged in as'; then
                # Compatibility for rolling back to an image built before the
                # ready-marker healthcheck existed.
                ready=1
            fi

            if [ "$ready" = "1" ]; then
                actual_version="$(container_version)"
                if [ -z "$expected_version" ] || [ "$actual_version" = "$expected_version" ]; then
                    sleep 5
                    [ "$(docker inspect --format '{{.State.Status}}' "$CONTAINER" 2>/dev/null || true)" = "running" ] && return 0
                fi
            fi
        elif [ "$status" = "exited" ] || [ "$status" = "dead" ]; then
            return 1
        fi
        sleep 5
    done
    return 1
}

resolve_target() {
    TARGET_IMAGE=""
    TARGET_VERSION=""
    TARGET_COMMIT=""

    log "Fetching main and immutable deployment authorizations..."
    git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main
    git fetch --tags origin

    while IFS= read -r commit; do
        while IFS= read -r tag; do
            [ -n "$tag" ] || continue
            # The release gate only creates lightweight tags. Annotated or
            # malformed refs are not deployment authorizations.
            [ "$(git cat-file -t "refs/tags/$tag" 2>/dev/null || true)" = "commit" ] || continue
            version="$(python3 tools/ytdlp_release.py verify-tag "$tag" "$commit" 2>/dev/null || true)"
            [ -n "$version" ] || continue

            image="$IMAGE_REPOSITORY:ytdlp-$version-sha-$commit"
            log "Trying authorized image $image..."
            if docker pull "$image"; then
                TARGET_IMAGE="$image"
                TARGET_VERSION="$version"
                TARGET_COMMIT="$commit"
                return 0
            fi
            log "Authorized image is not available yet; checking the previous authorization."
        done < <(git tag --points-at "$commit" --list 'deploy-ytdlp-v*--*' | LC_ALL=C sort -r)
    done < <(git rev-list --first-parent refs/remotes/origin/main)

    return 1
}

if ! docker info >/dev/null 2>&1; then
    # Docker Desktop may still be starting after login. A nonzero exit keeps
    # this visible in launchd instead of silently delaying the next attempt.
    log "Docker is not available."
    exit 1
fi

cd "$PROJECT_DIR"
if ! resolve_target; then
    log "No pullable image backed by a valid deployment authorization was found."
    exit 1
fi

before="$(image_id)"
before_version="$(container_version)"
target_id="$(docker image inspect --format '{{.Id}}' "$TARGET_IMAGE")"

if [ "$before" != "none" ]; then
    # Keep exactly one easy-to-name recovery image; do not prune it on success.
    docker image tag "$before" "$ROLLBACK_IMAGE"
fi

deploy_since="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
log "Applying $TARGET_IMAGE (authorized source $TARGET_COMMIT)..."

compose_args=(up -d --no-deps)
if [ "$FORCE" = "1" ] || [ "$before" != "$target_id" ]; then
    compose_args+=(--force-recreate)
fi
compose_args+=("$SERVICE")

deploy_ok=1
if ! BOT_IMAGE="$TARGET_IMAGE" docker compose "${compose_args[@]}"; then
    deploy_ok=0
elif ! wait_until_ready "$TARGET_VERSION" "$deploy_since"; then
    deploy_ok=0
fi

if [ "$deploy_ok" != "1" ]; then
    log "New container did not become ready with yt-dlp $TARGET_VERSION; rolling back."
    if [ "$before" = "none" ]; then
        log "No previous image exists for rollback."
        exit 1
    fi

    rollback_since="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    BOT_IMAGE="$ROLLBACK_IMAGE" docker compose up -d --no-deps --force-recreate "$SERVICE"
    if wait_until_ready "$before_version" "$rollback_since"; then
        log "Rollback succeeded: $before ($before_version)."
    else
        log "CRITICAL: rollback container did not become ready; inspect Docker logs immediately."
    fi
    exit 1
fi

after="$(image_id)"
if [ "$before" = "$after" ] && [ "$FORCE" != "1" ]; then
    log "Already running the newest authorized image."
else
    log "Updated: ${before:0:19} -> ${after:0:19}"
fi
log "Ready with yt-dlp $(container_version) from $TARGET_IMAGE."
