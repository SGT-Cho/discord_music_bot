#!/bin/bash
# Build and smoke-test an exact yt-dlp release on the deployment WAN. A pass
# authorizes GitHub to publish it by creating one signed, append-only tag.
set -euo pipefail
umask 077

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHANNEL="${1:-nightly}"
LOCK_DIR="${HOME:?HOME must be set}/Library/Caches/musicbot"
LOCK_FILE="$LOCK_DIR/pipeline.lock"
SIGNING_KEY="${MUSICBOT_RELEASE_SIGNING_KEY:-/Users/winter/.ssh/sgt_cho_musicbot_release_signing_ed25519}"
ALLOWED_SIGNERS="$PROJECT_DIR/config/release_allowed_signers"
TEMP_ROOT="${TMPDIR:-/tmp}"
TEMP_ROOT="${TEMP_ROOT%/}"
TEMP_DIR=""
CANDIDATE_IMAGE=""

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

verify_remote_tag() {
    remote_lines="$(git ls-remote --tags origin "refs/tags/$TAG" "refs/tags/$TAG^{}")" || return 1
    remote_object="$(printf '%s\n' "$remote_lines" | awk -v ref="refs/tags/$TAG" '$2 == ref {print $1; exit}')"
    remote_commit="$(printf '%s\n' "$remote_lines" | awk -v ref="refs/tags/$TAG^{}" '$2 == ref {print $1; exit}')"

    [ -n "$remote_object" ] || return 1
    [ "$remote_commit" = "$SOURCE_SHA" ] || return 1

    # A per-process ref prevents a failed fetch from falling back to an object
    # left behind by an earlier verification attempt.
    check_ref="refs/musicbot-release-check/$$/$TAG"
    git fetch --no-tags origin "+refs/tags/$TAG:$check_ref" >/dev/null || return 1

    verified=0
    if [ "$(git cat-file -t "$check_ref" 2>/dev/null || true)" = "tag" ] && \
        [ "$(git for-each-ref --format='%(tag)' "$check_ref")" = "$TAG" ] && \
        git -c gpg.format=ssh \
            -c gpg.ssh.allowedSignersFile="$ALLOWED_SIGNERS" \
            verify-tag "$check_ref" >/dev/null 2>&1; then
        verified=1
    fi
    git update-ref -d "$check_ref" >/dev/null 2>&1 || true
    [ "$verified" = "1" ]
}

cleanup() {
    status=$?
    trap - EXIT

    if [ -n "$CANDIDATE_IMAGE" ]; then
        docker image rm "$CANDIDATE_IMAGE" >/dev/null 2>&1 || true
    fi

    case "$TEMP_DIR" in
        "$TEMP_ROOT"/musicbot-release.*)
            rm -rf -- "$TEMP_DIR"
            ;;
        "") ;;
        *) log "Refusing to remove unexpected temporary path: $TEMP_DIR" ;;
    esac

    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

case "$CHANNEL" in
    nightly|stable) ;;
    *)
        echo "usage: $0 [nightly|stable]" >&2
        exit 2
        ;;
esac

# Re-enter under a kernel-backed lock. lockf releases it automatically even if
# the worker is killed; unlike a PID file it cannot go stale or be PID-reused.
if [ "${MUSICBOT_RELEASE_LOCKED:-0}" != "1" ]; then
    mkdir -p "$LOCK_DIR"
    chmod 700 "$LOCK_DIR"
    set +e
    MUSICBOT_RELEASE_LOCKED=1 /usr/bin/lockf -s -t 0 -k "$LOCK_FILE" "$0" "$@"
    lock_status=$?
    set -e
    if [ "$lock_status" = "75" ]; then
        log "Another yt-dlp release probe is already running; skipping."
        exit 0
    fi
    exit "$lock_status"
fi

if ! docker info >/dev/null 2>&1; then
    log "Docker is not available."
    exit 1
fi

if [ ! -r "$SIGNING_KEY" ] || [ ! -r "$ALLOWED_SIGNERS" ]; then
    log "Release signing key or allowed-signers file is unavailable."
    exit 1
fi

cd "$PROJECT_DIR"
log "Fetching origin/main over the repository's SSH deploy key..."
git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main
SOURCE_SHA="$(git rev-parse --verify 'refs/remotes/origin/main^{commit}')"

TEMP_DIR="$(mktemp -d "$TEMP_ROOT/musicbot-release.XXXXXX")"
SOURCE_DIR="$TEMP_DIR/source"
mkdir -p "$SOURCE_DIR"
git archive "$SOURCE_SHA" | tar -x -C "$SOURCE_DIR"

