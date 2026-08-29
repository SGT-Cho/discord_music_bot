"""Unit tests for yt-dlp canary verdict classification."""

import pytest

from tools.ytdlp_smoke import is_environment_failure


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
