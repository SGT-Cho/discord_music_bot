# Python 3.11 slim base
FROM python:3.11-slim

# ----------------------------------------------------------------------------
# Non-root user UID/GID
#   - Matches the host ubuntu user (=1001:1001) to avoid permission conflicts
#     on the bind mount (music_library).
#   - Override with --build-arg APP_UID=... when building in other environments.
# ----------------------------------------------------------------------------
ARG APP_UID=1001
ARG APP_GID=1001

WORKDIR /app

# System packages
#   ffmpeg/libopus    : required for voice playback
#   ca-certificates   : HTTPS for pip / yt-dlp
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg libopus0 libopus-dev \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Deno (EJS runtime for YouTube JS challenges = signature / n-sig solver)
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

# ----------------------------------------------------------------------------
# Create non-root user
# ----------------------------------------------------------------------------
RUN groupadd --gid ${APP_GID} botuser && \
    useradd  --uid ${APP_UID} --gid botuser --create-home --shell /bin/bash botuser

# ----------------------------------------------------------------------------
# Install Python dependencies inside a venv.
#   - The venv is owned by botuser so no step needs root.
#   - System site-packages stay untouched, so no root privileges are needed.
# ----------------------------------------------------------------------------
RUN python -m venv /app/venv && \
    chown -R botuser:botuser /app

USER botuser
ENV PATH="/app/venv/bin:/home/botuser/.local/bin:$PATH" \
    VIRTUAL_ENV="/app/venv" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

# Which yt-dlp to install, in order of precedence:
#
#   YT_DLP_VERSION=2026.08.18.122307  exact pin, stable or nightly
#   YT_DLP_CHANNEL=nightly            newest pre-release
#   (neither)                         newest stable, per requirements.txt
#
# Nightly is not a preference for bleeding edge. YouTube breaks extraction
# faster than the stable cadence ships fixes, and a stable release that cannot
# fetch audio is worth less than a nightly that can. CI pins the exact version
# its smoke test just verified against real YouTube, so an image ships a
# known-good extractor and a rollback is redeploying the previous tag.
ARG YT_DLP_VERSION=""
ARG YT_DLP_CHANNEL="stable"

COPY --chown=botuser:botuser requirements.txt .
# --upgrade : always pull the latest even with >= pins (avoids stale versions from cached images)
RUN pip install --no-cache-dir --upgrade -r requirements.txt && \
    if [ -n "$YT_DLP_VERSION" ]; then \
        pip install --no-cache-dir --force-reinstall "yt-dlp[default]==${YT_DLP_VERSION}"; \
    elif [ "$YT_DLP_CHANNEL" = "nightly" ]; then \
        pip install --no-cache-dir --upgrade --pre "yt-dlp[default]"; \
    fi && \
    echo "built with yt-dlp $(yt-dlp --version)"

COPY --chown=botuser:botuser . .

# Ensure the entrypoint is executable (works without chmod on the host)
RUN chmod +x /app/bin/docker-entrypoint.sh

ENTRYPOINT ["/app/bin/docker-entrypoint.sh"]
CMD ["python", "music_bot.py"]
