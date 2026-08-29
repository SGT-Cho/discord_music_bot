#!/usr/bin/env python3
"""Canary check: does the current yt-dlp still work against real YouTube?

Run by the ytdlp-canary workflow before a new yt-dlp release is allowed into a
deployed image, and useful by hand when playback breaks and you need to know
whether the cause is upstream.

It exercises the two paths the bot actually uses, because they fail
independently:

1. **Stream read** — resolve a stream URL and read it over plain HTTP with the
   headers yt-dlp supplies. This is what FFmpeg does during playback. It reads
   past the 20 MB mark on purpose: YouTube has cut media sessions around there
   before, and FFmpeg cannot re-issue an expired URL, so the failure shows up
   as a track stopping partway with no error the listener can see.

2. **Full download** — let yt-dlp download and post-process a whole track, the
   way AudioCacheManager does. yt-dlp *can* recover a dropped session by
   re-resolving the URL, so this passing while (1) fails is a meaningful
   result, not a contradiction.

Exit codes are distinct on purpose:
    0  everything worked
    1  yt-dlp regressed — do not deploy
    2  the canary itself is misconfigured (video removed, no network).
       Not a yt-dlp verdict; fix the canary.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import yt_dlp

# "Me at the zoo" — the oldest video on the platform, and the least likely to
# disappear. Used only to prove extraction works at all.
BASIC_VIDEO_ID = os.getenv("SMOKE_VIDEO_ID", "jNQXAC9IVRw")

# The stream-read check needs a track whose audio exceeds the ~20 MB boundary,
# which means roughly half an hour of audio. Any single video ID eventually
# rots, so this is a candidate list and the check falls back to a search when
# every candidate is gone — otherwise a deleted video looks like a yt-dlp
# regression and wedges deployments. Comma-separated; override with
# SMOKE_LARGE_VIDEO_ID.
LARGE_VIDEO_CANDIDATES = [
    part.strip()
    for part in os.getenv(
        "SMOKE_LARGE_VIDEO_ID", "HriYRoxWo1I,x2bd1zp_q6Y,YBrRJY_V1lc"
    ).split(",")
    if part.strip()
]

# Below this, the audio cannot reach the read target at typical bitrates.
MIN_LARGE_DURATION_SECONDS = 25 * 60

# Used to re-find a long video when every candidate above has gone away.
LARGE_VIDEO_SEARCH = os.getenv("SMOKE_LARGE_SEARCH", "full concert live")

# Read this far into the stream; anything past ~21 MB clears the boundary.
STREAM_READ_LIMIT = 25 * 1024 * 1024
STREAM_READ_TARGET = 21 * 1024 * 1024
STREAM_READ_DEADLINE_SECONDS = int(os.getenv("SMOKE_STREAM_DEADLINE_SECONDS", "180"))

# GitHub-hosted runner addresses are shared and can be rate-limited or blocked
# independently of the yt-dlp release under test. Those messages describe an
# unusable probe environment, not an extractor regression. Keep this list
# deliberately narrow: HTTP 403 and parser/format errors remain hard failures.
ENVIRONMENT_FAILURE_MARKERS = (
    "sign in to confirm you're not a bot",
    "sign in to confirm you’re not a bot",
    "http error 429",
    "too many requests",
    "this content isn't available, try again later",
    "temporary failure in name resolution",
    "name or service not known",
    "network is unreachable",
    "connection reset",
    "remote end closed connection",
    "timed out",
)

BASE_OPTS = {
    "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "socket_timeout": 30,
    "retries": 3,
    "cachedir": False,
}


class CanaryMisconfigured(Exception):
    """The check could not run — not evidence that yt-dlp is broken."""


def is_environment_failure(error):
    """Return whether an error points to the probe environment, not yt-dlp."""
    message = str(error).casefold()
    return any(marker.casefold() in message for marker in ENVIRONMENT_FAILURE_MARKERS)


def log(message):
    print(message, flush=True)


def watch_url(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"


def extract(video_id, extra_opts=None):
    opts = dict(BASE_OPTS)
    if extra_opts:
        opts.update(extra_opts)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(watch_url(video_id), download=False)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_extraction():
    """Metadata and a stream URL come back for a known-good video."""
    log(f"[1/4] Extracting metadata for {BASIC_VIDEO_ID}...")
    info = extract(BASIC_VIDEO_ID)

    if not info:
        raise AssertionError("extraction returned no data")
    if not info.get("url"):
        raise AssertionError("extraction returned no stream URL")

    log(f"      ok — '{info.get('title')}' ({info.get('duration')}s)")
    return info


def find_large_video():
    """Return info for a video long enough to exceed the read target.

    Tries the configured candidates in order, then falls back to a search.
    Raises CanaryMisconfigured only when nothing usable can be found at all,
    which is a canary problem rather than a yt-dlp verdict.
    """
    for video_id in LARGE_VIDEO_CANDIDATES:
        try:
            info = extract(video_id)
        except yt_dlp.utils.DownloadError as e:
            log(f"      candidate {video_id} unavailable ({str(e).strip()[:80]}); trying next")
            continue
        if not info or not info.get("url"):
            continue
        if (info.get("duration") or 0) < MIN_LARGE_DURATION_SECONDS:
            log(f"      candidate {video_id} is too short; trying next")
            continue
        return info

    log(f"      all candidates unusable; searching for '{LARGE_VIDEO_SEARCH}'")
    try:
        results = extract_search(LARGE_VIDEO_SEARCH, limit=8)
    except yt_dlp.utils.DownloadError as e:
        raise CanaryMisconfigured(f"candidates gone and search failed: {e}") from e

    for entry in results:
        if (entry.get("duration") or 0) < MIN_LARGE_DURATION_SECONDS:
            continue
        try:
            info = extract(entry["id"])
        except yt_dlp.utils.DownloadError:
            continue
        if info and info.get("url"):
            log(
                f"      using {entry['id']} from search — consider adding it to "
                f"SMOKE_LARGE_VIDEO_ID"
            )
            return info

    raise CanaryMisconfigured(
        "no video long enough could be found; set SMOKE_LARGE_VIDEO_ID"
    )


def extract_search(query, limit):
    """Return flat search results, cheapest possible extraction."""
    opts = dict(BASE_OPTS)
    opts.update({"extract_flat": True, "noplaylist": False})
    with yt_dlp.YoutubeDL(opts) as ydl:
        result = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    return [e for e in (result.get("entries") or []) if e and e.get("id")]


def check_stream_read():
    """Read past 20 MB of a stream URL, the way FFmpeg does during playback."""
    log(f"[2/4] Reading {STREAM_READ_TARGET // 1024 // 1024} MB+ from a stream URL...")
    info = find_large_video()
    stream_url = info["url"]

    # The googlevideo CDN only answers with 200 when the request carries the
    # same headers (notably User-Agent) as the client yt-dlp used.
    request = urllib.request.Request(stream_url)
    for header, value in (info.get("http_headers") or {}).items():
        request.add_header(header, value)

    read = 0
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            while read < STREAM_READ_LIMIT:
                if time.monotonic() - started >= STREAM_READ_DEADLINE_SECONDS:
                    raise CanaryMisconfigured(
                        "stream read exceeded "
                        f"{STREAM_READ_DEADLINE_SECONDS}s; runner/CDN path is too slow"
                    )
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                read += len(chunk)
    except urllib.error.HTTPError as e:
        if is_environment_failure(e):
            raise CanaryMisconfigured(f"stream probe was rate-limited: {e}") from e
        raise AssertionError(
            f"stream read failed with HTTP {e.code} after {read:,} bytes "
            f"({read / 1024 / 1024:.1f} MB)"
        ) from e
    except OSError as e:
        if is_environment_failure(e):
            raise CanaryMisconfigured(f"stream probe network failure: {e}") from e
        raise AssertionError(
            f"stream read failed after {read:,} bytes "
            f"({read / 1024 / 1024:.1f} MB): {e}"
        ) from e

    if read < STREAM_READ_TARGET:
        raise AssertionError(
            f"stream ended early at {read:,} bytes ({read / 1024 / 1024:.1f} MB); "
            f"expected to get past {STREAM_READ_TARGET / 1024 / 1024:.0f} MB. "
            f"This is the session-cap failure mode: playback would cut off here."
        )

    log(f"      ok — read {read / 1024 / 1024:.1f} MB without interruption")


def check_ffmpeg_decode():
    """FFmpeg can decode the stream using the headers the bot passes it."""
    log("[3/4] Decoding a stream through FFmpeg...")
    if shutil.which("ffmpeg") is None:
        raise CanaryMisconfigured("ffmpeg is not installed")

    info = extract(BASIC_VIDEO_ID)
    stream_url = info.get("url")
    headers = info.get("http_headers") or {}

    command = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    user_agent = headers.get("User-Agent")
    if user_agent:
        command += ["-user_agent", user_agent]
    others = "".join(
        f"{k}: {v}\r\n" for k, v in headers.items() if k.lower() != "user-agent" and v
    )
    if others:
        command += ["-headers", others]
    command += ["-i", stream_url, "-t", "3", "-f", "null", "-"]

    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise AssertionError(
            f"ffmpeg failed to decode the stream: {result.stderr.strip()[:500]}"
        )

    log("      ok — ffmpeg decoded 3s of audio")


def check_full_download():
    """Download and transcode a whole track, the way the audio cache does."""
    log("[4/4] Downloading a full track through yt-dlp...")
    if shutil.which("ffmpeg") is None:
        raise CanaryMisconfigured("ffmpeg is not installed")

    with tempfile.TemporaryDirectory() as workdir:
        target = Path(workdir) / "smoke.%(ext)s"
        opts = dict(BASE_OPTS)
        opts.update({
            "format": "bestaudio/best",
            "outtmpl": str(target),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([watch_url(BASIC_VIDEO_ID)])

        produced = list(Path(workdir).glob("smoke.*"))
        if not produced:
            raise AssertionError("download produced no file")
        size = produced[0].stat().st_size
        if size == 0:
            raise AssertionError("download produced an empty file")

    log(f"      ok — produced {size:,} bytes")


CHECKS = (
    ("extraction", check_extraction),
    ("stream_read", check_stream_read),
    ("ffmpeg_decode", check_ffmpeg_decode),
    ("full_download", check_full_download),
)


def main():
    version = yt_dlp.version.__version__
    log(f"yt-dlp {version}")
    log("")

    failures = {}
    misconfigured = {}

    for name, check in CHECKS:
        try:
            check()
        except CanaryMisconfigured as e:
            log(f"      SKIPPED — {e}")
            misconfigured[name] = str(e)
        except yt_dlp.utils.DownloadError as e:
            if is_environment_failure(e):
                log(f"      SKIPPED — probe environment rejected the request: {e}")
                misconfigured[name] = str(e)
            else:
                log(f"      FAILED — {e}")
                failures[name] = str(e)
        except AssertionError as e:
            log(f"      FAILED — {e}")
            failures[name] = str(e)
        except Exception as e:  # unexpected: still a yt-dlp verdict
            log(f"      FAILED — unexpected {type(e).__name__}: {e}")
            failures[name] = f"{type(e).__name__}: {e}"

    log("")
    summary = {
        "ytdlp_version": version,
        "failed": sorted(failures),
        "skipped": sorted(misconfigured),
        "details": {**failures, **misconfigured},
    }

    # Consumed by the workflow to build its alert message.
    summary_path = os.getenv("SMOKE_SUMMARY_PATH")
    if summary_path:
        Path(summary_path).write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if failures:
        log(f"RESULT: yt-dlp {version} FAILED ({', '.join(sorted(failures))})")
        return 1
    if misconfigured:
        log(f"RESULT: inconclusive — canary needs attention ({', '.join(sorted(misconfigured))})")
        return 2
    log(f"RESULT: yt-dlp {version} passed all checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
