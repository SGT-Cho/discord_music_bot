#!/bin/bash
# Pull the newest signed deployment authorization by immutable registry digest,
# recreate the bot, and transactionally roll back if readiness does not pass.
set -euo pipefail
umask 077

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="music-bot"
CONTAINER="discord-music-bot"
IMAGE_REPOSITORY="${BOT_IMAGE_REPOSITORY:-ghcr.io/sgt-cho/discord_music_bot}"
READY_TIMEOUT_SECONDS="${MUSICBOT_READY_TIMEOUT_SECONDS:-180}"
STATE_DIR="${HOME:?HOME must be set}/Library/Caches/musicbot"
PIPELINE_LOCK="$STATE_DIR/pipeline.lock"
PENDING_FILE="$STATE_DIR/deploy-pending.tsv"
LAST_GOOD_FILE="$STATE_DIR/last-good.tsv"
AUTH_DIGESTS_FILE="$STATE_DIR/authorization-digests.tsv"
REJECTED_FILE="$STATE_DIR/rejected-digests.txt"
ALLOWED_SIGNERS="$PROJECT_DIR/config/release_allowed_signers"
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

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"

# The release probe and updater share one lock: both can be scheduled together
# after wake, but only one may fetch/build/pull through Colima at a time.
if [ "${MUSICBOT_UPDATE_LOCKED:-0}" != "1" ]; then
    set +e
    MUSICBOT_UPDATE_LOCKED=1 /usr/bin/lockf -s -t 0 -k \
        "$PIPELINE_LOCK" "$0" "$@"
    lock_status=$?
    set -e
    if [ "$lock_status" = "75" ]; then
        log "Another release or update operation is already running; skipping."
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

legacy_log_ready() {
    since="$1"
    logs="$(docker logs --since "$since" "$CONTAINER" 2>&1 || true)"
    case "$logs" in
        *"Logged in as"*) return 0 ;;
        *) return 1 ;;
    esac
}

wait_until_ready() {
    expected_version="$1"
    since="$2"
    allow_legacy_logs="${3:-0}"
    deadline=$((SECONDS + READY_TIMEOUT_SECONDS))

    while [ "$SECONDS" -lt "$deadline" ]; do
        status="$(docker inspect --format '{{.State.Status}}' "$CONTAINER" 2>/dev/null || true)"
        health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER" 2>/dev/null || true)"

        ready=0
        if [ "$status" = "running" ] && [ "$health" = "healthy" ]; then
            ready=1
        elif [ "$status" = "running" ] && [ "$allow_legacy_logs" = "1" ] && legacy_log_ready "$since"; then
            ready=1
        elif [ "$status" = "exited" ] || [ "$status" = "dead" ]; then
            return 1
        fi

        if [ "$ready" = "1" ] && [ "$(container_version)" = "$expected_version" ]; then
            stable_container_id="$(docker inspect --format '{{.Id}}' "$CONTAINER")"
            stable_image_id="$(image_id)"
            stable_restarts="$(docker inspect --format '{{.RestartCount}}' "$CONTAINER")"
            sleep 15

            [ "$(docker inspect --format '{{.Id}}' "$CONTAINER" 2>/dev/null || true)" = "$stable_container_id" ] || return 1
            [ "$(image_id)" = "$stable_image_id" ] || return 1
            [ "$(docker inspect --format '{{.RestartCount}}' "$CONTAINER" 2>/dev/null || true)" = "$stable_restarts" ] || return 1
            [ "$(docker inspect --format '{{.State.Status}}' "$CONTAINER" 2>/dev/null || true)" = "running" ] || return 1
            [ "$(container_version)" = "$expected_version" ] || return 1

            if [ "$allow_legacy_logs" = "1" ]; then
                final_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER" 2>/dev/null || true)"
                [ "$final_health" = "healthy" ] || legacy_log_ready "$since" || return 1
            else
                [ "$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || true)" = "healthy" ] || return 1
            fi
            return 0
        fi
        sleep 5
    done
    return 1
}

atomic_record() {
    destination="$1"
    shift
    temporary="$destination.tmp.$$"
    printf '%s' "$1" > "$temporary"
    shift
    while [ "$#" -gt 0 ]; do
        printf '\t%s' "$1" >> "$temporary"
        shift
    done
    printf '\n' >> "$temporary"
    chmod 600 "$temporary"
    mv -f "$temporary" "$destination"
}

