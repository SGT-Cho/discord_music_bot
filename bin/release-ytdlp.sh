#!/bin/bash
# Build and smoke-test an exact yt-dlp release on the deployment WAN. A pass
# authorizes GitHub to publish it by creating one immutable lightweight tag.
set -euo pipefail
umask 077

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHANNEL="${1:-nightly}"
LOCK_DIR="${HOME:?HOME must be set}/Library/Caches/musicbot"
LOCK_FILE="$LOCK_DIR/release-ytdlp.lock"
TEMP_ROOT="${TMPDIR:-/tmp}"
TEMP_ROOT="${TEMP_ROOT%/}"
TEMP_DIR=""
CANDIDATE_IMAGE=""

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
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

REMOTE_TAG_SHA="$(git ls-remote --tags origin "refs/tags/$TAG" | awk 'NR == 1 {print $1}')"
if [ -n "$REMOTE_TAG_SHA" ]; then
    if [ "$REMOTE_TAG_SHA" = "$SOURCE_SHA" ]; then
        log "$TAG already authorizes this source; nothing to do."
        log "If its publish run failed, use GitHub Actions 'Re-run failed jobs'; never move the tag."
        exit 0
    fi
    log "Refusing conflicting remote tag $TAG ($REMOTE_TAG_SHA)."
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
if ! git push origin "$SOURCE_SHA:refs/tags/$TAG"; then
    REMOTE_TAG_SHA="$(git ls-remote --tags origin "refs/tags/$TAG" | awk 'NR == 1 {print $1}')"
    if [ "$REMOTE_TAG_SHA" = "$SOURCE_SHA" ]; then
        log "A concurrent run created the same authorization tag."
        exit 0
    fi
    log "Tag push failed and no matching remote authorization exists."
    exit 1
fi

log "Authorized yt-dlp $VERSION at $SOURCE_SHA. GitHub will now test and publish it."
