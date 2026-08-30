"""Regression tests for audio cache filesystem behavior."""

from pathlib import Path

import pytest

from src.cache.audio_cache import AudioCacheManager


class _FakeYDL:
    def __init__(self, _options, action):
        self.action = action

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def download(self, _urls):
        self.action()


def _deny_directory_listing(monkeypatch, cache_dir):
    original_iterdir = Path.iterdir

    def guarded_iterdir(path):
        if path == cache_dir:
            raise PermissionError(1, "Operation not permitted", str(path))
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)


@pytest.mark.asyncio
async def test_download_succeeds_when_named_io_works_but_listing_is_denied(
    tmp_path, monkeypatch
):
    manager = AudioCacheManager(str(tmp_path))
    video_id = "video123"
    temp_file = manager._tmp_path(video_id)

    monkeypatch.setattr(
        "src.cache.audio_cache.yt_dlp.YoutubeDL",
        lambda options: _FakeYDL(options, lambda: temp_file.write_bytes(b"audio")),
    )
    _deny_directory_listing(monkeypatch, manager.cache_dir)

    assert await manager.download_and_cache(video_id, "https://example.invalid/watch")
    assert manager._mp3_path(video_id).read_bytes() == b"audio"
    assert not temp_file.exists()


@pytest.mark.asyncio
async def test_download_failure_cleanup_does_not_require_directory_listing(
    tmp_path, monkeypatch
):
    manager = AudioCacheManager(str(tmp_path))

    def fail_download():
        raise RuntimeError("download failed")

    monkeypatch.setattr(
        "src.cache.audio_cache.yt_dlp.YoutubeDL",
        lambda options: _FakeYDL(options, fail_download),
    )
    _deny_directory_listing(monkeypatch, manager.cache_dir)

    assert not await manager.download_and_cache(
        "video123", "https://example.invalid/watch"
    )
