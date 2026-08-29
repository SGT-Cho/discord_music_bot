"""Tests for deployment authorization tag metadata."""

import pytest

from tools.ytdlp_release import (
    ReleaseMetadataError,
    build_release_tag,
    validate_version,
    version_sort_key,
    verify_release_tag,
)


COMMIT = "736ba607bb445cfa5d928d0f7562d35dedda3f01"


@pytest.mark.parametrize("version", ["2026.8.27", "2026.8.27.231323.dev0"])
def test_accepts_normalized_distribution_versions(version):
    assert validate_version(version) == version


@pytest.mark.parametrize(
    "version",
    [
        "2026.08.27.231323",
        "2026.08.27",
        "2026.8.27.246060.dev0",
        "2026.2.30",
        "nightly",
        "2026.8.27.dev0",
    ],
)
def test_rejects_non_normalized_or_invalid_versions(version):
    with pytest.raises(ReleaseMetadataError):
        validate_version(version)


def test_round_trip_release_tag():
    tag = build_release_tag("2026.8.27.231323.dev0", COMMIT)
    assert tag == f"deploy-ytdlp-v2026.8.27.231323.dev0--{COMMIT}"
    assert verify_release_tag(tag, COMMIT) == "2026.8.27.231323.dev0"


def test_version_sort_key_uses_numeric_date_order():
    assert version_sort_key("2026.10.1") > version_sort_key("2026.9.30.235959.dev0")


def test_tag_must_match_the_full_checkout_commit():
    tag = build_release_tag("2026.8.27", COMMIT)
    with pytest.raises(ReleaseMetadataError, match="does not match"):
        verify_release_tag(tag, "a" * 40)


@pytest.mark.parametrize("commit", ["736ba60", "A" * 40, "g" * 40])
def test_tag_rejects_abbreviated_or_non_lowercase_commits(commit):
    with pytest.raises(ReleaseMetadataError):
        build_release_tag("2026.8.27", commit)