log "Resolving the newest $CHANNEL yt-dlp distribution version..."
if [ "$CHANNEL" = "nightly" ]; then
    PIP_SPEC='yt-dlp[default]'
    PIP_FLAGS='--pre'
else
    PIP_SPEC='yt-dlp[default]'
    PIP_FLAGS=''
fi

VERSION="$(docker run --rm \
    -e PIP_SPEC="$PIP_SPEC" \
    -e PIP_FLAGS="$PIP_FLAGS" \
    python:3.11-slim \
    /bin/sh -ec '
        python -m pip install --disable-pip-version-check --no-cache-dir --upgrade $PIP_FLAGS "$PIP_SPEC" >&2
        python -c '\''from importlib.metadata import version; print(version("yt-dlp"))'\''
    ')"
VERSION="$(python3 "$PROJECT_DIR/tools/ytdlp_release.py" validate-version "$VERSION")"
TAG="$(python3 "$PROJECT_DIR/tools/ytdlp_release.py" tag "$VERSION" "$SOURCE_SHA")"

REMOTE_TAG_OBJECT="$(git ls-remote --tags origin "refs/tags/$TAG" | awk 'NR == 1 {print $1}')"
if [ -n "$REMOTE_TAG_OBJECT" ]; then
    if verify_remote_tag; then
        log "$TAG already authorizes this source; nothing to do."
        log "If its publish run failed, use GitHub Actions 'Re-run failed jobs'; never move the tag."
        exit 0
    fi
    log "Refusing conflicting, unsigned, or incorrectly targeted remote tag $TAG."
    exit 1
fi

SHORT_SHA="${SOURCE_SHA:0:12}"
CANDIDATE_IMAGE="music-bot-canary:${VERSION}-${SHORT_SHA}"
log "Building isolated candidate $CANDIDATE_IMAGE from origin/main $SOURCE_SHA..."
docker build \
    --network host \
    --build-arg "YT_DLP_VERSION=$VERSION" \
    --label "com.musicbot.canary=true" \
    --label "org.opencontainers.image.revision=$SOURCE_SHA" \
    --label "org.opencontainers.image.version=$VERSION" \
    --tag "$CANDIDATE_IMAGE" \
    "$SOURCE_DIR"

INSTALLED_VERSION="$(docker run --rm --entrypoint python "$CANDIDATE_IMAGE" \
    -c 'from importlib.metadata import version; print(version("yt-dlp"))')"
if [ "$INSTALLED_VERSION" != "$VERSION" ]; then
    log "Candidate version mismatch: expected $VERSION, installed $INSTALLED_VERSION"
    exit 1
fi

log "Running the release gate on the deployment WAN..."
docker run --rm \
    --entrypoint python \
    -e SMOKE_TOTAL_DEADLINE_SECONDS=600 \
    -e SMOKE_STREAM_DEADLINE_SECONDS=120 \
    "$CANDIDATE_IMAGE" \
    tools/ytdlp_smoke.py

# A long probe must not publish a stale main commit as latest.
git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main
CURRENT_MAIN="$(git rev-parse --verify 'refs/remotes/origin/main^{commit}')"
if [ "$CURRENT_MAIN" != "$SOURCE_SHA" ]; then
    log "origin/main advanced to $CURRENT_MAIN during the probe; discarding the stale result."
    exit 0
fi

log "Probe passed; authorizing publication with $TAG..."
if git show-ref --verify --quiet "refs/tags/$TAG"; then
    [ "$(git rev-parse "refs/tags/$TAG^{commit}")" = "$SOURCE_SHA" ]
    [ "$(git for-each-ref --format='%(tag)' "refs/tags/$TAG")" = "$TAG" ]
    git -c gpg.format=ssh \
        -c gpg.ssh.allowedSignersFile="$ALLOWED_SIGNERS" \
        verify-tag "$TAG" >/dev/null
else
    git -c gpg.format=ssh \
        -c user.signingkey="$SIGNING_KEY" \
        -c user.name=musicbot-release \
        -c user.email=musicbot-release@localhost \
        tag -s "$TAG" "$SOURCE_SHA" \
        -m "Authorize yt-dlp $VERSION after deployment-WAN canary"
fi

# Including main as a no-op refspec makes the tag creation atomic with the
# observed main tip. If main advances after the final fetch, the non-fast-
# forward main refspec rejects the whole push instead of authorizing stale code.
if ! git push --atomic origin \
    "$SOURCE_SHA:refs/heads/main" \
    "refs/tags/$TAG:refs/tags/$TAG"; then
    if verify_remote_tag; then
        log "A concurrent run created the same authorization tag."
        exit 0
    fi
    log "Tag push failed and no matching remote authorization exists."
    exit 1
fi

log "Authorized yt-dlp $VERSION at $SOURCE_SHA. GitHub will now test and publish it."
