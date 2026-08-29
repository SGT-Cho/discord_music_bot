#!/usr/bin/env python3
"""Canary check: does the current yt-dlp still work against real YouTube?

Run by the ytdlp-canary workflow before a new yt-dlp release is allowed into a
deployed image, and useful by hand when playback breaks and you need to know
whether the cause is upstream.

It exercises the paths the bot actually uses, because they fail independently:

1. **Stream read** — resolve a stream URL and read a small amount over plain
   HTTP with the headers yt-dlp supplies. This catches URL/header failures
   without making a healthy, throttled CDN path hold the release gate open for
   more than ten minutes.

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
import signal
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

# The optional long-session check needs a track whose audio exceeds the ~20 MB boundary,
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

STREAM_READ_TARGET = int(os.getenv("SMOKE_STREAM_READ_TARGET_BYTES", str(128 * 1024)))
STREAM_READ_DEADLINE_SECONDS = int(os.getenv("SMOKE_STREAM_DEADLINE_SECONDS", "120"))

# A historical YouTube failure cut long media sessions around 20 MB. Reading
# beyond that boundary remains useful as a diagnostic, but the deployment WAN
# currently receives media at roughly 31 KiB/s, so it cannot be a fast release
# gate. Opt in with SMOKE_LONG_SESSION_CHECK=1 (the launchd gate leaves it off).
LONG_SESSION_CHECK = os.getenv("SMOKE_LONG_SESSION_CHECK", "").casefold() in {
    "1",
    "true",
    "yes",
}
LONG_SESSION_READ_TARGET = 21 * 1024 * 1024
LONG_SESSION_DEADLINE_SECONDS = int(
    os.getenv("SMOKE_LONG_SESSION_DEADLINE_SECONDS", "1200")
)
TOTAL_DEADLINE_SECONDS = int(
    os.getenv("SMOKE_TOTAL_DEADLINE_SECONDS", "1500" if LONG_SESSION_CHECK else "600")
)

# GitHub-hosted runner addresses are shared and can be rate-limited or blocked
# independently of the yt-dlp release under test. Those messages describe an
# unusable probe environment, not an extractor regression. Keep this list
# deliberately narrow: HTTP 403 and parser/format errors remain hard failures.
ENVIRONMENT_FAILURE_MARKERS = (
    "sign in to confirm you're not a bot",
    "sign in to confirm you’re not a bot",
    "http error 429",
    "http error 5",
    "too many requests",
    "this content isn't available, try again later",
    "temporary failure in name resolution",
    "name or service not known",
    "network is unreachable",
    "connection reset",
    "remote end closed connection",
    "timed out",
    "certificate verify failed",
    "tls handshake",
)

# A stale candidate should not wedge the canary, but only errors that clearly
# describe that one video may fall through to the next candidate. Parser,
# format, and extractor errors remain hard failures.
CANDIDATE_UNAVAILABLE_MARKERS = (
    "video unavailable",
    "this video is unavailable",
    "private video",
    "has been removed",
    "is no longer available",
    "members-only content",
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


class CanaryDeadlineExceeded(CanaryMisconfigured):
    """The whole probe exceeded its bounded execution time."""


def is_environment_failure(error):
    """Return whether an error points to the probe environment, not yt-dlp."""
    message = str(error).casefold()
    return any(marker.casefold() in message for marker in ENVIRONMENT_FAILURE_MARKERS)


def is_candidate_unavailable(error):
    """Return whether an error only invalidates one configured test video."""
    message = str(error).casefold()
    return any(marker.casefold() in message for marker in CANDIDATE_UNAVAILABLE_MARKERS)


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
            if is_environment_failure(e):
                raise CanaryMisconfigured(
                    f"probe environment rejected candidate {video_id}: {e}"
                ) from e
            if is_candidate_unavailable(e):
                log(
                    f"      candidate {video_id} unavailable "
                    f"({str(e).strip()[:80]}); trying next"
                )
                continue
            raise
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
        if is_environment_failure(e):
            raise CanaryMisconfigured(
                f"candidates gone and the probe environment rejected search: {e}"
            ) from e
        raise

    for entry in results:
        if (entry.get("duration") or 0) < MIN_LARGE_DURATION_SECONDS:
            continue
        try:
            info = extract(entry["id"])
        except yt_dlp.utils.DownloadError as e:
            if is_environment_failure(e):
                raise CanaryMisconfigured(
                    f"probe environment rejected search candidate {entry['id']}: {e}"
                ) from e
            if is_candidate_unavailable(e):
                continue
            raise
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


def read_stream(info, target_bytes, deadline_seconds):
    """Read *target_bytes* from an extracted media URL with yt-dlp headers."""
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
            # HTTPResponse.read(n) may wait to fill all n bytes while a CDN
            # trickles data, preventing the wall-clock deadline from being
            # checked. read1() performs at most one buffered/socket read.
            read_chunk = getattr(response, "read1", None) or response.read
            while read < target_bytes:
                if time.monotonic() - started >= deadline_seconds:
                    raise CanaryMisconfigured(
                        "stream read exceeded "
                        f"{deadline_seconds}s; runner/CDN path is too slow"
                    )
                chunk = read_chunk(min(256 * 1024, target_bytes - read))
                if not chunk:
                    break
                read += len(chunk)
    except urllib.error.HTTPError as e:
        if e.code == 429 or 500 <= e.code <= 599 or is_environment_failure(e):
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

    if read < target_bytes:
        raise AssertionError(
            f"stream ended early at {read:,} bytes ({read / 1024 / 1024:.1f} MB); "
            f"expected at least {target_bytes:,} bytes"
        )

    return read


def check_stream_read():
    """Read a small amount from a stream URL, the way FFmpeg starts playback."""
    log(f"[2/4] Reading {STREAM_READ_TARGET // 1024} KiB from a stream URL...")
    info = extract(BASIC_VIDEO_ID)
    read = read_stream(info, STREAM_READ_TARGET, STREAM_READ_DEADLINE_SECONDS)
    log(f"      ok — read {read / 1024:.0f} KiB without interruption")


def check_long_session_read():
    """Opt-in monitor for media sessions surviving the historical 20 MB cap."""
    log(
        "[long-session] Reading "
        f"{LONG_SESSION_READ_TARGET // 1024 // 1024} MB from a stream URL..."
    )
    info = find_large_video()
    read = read_stream(
        info,
        LONG_SESSION_READ_TARGET,
        LONG_SESSION_DEADLINE_SECONDS,
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

    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as error:
        raise CanaryMisconfigured("ffmpeg decode exceeded 120s") from error
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

        produced = list(Path(workdir).glob("smoke.mp3"))
        if not produced:
            raise AssertionError("download did not produce the expected MP3")
        size = produced[0].stat().st_size
        if size < 16 * 1024:
            raise AssertionError(f"download produced an implausibly small MP3 ({size} bytes)")

        try:
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-show_entries",
                    "stream=codec_type:format=duration",
                    "-of",
                    "json",
                    str(produced[0]),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as error:
            raise CanaryMisconfigured("ffprobe validation exceeded 30s") from error
        if probe.returncode != 0:
            raise AssertionError(f"ffprobe rejected the MP3: {probe.stderr.strip()[:300]}")
        media = json.loads(probe.stdout)
        streams = media.get("streams") or []
        duration = float((media.get("format") or {}).get("duration") or 0)
        if not any(stream.get("codec_type") == "audio" for stream in streams):
            raise AssertionError("downloaded MP3 contains no audio stream")
        if duration <= 1:
            raise AssertionError(f"downloaded MP3 duration is invalid ({duration}s)")

    log(f"      ok — produced {size:,} bytes")


CHECKS = (
    ("extraction", check_extraction),
    ("stream_read", check_stream_read),
    ("ffmpeg_decode", check_ffmpeg_decode),
    ("full_download", check_full_download),
)

if LONG_SESSION_CHECK:
    CHECKS += (("long_session_read", check_long_session_read),)


def _deadline_handler(_signum, _frame):
    raise CanaryDeadlineExceeded(
        f"whole canary exceeded {TOTAL_DEADLINE_SECONDS}s"
    )


def main():
    version = yt_dlp.version.__version__
    log(f"yt-dlp {version}")
    log("")

    failures = {}
    misconfigured = {}

    deadline_supported = hasattr(signal, "SIGALRM") and TOTAL_DEADLINE_SECONDS > 0
    if deadline_supported:
        signal.signal(signal.SIGALRM, _deadline_handler)
        signal.alarm(TOTAL_DEADLINE_SECONDS)

    try:
        for index, (name, check) in enumerate(CHECKS):
            try:
                check()
            except CanaryDeadlineExceeded as e:
                log(f"      SKIPPED — {e}")
                misconfigured[name] = str(e)
                for remaining_name, _ in CHECKS[index + 1:]:
                    misconfigured[remaining_name] = (
                        "not run after whole-canary deadline"
                    )
                break
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
            except Exception as e:
                # An unknown canary/software/environment error must block a
                # release, but it is not evidence that yt-dlp regressed.
                log(f"      SKIPPED — unexpected {type(e).__name__}: {e}")
                misconfigured[name] = f"{type(e).__name__}: {e}"
    finally:
        if deadline_supported:
            signal.alarm(0)

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
