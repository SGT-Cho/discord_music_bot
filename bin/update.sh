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
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-music-bot}"
export COMPOSE_PROJECT_NAME
CACHE_DIR="${HOME:?HOME must be set}/Library/Caches/musicbot"
STATE_DIR="$HOME/Library/Application Support/musicbot"
PIPELINE_LOCK="$CACHE_DIR/pipeline.lock"
PENDING_FILE="$STATE_DIR/deploy-pending.tsv"
AUTH_DIGESTS_FILE="$STATE_DIR/authorization-digests.tsv"
REJECTED_FILE="$STATE_DIR/rejected-digests.txt"
ALLOWED_SIGNERS="$PROJECT_DIR/config/release_allowed_signers"
FORCE=0

case "$READY_TIMEOUT_SECONDS" in
    ''|*[!0-9]*)
        echo "MUSICBOT_READY_TIMEOUT_SECONDS must be an integer from 1 to 86400." >&2
        exit 2
        ;;
esac
if [ "${#READY_TIMEOUT_SECONDS}" -gt 5 ]; then
    echo "MUSICBOT_READY_TIMEOUT_SECONDS must be an integer from 1 to 86400." >&2
    exit 2
fi
READY_TIMEOUT_SECONDS=$((10#$READY_TIMEOUT_SECONDS))
if [ "$READY_TIMEOUT_SECONDS" -lt 1 ] || [ "$READY_TIMEOUT_SECONDS" -gt 86400 ]; then
    echo "MUSICBOT_READY_TIMEOUT_SECONDS must be an integer from 1 to 86400." >&2
    exit 2
fi

if [ "${1:-}" = "--force" ]; then
    FORCE=1
elif [ -n "${1:-}" ]; then
    echo "usage: $0 [--force]" >&2
    exit 2
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

mkdir -p "$CACHE_DIR" "$STATE_DIR"
chmod 700 "$CACHE_DIR" "$STATE_DIR"

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

load_container_snapshot() {
    snapshot=""
    if snapshot="$(docker container inspect --format \
        '{{.Id}}|{{.Image}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}|{{.State.StartedAt}}' \
        "$CONTAINER" 2>/dev/null)"; then
        :
    else
        listed="$(docker container ls --all --quiet \
            --filter "name=^/${CONTAINER}$" 2>/dev/null)" || return 1
        [ -z "$listed" ] || return 1
        snapshot="none|none|absent|none|0|-"
    fi

    IFS='|' read -r SNAPSHOT_CONTAINER_ID SNAPSHOT_IMAGE_ID SNAPSHOT_STATUS \
        SNAPSHOT_HEALTH SNAPSHOT_RESTARTS SNAPSHOT_STARTED_AT SNAPSHOT_EXTRA \
        <<< "$snapshot"
    [ -z "${SNAPSHOT_EXTRA:-}" ] && [ -n "$SNAPSHOT_CONTAINER_ID" ] && \
        [ -n "$SNAPSHOT_IMAGE_ID" ] && [ -n "$SNAPSHOT_STATUS" ] && \
        [ -n "$SNAPSHOT_HEALTH" ] && [ -n "$SNAPSHOT_RESTARTS" ] && \
        [ -n "$SNAPSHOT_STARTED_AT" ]
}

image_version() {
    docker image inspect --format \
        '{{index .Config.Labels "org.opencontainers.image.version"}}' \
        "$1" 2>/dev/null || true
}

container_version() {
    docker exec "$CONTAINER" python -c \
        'from importlib.metadata import version; print(version("yt-dlp"))' \
        2>/dev/null || true
}

legacy_log_ready() {
    since="$1"
    docker logs --since "$since" "$CONTAINER" 2>&1 | \
        awk 'index($0, "Logged in as") { found = 1 } END { exit !found }'
}

ready_now() {
    expected_version="$1"
    since="$2"
    allow_legacy_logs="${3:-0}"
    [ -n "$expected_version" ] || return 1

    load_container_snapshot || return 1
    status="$SNAPSHOT_STATUS"
    [ "$status" = "running" ] || return 1
    health="$SNAPSHOT_HEALTH"
    if [ "$health" != "healthy" ]; then
        [ "$allow_legacy_logs" = "1" ] && legacy_log_ready "$since" || return 1
    fi
    [ "$(container_version)" = "$expected_version" ]
}

wait_until_ready() {
    expected_version="$1"
    since="$2"
    allow_legacy_logs="${3:-0}"
    [ -n "$expected_version" ] || return 1
    deadline=$((SECONDS + READY_TIMEOUT_SECONDS))

    while [ "$SECONDS" -lt "$deadline" ]; do
        if ! load_container_snapshot; then
            sleep 5
            continue
        fi
        status="$SNAPSHOT_STATUS"
        health="$SNAPSHOT_HEALTH"

        ready=0
        if [ "$status" = "running" ] && [ "$health" = "healthy" ]; then
            ready=1
        elif [ "$status" = "running" ] && [ "$allow_legacy_logs" = "1" ] && legacy_log_ready "$since"; then
            ready=1
        elif [ "$status" = "exited" ] || [ "$status" = "dead" ]; then
            return 1
        fi

        if [ "$ready" = "1" ] && [ "$(container_version)" = "$expected_version" ]; then
            stable_container_id="$SNAPSHOT_CONTAINER_ID"
            stable_image_id="$SNAPSHOT_IMAGE_ID"
            stable_restarts="$SNAPSHOT_RESTARTS"
            sleep 15

            load_container_snapshot || return 1
            [ "$SNAPSHOT_CONTAINER_ID" = "$stable_container_id" ] || return 1
            [ "$SNAPSHOT_IMAGE_ID" = "$stable_image_id" ] || return 1
            [ "$SNAPSHOT_RESTARTS" = "$stable_restarts" ] || return 1
            [ "$SNAPSHOT_STATUS" = "running" ] || return 1
            [ "$(container_version)" = "$expected_version" ] || return 1

            if [ "$allow_legacy_logs" = "1" ]; then
                final_health="$SNAPSHOT_HEALTH"
                [ "$final_health" = "healthy" ] || legacy_log_ready "$since" || return 1
            else
                [ "$SNAPSHOT_HEALTH" = "healthy" ] || return 1
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
    [ ! -d "$destination" ] || return 1
    temporary="$destination.tmp.$$"
    printf '%s' "$1" > "$temporary" || return 1
    shift
    while [ "$#" -gt 0 ]; do
        printf '\t%s' "$1" >> "$temporary" || return 1
        shift
    done
    printf '\n' >> "$temporary" || return 1
    chmod 600 "$temporary" || return 1
    mv -f "$temporary" "$destination" || return 1
    [ -f "$destination" ]
}

append_record() {
    destination="$1"
    first="$2"
    second="${3:-}"
    [ ! -d "$destination" ] || return 1
    temporary="$destination.tmp.$$"
    if [ -f "$destination" ]; then
        cp "$destination" "$temporary" || return 1
    else
        : > "$temporary" || return 1
    fi
    if [ -n "$second" ]; then
        printf '%s\t%s\n' "$first" "$second" >> "$temporary" || return 1
    else
        printf '%s\n' "$first" >> "$temporary" || return 1
    fi
    chmod 600 "$temporary" || return 1
    mv -f "$temporary" "$destination" || return 1
    [ -f "$destination" ]
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
        append_record "$AUTH_DIGESTS_FILE" "$tag" "$digest" || return 1
    fi
}

reject_digest() {
    digest="$1"
    if [ ! -f "$REJECTED_FILE" ] || ! grep -Fqx -- "$digest" "$REJECTED_FILE"; then
        append_record "$REJECTED_FILE" "$digest" || return 1
    fi
}

resolve_target() {
    TARGET_IMAGE=""
    TARGET_VERSION=""
    TARGET_COMMIT=""
    TARGET_TAG=""

    log "Fetching main and signed deployment authorizations..."
    git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main || {
        log "Could not refresh origin/main; refusing stale deployment state."
        return 1
    }
    git fetch --no-tags --prune origin \
        '+refs/tags/deploy-ytdlp-v*:refs/musicbot-deploy/deploy-ytdlp-v*' || {
        log "Could not refresh deployment authorizations; refusing stale tag state."
        return 1
    }

    while IFS= read -r commit; do
        best_key=""
        best_tag=""
        best_version=""

        while IFS=$'\t' read -r object_type _object_name ref_name; do
            [ "$object_type" = "tag" ] || continue
            ref_commit="$(git rev-parse "$ref_name^{commit}" 2>/dev/null || true)"
            [ "$ref_commit" = "$commit" ] || continue
            tag="${ref_name#refs/musicbot-deploy/}"
            [ "$(git for-each-ref --format='%(tag)' "$ref_name")" = "$tag" ] || continue
            git -c gpg.format=ssh \
                -c gpg.ssh.allowedSignersFile="$ALLOWED_SIGNERS" \
                verify-tag "$ref_name" >/dev/null 2>&1 || continue

            version="$(python3 tools/ytdlp_release.py verify-tag "$tag" "$commit" 2>/dev/null || true)"
            [ -n "$version" ] || continue
            key="$(python3 tools/ytdlp_release.py sort-key "$version")" || continue
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
    load_container_snapshot || return 1
    [ "$SNAPSHOT_IMAGE_ID" = "$previous_id" ]
}

remove_failed_candidate() {
    if ! docker container rm --force "$CONTAINER" >/dev/null 2>&1; then
        load_container_snapshot || return 1
        [ "$SNAPSHOT_CONTAINER_ID" = "none" ] || return 1
    fi
    load_container_snapshot || return 1
    [ "$SNAPSHOT_CONTAINER_ID" = "none" ]
}

record_deployment_phase() {
    deployment_phase="$1"
    atomic_record "$PENDING_FILE" \
        "v3" "$deployment_phase" "$rollback_ref" "$before" \
        "$before_container_id" "${before_version:--}" \
        "$TARGET_IMAGE" "$TARGET_VERSION"
}

record_recovery_phase() {
    recovery_phase="$1"
    atomic_record "$PENDING_FILE" \
        "v3" "$recovery_phase" "$previous_ref" "$previous_image_id" \
        "${previous_container_id:-none}" "${previous_version:--}" \
        "$candidate_ref" "$candidate_version" || return 1
    journal_phase="$recovery_phase"
    journal_version="v3"
}

clear_deployment_journal() {
    authorized_candidate="$1"
    if [ "$journal_phase" = "rollback-rejected" ]; then
        if [ ! -f "$REJECTED_FILE" ] || \
            ! grep -Fqx -- "$authorized_candidate" "$REJECTED_FILE"; then
            log "CRITICAL: refusing to clear the journal before its rejection is durable."
            return 1
        fi
    fi
    rm -f -- "$PENDING_FILE"
}

recover_interrupted_deployment() {
    [ -f "$PENDING_FILE" ] || return 0
    IFS=$'\t' read -r field1 field2 field3 field4 field5 field6 field7 field8 extra \
        < "$PENDING_FILE" || return 1

    if [ "$field1" = "v3" ]; then
        journal_version="v3"
        journal_phase="$field2"
        previous_ref="$field3"
        previous_image_id="$field4"
        previous_container_id="$field5"
        previous_version="$field6"
        candidate_ref="$field7"
        candidate_version="$field8"
        [ -z "${extra:-}" ] || return 1
        case "$journal_phase" in
            activating|rollback-required|rollback-rejected) ;;
            *) return 1 ;;
        esac
        [ -n "$previous_container_id" ] || return 1
    elif [ "$field1" = "v2" ]; then
        journal_version="v2"
        journal_phase="activating"
        previous_ref="$field2"
        previous_image_id="$field3"
        previous_container_id="$field4"
        previous_version="$field5"
        candidate_ref="$field6"
        candidate_version="$field7"
        [ -z "${field8:-}" ] && [ -z "${extra:-}" ] || return 1
        [ -n "$previous_container_id" ] || return 1
    else
        journal_version="v1"
        journal_phase="activating"
        previous_ref="$field1"
        previous_image_id="$field2"
        previous_container_id=""
        previous_version="$field3"
        candidate_ref="$field4"
        candidate_version="$field5"
        [ -z "${field6:-}" ] && [ -z "${field7:-}" ] && [ -z "${field8:-}" ] && \
            [ -z "${extra:-}" ] || return 1
        log "Recovering a legacy v1 deployment journal."
    fi

    [ -n "$previous_ref" ] && [ -n "$previous_image_id" ] && \
        [ -n "$candidate_ref" ] && [ -n "$candidate_version" ] || return 1
    candidate_version="$(python3 tools/ytdlp_release.py validate-version "$candidate_version")" || return 1
    [ "$previous_version" != "-" ] || previous_version=""

    if [ "$previous_ref" != "none" ]; then
        if [ -z "$previous_version" ]; then
            previous_version="$(image_version "$previous_image_id")"
            [ -n "$previous_version" ] || previous_version="$(image_version "$previous_ref")"
        fi
        previous_version="$(python3 tools/ytdlp_release.py validate-version "$previous_version")" || {
            log "Could not recover the previous yt-dlp version from the deployment journal or image."
            return 1
        }
    else
        [ "$previous_image_id" = "none" ] || return 1
        previous_version=""
    fi

    if ! load_container_snapshot; then
        log "CRITICAL: Docker could not provide a reliable container snapshot."
        return 1
    fi
    current_container_id="$SNAPSHOT_CONTAINER_ID"
    current_image_id="$SNAPSHOT_IMAGE_ID"
    current_started_at="$SNAPSHOT_STARTED_AT"
    candidate_image_id="$(docker image inspect --format '{{.Id}}' "$candidate_ref" 2>/dev/null || true)"
    candidate_was_rejected=0
    if [ "$journal_phase" = "rollback-rejected" ] || \
        { [ -f "$REJECTED_FILE" ] && grep -Fqx -- "$candidate_ref" "$REJECTED_FILE"; }; then
        candidate_was_rejected=1
    fi

    if [ "$journal_phase" = "rollback-rejected" ] && \
        { [ -z "$candidate_image_id" ] || [ "$candidate_image_id" != "$previous_image_id" ]; }; then
        if ! reject_digest "$candidate_ref"; then
            log "WARNING: could not persist the candidate rejection during recovery."
        fi
    fi

    if [ "$previous_ref" = "none" ] && [ "$current_container_id" = "none" ]; then
        log "Clearing a transaction with no previous or candidate container present."
        clear_deployment_journal "$candidate_ref" || return 1
        return 0
    fi

    if { [ "$journal_version" = "v2" ] || [ "$journal_version" = "v3" ]; } && \
        [ "$current_container_id" = "$previous_container_id" ]; then
        if [ "$current_image_id" = "$previous_image_id" ] && \
            ready_now "$previous_version" "$current_started_at" 1 && \
            wait_until_ready "$previous_version" "$current_started_at" 1; then
            log "Clearing a transaction interrupted before candidate activation."
            clear_deployment_journal "$candidate_ref" || return 1
            return 0
        fi
        log "The previous container changed state during activation; recreating it."
        if [ "$previous_ref" != "none" ] && \
            rollback_to "$previous_ref" "$previous_image_id" "$previous_version"; then
            clear_deployment_journal "$candidate_ref" || return 1
            return 0
        fi
    fi

    if [ "$current_container_id" = "none" ] && [ "$previous_ref" != "none" ]; then
        log "Restoring the previous image after an interrupted container recreation..."
        if rollback_to "$previous_ref" "$previous_image_id" "$previous_version"; then
            clear_deployment_journal "$candidate_ref" || return 1
            return 0
        fi
    fi

    if [ "$previous_ref" != "none" ] && \
        [ "$current_image_id" = "$previous_image_id" ]; then
        log "Recovering an interrupted rollback to the previous image..."
        if ready_now "$previous_version" "$current_started_at" 1 && \
            wait_until_ready "$previous_version" "$current_started_at" 1; then
            clear_deployment_journal "$candidate_ref" || return 1
            return 0
        fi
        if rollback_to "$previous_ref" "$previous_image_id" "$previous_version"; then
            clear_deployment_journal "$candidate_ref" || return 1
            return 0
        fi
    fi

    if [ -n "$candidate_image_id" ] && [ "$current_image_id" = "$candidate_image_id" ]; then
        log "Recovering an interrupted candidate readiness decision..."
        if [ "$journal_phase" = "activating" ] && \
            [ "$candidate_was_rejected" = "0" ] && \
            wait_until_ready "$candidate_version" "$current_started_at" 0; then
            clear_deployment_journal "$candidate_ref" || return 1
            return 0
        fi

        if [ "$journal_phase" = "activating" ]; then
            if [ "$candidate_image_id" != "$previous_image_id" ]; then
                recovery_failure_phase="rollback-rejected"
            else
                recovery_failure_phase="rollback-required"
            fi
            record_recovery_phase "$recovery_failure_phase" || {
                log "CRITICAL: could not persist the recovery readiness decision."
                return 1
            }
        fi

        if [ "$journal_phase" = "rollback-rejected" ]; then
            if ! reject_digest "$candidate_ref"; then
                log "WARNING: could not persist the rejected digest quarantine."
            fi
        elif [ "$candidate_image_id" = "$previous_image_id" ]; then
            log "The recreated container used the prior digest; it was not quarantined."
        else
            log "The candidate is being rolled back for infrastructure reasons; it was not quarantined."
        fi
        if [ "$previous_ref" = "none" ]; then
            log "Removing the failed initial candidate; there is no previous deployment to restore."
            if remove_failed_candidate; then
                clear_deployment_journal "$candidate_ref" || return 1
                return 0
            fi
        fi
        if [ "$previous_ref" != "none" ] && rollback_to "$previous_ref" "$previous_image_id" "$previous_version"; then
            clear_deployment_journal "$candidate_ref" || return 1
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

if ! load_container_snapshot; then
    log "Docker could not provide a reliable pre-deployment container snapshot."
    exit 1
fi
before="$SNAPSHOT_IMAGE_ID"
before_container_id="$SNAPSHOT_CONTAINER_ID"
before_version=""
if [ "$before" != "none" ]; then
    before_version="$(container_version)"
    [ -n "$before_version" ] || before_version="$(image_version "$before")"
    before_version="$(python3 tools/ytdlp_release.py validate-version "$before_version")" || {
        log "Could not determine the current container's yt-dlp version; refusing activation."
        exit 1
    }
fi
target_id="$(docker image inspect --format '{{.Id}}' "$TARGET_IMAGE" 2>/dev/null)" || {
    log "Could not inspect the authorized target image after pulling it."
    exit 1
}
[ -n "$target_id" ] || {
    log "The authorized target image has no local image ID."
    exit 1
}

# The deployment-WAN release probe initially authorizes source + version. The
# registry image is rebuilt by GitHub, so exercise the exact pulled digest on
# this host before it is allowed to replace the running container.
if [ "$before" != "$target_id" ]; then
    log "Running the release gate against the exact published digest..."
    set +e
    docker run --rm \
        --entrypoint python \
        -e SMOKE_TOTAL_DEADLINE_SECONDS=600 \
        -e SMOKE_STREAM_DEADLINE_SECONDS=120 \
        "$TARGET_IMAGE" \
        tools/ytdlp_smoke.py
    smoke_status=$?
    set -e

    if [ "$smoke_status" = "1" ]; then
        if ! reject_digest "$TARGET_IMAGE"; then
            log "WARNING: could not persist the rejected digest quarantine."
        fi
        log "Published digest failed its deployment-WAN release gate; keeping the current deployment."
        exit 1
    elif [ "$smoke_status" != "0" ]; then
        log "Published digest probe was inconclusive (exit $smoke_status); keeping the current deployment."
        exit 1
    fi
fi

if [ "$before" = "none" ]; then
    rollback_ref="none"
else
    before_suffix="${before#sha256:}"
    rollback_ref="music-bot-rollback:pre-${before_suffix:0:12}"
    docker image tag "$before" "$rollback_ref"
fi

record_deployment_phase "activating"
journal_phase="activating"

deploy_since="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
log "Applying $TARGET_IMAGE ($TARGET_TAG, source $TARGET_COMMIT)..."

compose_args=(up -d --no-deps)
if [ "$FORCE" = "1" ] || [ "$before" != "$target_id" ]; then
    compose_args+=(--force-recreate)
fi
compose_args+=("$SERVICE")

deploy_result="ok"
if ! BOT_IMAGE="$TARGET_IMAGE" docker compose "${compose_args[@]}"; then
    deploy_result="infrastructure"
elif ! wait_until_ready "$TARGET_VERSION" "$deploy_since" 0; then
    deploy_result="readiness"
    if [ "$before" != "$target_id" ]; then
        failure_phase="rollback-rejected"
    else
        failure_phase="rollback-required"
    fi
    record_deployment_phase "$failure_phase" || {
        log "CRITICAL: could not persist the failed readiness decision."
        exit 1
    }
    journal_phase="$failure_phase"
elif ! load_container_snapshot; then
    deploy_result="infrastructure"
elif [ "$SNAPSHOT_IMAGE_ID" != "$target_id" ]; then
    deploy_result="infrastructure"
fi

if [ "$deploy_result" != "ok" ]; then
    log "New digest did not become stably healthy with yt-dlp $TARGET_VERSION; assessing rollback."
    if ! load_container_snapshot; then
        log "CRITICAL: Docker could not provide a reliable failure snapshot; leaving the journal intact."
        exit 1
    fi
    current_container_after_failure="$SNAPSHOT_CONTAINER_ID"
    current_image_after_failure="$SNAPSHOT_IMAGE_ID"
    current_status_after_failure="$SNAPSHOT_STATUS"
    current_started_at="$SNAPSHOT_STARTED_AT"

    if [ "$deploy_result" = "infrastructure" ] && \
        [ "$current_image_after_failure" = "$target_id" ]; then
        case "$current_status_after_failure" in
            created|restarting|exited|dead) deploy_result="activation" ;;
        esac
    fi

    failure_phase="rollback-required"
    if { [ "$deploy_result" = "readiness" ] || [ "$deploy_result" = "activation" ]; } && \
        [ "$before" != "$target_id" ]; then
        failure_phase="rollback-rejected"
    fi
    if [ "$journal_phase" != "$failure_phase" ]; then
        record_deployment_phase "$failure_phase" || {
            log "CRITICAL: could not persist the rollback decision; leaving the journal intact."
            exit 1
        }
        journal_phase="$failure_phase"
    fi

    if [ "$deploy_result" = "readiness" ] || [ "$deploy_result" = "activation" ]; then
        if [ "$before" != "$target_id" ]; then
            if ! reject_digest "$TARGET_IMAGE"; then
                log "WARNING: could not persist the rejected digest quarantine."
            fi
        else
            log "The recreated container used the prior digest; it was not quarantined."
        fi
    else
        log "Deployment infrastructure failed; the digest itself was not quarantined."
    fi

    if [ "$deploy_result" = "infrastructure" ] && \
        [ "$current_container_after_failure" = "$before_container_id" ] && \
        [ "$current_image_after_failure" = "$before" ] && \
        ready_now "$before_version" "$current_started_at" 1 && \
        wait_until_ready "$before_version" "$current_started_at" 1; then
        clear_deployment_journal "$TARGET_IMAGE" || \
            log "WARNING: could not clear the deployment journal."
        log "The existing deployment remained active; no rollback recreation was needed."
    elif [ "$before" != "none" ] && rollback_to "$rollback_ref" "$before" "$before_version"; then
        clear_deployment_journal "$TARGET_IMAGE" || \
            log "WARNING: rollback succeeded but the deployment journal remains."
        log "Rollback succeeded: $before ($before_version)."
    elif [ "$before" = "none" ] && remove_failed_candidate; then
        clear_deployment_journal "$TARGET_IMAGE" || \
            log "WARNING: cleanup succeeded but the deployment journal remains."
        log "Removed the failed initial candidate; no previous deployment existed."
    else
        log "CRITICAL: rollback did not become ready; inspect Docker logs immediately."
    fi
    exit 1
fi

load_container_snapshot || {
    log "CRITICAL: Docker could not provide the final deployment snapshot."
    exit 1
}
after="$SNAPSHOT_IMAGE_ID"
[ "$after" = "$target_id" ] || {
    log "CRITICAL: the ready container is not running the authorized target image."
    exit 1
}
clear_deployment_journal "$TARGET_IMAGE" || exit 1
if [ "$before" = "$after" ] && [ "$FORCE" != "1" ]; then
    log "Already running the newest authorized digest."
else
    log "Updated: ${before:0:19} -> ${after:0:19}"
fi
log "Ready with yt-dlp $(container_version) from $TARGET_IMAGE."
