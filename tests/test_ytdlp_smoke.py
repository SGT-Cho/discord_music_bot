"""Unit tests for yt-dlp canary verdict classification."""

import pytest
import yt_dlp

import tools.ytdlp_smoke as smoke
from tools.ytdlp_smoke import is_candidate_unavailable, is_environment_failure


@pytest.mark.parametrize(
    "message",
    [
        "Sign in to confirm you're not a bot",
        "HTTP Error 429: Too Many Requests",
        "Temporary failure in name resolution",
        "The read operation timed out",
        "Remote end closed connection without response",
    ],
)
def test_runner_and_network_failures_are_inconclusive(message):
    assert is_environment_failure(message)


@pytest.mark.parametrize(
    "message",
    [
        "HTTP Error 403: Forbidden",
        "No video formats found",
        "Unable to extract player response",
    ],
)
def test_extractor_regressions_remain_hard_failures(message):
    assert not is_environment_failure(message)


@pytest.mark.parametrize(
    "message",
    [
        "ERROR: [youtube] abc: Video unavailable",
        "ERROR: [youtube] abc: Private video",
        "This video has been removed by the uploader",
    ],
)
def test_stale_candidate_errors_can_fall_through(message):
    assert is_candidate_unavailable(message)


def test_large_video_hard_failure_is_not_downgraded(monkeypatch):
    monkeypatch.setattr(smoke, "LARGE_VIDEO_CANDIDATES", ["brokenvideo"])

    def fail_extract(_video_id, _extra_opts=None):
        raise yt_dlp.utils.DownloadError("Unable to extract player response")

    monkeypatch.setattr(smoke, "extract", fail_extract)

    with pytest.raises(yt_dlp.utils.DownloadError):
        smoke.find_large_video()


def test_large_video_runner_block_is_inconclusive(monkeypatch):
    monkeypatch.setattr(smoke, "LARGE_VIDEO_CANDIDATES", ["blockedvid"])

    def fail_extract(_video_id, _extra_opts=None):
        raise yt_dlp.utils.DownloadError("Sign in to confirm you're not a bot")

    monkeypatch.setattr(smoke, "extract", fail_extract)

    with pytest.raises(smoke.CanaryMisconfigured):
        smoke.find_large_video()


def test_whole_canary_deadline_is_inconclusive():
    with pytest.raises(smoke.CanaryDeadlineExceeded):
        smoke._deadline_handler(None, None)