append_record() {
    destination="$1"
    first="$2"
    second="${3:-}"
    temporary="$destination.tmp.$$"
    if [ -f "$destination" ]; then
        cp "$destination" "$temporary"
    else
        : > "$temporary"
    fi
    if [ -n "$second" ]; then
        printf '%s\t%s\n' "$first" "$second" >> "$temporary"
    else
        printf '%s\n' "$first" >> "$temporary"
    fi
    chmod 600 "$temporary"
    mv -f "$temporary" "$destination"
}

pin_authorization_digest() {
    tag="$1"
    digest="$2"
    known=""
    if [ -f "$AUTH_DIGESTS_FILE" ]; then
        known="$(awk -F '\t' -v wanted="$tag" '$1 == wanted {print $2; exit}' "$AUTH_DIGESTS_FILE")"
    fi
    if [ -n "$known" ] && [ "$known" != "$digest" ]; then
        log "Registry tag mutation detected for $tag: $known -> $digest"
        return 1
    fi
    if [ -z "$known" ]; then
        append_record "$AUTH_DIGESTS_FILE" "$tag" "$digest"
    fi
}

reject_digest() {
    digest="$1"
    if [ ! -f "$REJECTED_FILE" ] || ! grep -Fqx -- "$digest" "$REJECTED_FILE"; then
        append_record "$REJECTED_FILE" "$digest"
    fi
}

resolve_target() {
    TARGET_IMAGE=""
    TARGET_VERSION=""
    TARGET_COMMIT=""
    TARGET_TAG=""

    log "Fetching main and signed deployment authorizations..."
    git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main
    git fetch --no-tags --prune origin \
        '+refs/tags/deploy-ytdlp-v*:refs/musicbot-deploy/deploy-ytdlp-v*'

    while IFS= read -r commit; do
        best_key=""
        best_tag=""
        best_version=""

        while IFS=$'\t' read -r object_type _object_name ref_name; do
            [ "$object_type" = "tag" ] || continue
            ref_commit="$(git rev-parse "$ref_name^{commit}" 2>/dev/null || true)"
            [ "$ref_commit" = "$commit" ] || continue
            git -c gpg.format=ssh \
                -c gpg.ssh.allowedSignersFile="$ALLOWED_SIGNERS" \
                verify-tag "$ref_name" >/dev/null 2>&1 || continue

            tag="${ref_name#refs/musicbot-deploy/}"
            version="$(python3 tools/ytdlp_release.py verify-tag "$tag" "$commit" 2>/dev/null || true)"
            [ -n "$version" ] || continue
            key="$(python3 tools/ytdlp_release.py sort-key "$version")"
            if [ -z "$best_key" ] || [[ "$key" > "$best_key" ]]; then
                best_key="$key"
                best_tag="$tag"
                best_version="$version"
            fi
        done < <(git for-each-ref \
            --format='%(objecttype)%09%(objectname)%09%(refname)' \
            refs/musicbot-deploy/)

        [ -n "$best_tag" ] || continue
        candidate_tag="$IMAGE_REPOSITORY:ytdlp-$best_version-sha-$commit"
        log "Pulling newest authorization $candidate_tag..."
        if ! docker pull "$candidate_tag"; then
            log "Newest authorized image is unavailable; keeping the current deployment."
            return 1
        fi

        revision="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$candidate_tag" 2>/dev/null || true)"
        image_version="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' "$candidate_tag" 2>/dev/null || true)"
        if [ "$revision" != "$commit" ] || [ "$image_version" != "$best_version" ]; then
            log "Image label mismatch; refusing $candidate_tag."
            return 1
        fi

        digest_ref=""
        while IFS= read -r repo_digest; do
            case "$repo_digest" in
                "$IMAGE_REPOSITORY"@sha256:*)
                    digest_ref="$repo_digest"
                    break
                    ;;
            esac
        done < <(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$candidate_tag")
        if [ -z "$digest_ref" ]; then
            log "Pulled image has no immutable RepoDigest; refusing deployment."
            return 1
        fi

        pin_authorization_digest "$best_tag" "$digest_ref" || return 1
        if [ -f "$REJECTED_FILE" ] && grep -Fqx -- "$digest_ref" "$REJECTED_FILE"; then
            log "Newest authorized digest was previously rejected; keeping the current deployment."
            return 1
        fi

        TARGET_IMAGE="$digest_ref"
        TARGET_VERSION="$best_version"
        TARGET_COMMIT="$commit"
        TARGET_TAG="$best_tag"
        return 0
    done < <(git rev-list --first-parent refs/remotes/origin/main)

    return 1
}

