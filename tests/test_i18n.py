"""Tests for the message catalog.

Translation drift is silent: `t()` falls back to English for a missing key and
to the key name itself for an unknown one, so a typo ships as a user-visible
"notify_track_failed" instead of a crash. These tests turn that into a build
failure.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.i18n import DEFAULT_LANGUAGE, MESSAGES, SUPPORTED_LANGUAGES, t

PLACEHOLDER = re.compile(r"\{(\w+)\}")


def test_every_supported_language_has_a_catalog():
    for language in SUPPORTED_LANGUAGES:
        assert language in MESSAGES, f"no catalog for {language!r}"


@pytest.mark.parametrize(
    "language", [lang for lang in SUPPORTED_LANGUAGES if lang != DEFAULT_LANGUAGE]
)
def test_translations_cover_every_english_key(language):
    missing = sorted(set(MESSAGES[DEFAULT_LANGUAGE]) - set(MESSAGES[language]))
    assert not missing, f"{language!r} is missing keys: {missing}"


@pytest.mark.parametrize(
    "language", [lang for lang in SUPPORTED_LANGUAGES if lang != DEFAULT_LANGUAGE]
)
def test_translations_have_no_orphan_keys(language):
    """A key only in a translation is dead weight, or a typo of a real key."""
    orphans = sorted(set(MESSAGES[language]) - set(MESSAGES[DEFAULT_LANGUAGE]))
    assert not orphans, f"{language!r} has keys English does not: {orphans}"


@pytest.mark.parametrize(
    "language", [lang for lang in SUPPORTED_LANGUAGES if lang != DEFAULT_LANGUAGE]
)
def test_placeholders_match_across_languages(language):
    """A translation that drops or renames a {placeholder} raises KeyError at
    format time — in production, inside an error handler, where it is worst."""
    mismatches = {}
    for key, english in MESSAGES[DEFAULT_LANGUAGE].items():
        translated = MESSAGES[language].get(key)
        if translated is None:
            continue
        expected = set(PLACEHOLDER.findall(english))
        actual = set(PLACEHOLDER.findall(translated))
        if expected != actual:
            mismatches[key] = {"expected": sorted(expected), "found": sorted(actual)}

    assert not mismatches, f"{language!r} placeholder mismatch: {mismatches}"


def test_lookup_falls_back_to_english_for_unknown_language(monkeypatch):
    monkeypatch.setenv("BOT_LANGUAGE", "does-not-exist")
    assert t("err_unknown_error") == MESSAGES["en"]["err_unknown_error"]


def test_unknown_key_returns_the_key_instead_of_raising(monkeypatch):
    monkeypatch.setenv("BOT_LANGUAGE", "en")
    assert t("no_such_key_anywhere") == "no_such_key_anywhere"


def test_notifier_keys_format_with_their_arguments(monkeypatch):
    """The notification keys added for error reporting take arguments; make
    sure each accepts the ones the call sites actually pass."""
    monkeypatch.setenv("BOT_LANGUAGE", "en")
    cases = {
        "notify_suppressed_suffix": {"count": 3},
        "notify_track_failed": {"title": "Song"},
        "notify_stream_exhausted": {"title": "Song", "attempts": 3},
        "notify_extract_failed": {"query": "Song"},
        "notify_queue_stalled": {"count": 5},
        "notify_ops_cache_body": {"video_id": "abc123"},
        "notify_ops_playback_body": {"guild_id": 1, "title": "Song"},
    }
    for language in SUPPORTED_LANGUAGES:
        monkeypatch.setenv("BOT_LANGUAGE", language)
        for key, kwargs in cases.items():
            rendered = t(key, **kwargs)
            assert rendered != key, f"{key!r} missing from {language!r} catalog"
            assert "{" not in rendered, f"{key!r} left a placeholder unfilled in {language!r}"
