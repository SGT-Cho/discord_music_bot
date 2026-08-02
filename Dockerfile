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

# supercronic version pin (user-level cron for containers)
#   https://github.com/aptible/supercronic
#   - Single static Go binary, no root required, logs to stdout
ARG SUPERCRONIC_VERSION=v0.2.33

WORKDIR /app

# System packages
#   ffmpeg/libopus    : required for voice playback
#   procps            : pkill used by the cron job
#   ca-certificates   : HTTPS for pip / yt-dlp / supercronic
#   wget              : downloads the supercronic binary (removed in a later step)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg libopus0 libopus-dev \
        procps \
        ca-certificates \
        wget && \
    rm -rf /var/lib/apt/lists/*

# Deno (EJS runtime for YouTube JS challenges = signature / n-sig solver)
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

# Install supercronic — fetched directly via HTTPS from github.com.
#   For checksum verification, grab the sha1sum from
#   https://github.com/aptible/supercronic/releases and add
#   `echo "<sum>  /usr/local/bin/supercronic" | sha1sum -c -`.
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        arm64)  sc_arch="linux-arm64"  ;; \
        amd64)  sc_arch="linux-amd64"  ;; \
        *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    wget -q -O /usr/local/bin/supercronic \
        "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-${sc_arch}"; \
    chmod +x /usr/local/bin/supercronic; \
    apt-get purge -y --auto-remove wget; \
    rm -rf /var/lib/apt/lists/*

# ----------------------------------------------------------------------------
# Create non-root user
# ----------------------------------------------------------------------------
RUN groupadd --gid ${APP_GID} botuser && \
    useradd  --uid ${APP_UID} --gid botuser --create-home --shell /bin/bash botuser

# ----------------------------------------------------------------------------
# Install Python dependencies inside a venv.
#   - The venv is owned by botuser, so the cron job (=botuser) can self-update yt-dlp.
#   - System site-packages stay untouched, so no root privileges are needed.
# ----------------------------------------------------------------------------
RUN python -m venv /app/venv && \
    chown -R botuser:botuser /app

USER botuser
ENV PATH="/app/venv/bin:/home/botuser/.local/bin:$PATH" \
    VIRTUAL_ENV="/app/venv" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

COPY --chown=botuser:botuser requirements.txt .
# --upgrade : always pull the latest even with >= pins (avoids stale versions from cached images)
RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY --chown=botuser:botuser . .

# Ensure the entrypoint is executable (works without chmod on the host)
RUN chmod +x /app/bin/docker-entrypoint.sh

# The ENTRYPOINT launches CMD as a child process and runs supercronic in the background alongside it.
ENTRYPOINT ["/app/bin/docker-entrypoint.sh"]
CMD ["python", "music_bot.py"]