rollback_to() {
    previous_ref="$1"
    previous_id="$2"
    previous_version="$3"
    rollback_since="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

    if ! BOT_IMAGE="$previous_ref" docker compose up -d --no-deps --force-recreate "$SERVICE"; then
        return 1
    fi
    wait_until_ready "$previous_version" "$rollback_since" 1 || return 1
    [ "$(image_id)" = "$previous_id" ]
}

recover_interrupted_deployment() {
    [ -f "$PENDING_FILE" ] || return 0
    IFS=$'\t' read -r previous_ref previous_id previous_version candidate_ref candidate_version < "$PENDING_FILE" || return 1
    [ -n "$previous_ref" ] && [ -n "$previous_id" ] && [ -n "$candidate_ref" ] && [ -n "$candidate_version" ] || return 1
    candidate_version="$(python3 tools/ytdlp_release.py validate-version "$candidate_version")" || return 1
    [ "$previous_version" != "-" ] || previous_version=""

    current_id="$(image_id)"
    candidate_id="$(docker image inspect --format '{{.Id}}' "$candidate_ref" 2>/dev/null || true)"
    if [ "$current_id" = "$previous_id" ]; then
        log "Clearing a transaction interrupted before candidate activation."
        rm -f -- "$PENDING_FILE"
        return 0
    fi

    if [ -n "$candidate_id" ] && [ "$current_id" = "$candidate_id" ]; then
        log "Recovering an interrupted candidate readiness decision..."
        if wait_until_ready "$candidate_version" '1970-01-01T00:00:00Z' 0; then
            atomic_record "$LAST_GOOD_FILE" "$candidate_ref" "$candidate_version"
            rm -f -- "$PENDING_FILE"
            return 0
        fi

        reject_digest "$candidate_ref"
        if [ "$previous_ref" != "none" ] && rollback_to "$previous_ref" "$previous_id" "$previous_version"; then
            atomic_record "$LAST_GOOD_FILE" "$previous_ref" "${previous_version:--}"
            rm -f -- "$PENDING_FILE"
            return 0
        fi
    fi

    log "CRITICAL: interrupted deployment could not be recovered automatically."
    return 1
}

if ! docker info >/dev/null 2>&1; then
    log "Docker is not available."
    exit 1
fi

cd "$PROJECT_DIR"
recover_interrupted_deployment || exit 1

if ! resolve_target; then
    log "No deployable image backed by the newest valid authorization was found."
    exit 1
fi

before="$(image_id)"
before_version="$(container_version)"
if [ "$before" = "none" ]; then
    rollback_ref="none"
else
    before_suffix="${before#sha256:}"
    rollback_ref="music-bot-rollback:pre-${before_suffix:0:12}"
    docker image tag "$before" "$rollback_ref"
fi

target_id="$(docker image inspect --format '{{.Id}}' "$TARGET_IMAGE")"
atomic_record "$PENDING_FILE" \
    "$rollback_ref" "$before" "${before_version:--}" "$TARGET_IMAGE" "$TARGET_VERSION"

deploy_since="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
log "Applying $TARGET_IMAGE ($TARGET_TAG, source $TARGET_COMMIT)..."

compose_args=(up -d --no-deps)
if [ "$FORCE" = "1" ] || [ "$before" != "$target_id" ]; then
    compose_args+=(--force-recreate)
fi
compose_args+=("$SERVICE")

deploy_ok=1
if ! BOT_IMAGE="$TARGET_IMAGE" docker compose "${compose_args[@]}"; then
    deploy_ok=0
elif ! wait_until_ready "$TARGET_VERSION" "$deploy_since" 0; then
    deploy_ok=0
fi

if [ "$deploy_ok" != "1" ]; then
    log "New digest did not become stably healthy with yt-dlp $TARGET_VERSION; rolling back."
    reject_digest "$TARGET_IMAGE"
    if [ "$before" != "none" ] && rollback_to "$rollback_ref" "$before" "$before_version"; then
        atomic_record "$LAST_GOOD_FILE" "$rollback_ref" "${before_version:--}"
        rm -f -- "$PENDING_FILE"
        log "Rollback succeeded: $before ($before_version)."
    else
        log "CRITICAL: rollback did not become ready; inspect Docker logs immediately."
    fi
    exit 1
fi

atomic_record "$LAST_GOOD_FILE" "$TARGET_IMAGE" "$TARGET_VERSION"
rm -f -- "$PENDING_FILE"
after="$(image_id)"
if [ "$before" = "$after" ] && [ "$FORCE" != "1" ]; then
    log "Already running the newest authorized digest."
else
    log "Updated: ${before:0:19} -> ${after:0:19}"
fi
log "Ready with yt-dlp $(container_version) from $TARGET_IMAGE."
