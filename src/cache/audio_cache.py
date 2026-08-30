import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yt_dlp

from ..utils.redaction import redact_input
from ..i18n import t

logger = logging.getLogger(__name__)


class AudioCacheManager:
    """Audio file cache manager - downloads YouTube audio as MP3 into a local cache"""

    def __init__(self, cache_dir: Optional[str] = None):
        configured_cache_dir = cache_dir or os.getenv("AUDIO_CACHE_DIR")
        default_cache_dir = Path(__file__).resolve().parents[2] / "cache" / "audio"
        self.cache_dir = Path(configured_cache_dir or default_cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._download_locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        self._downloading: set[str] = set()
        # Set by the Music cog once the bot is connected. Cache downloads run
        # in the background, so without this their failures are invisible.
        self.notifier = None
        logger.info(f"[Cache] AudioCacheManager initialized: {self.cache_dir}")

    def attach_notifier(self, notifier) -> None:
        """Route cache failures to the operational notification channel."""
        self.notifier = notifier

    async def _report_failure(self, video_id: str, error: Optional[BaseException] = None):
        """Send a cache failure to the ops channel, if one is configured."""
        if self.notifier is None:
            return
        try:
            body = t("notify_ops_cache_body", video_id=video_id)
            if error is not None:
                await self.notifier.notify_ops_exception(
                    kind="cache_failed",
                    title=t("notify_ops_cache_title"),
                    context=body,
                    error=error,
                )
            else:
                await self.notifier.notify_ops(
                    kind="cache_failed",
                    title=t("notify_ops_cache_title"),
                    message=body,
                )
        except Exception as e:  # notification must never break the caller
            logger.warning(f"[Cache] Failed to report cache failure: {e}")

    def _mp3_path(self, video_id: str) -> Path:
        return self.cache_dir / f"{video_id}.mp3"

    def _json_path(self, video_id: str) -> Path:
        return self.cache_dir / f"{video_id}.json"

    def _tmp_path(self, video_id: str) -> Path:
        return self.cache_dir / f"{video_id}.tmp.mp3"

    def is_cached(self, video_id: str) -> bool:
        """Check that the cached MP3 file exists and is larger than 0 bytes"""
        path = self._mp3_path(video_id)
        return path.exists() and path.stat().st_size > 0

    def get(self, video_id: str) -> Optional[str]:
        """Return the cached file path, or None if not cached"""
        if self.is_cached(video_id):
            return str(self._mp3_path(video_id))
        return None

    async def _get_lock(self, video_id: str) -> asyncio.Lock:
        """Return the per-video_id lock (created lazily)"""
        async with self._locks_guard:
            if video_id not in self._download_locks:
                self._download_locks[video_id] = asyncio.Lock()
            return self._download_locks[video_id]

    async def schedule_cache(self, video_id: str, webpage_url: str, metadata: Optional[dict] = None):
        """Create a fire-and-forget download task in the background"""
        if self.is_cached(video_id) or video_id in self._downloading:
            return
        asyncio.create_task(self._safe_download(video_id, webpage_url, metadata))

    async def _safe_download(self, video_id: str, webpage_url: str, metadata: Optional[dict] = None):
        """On download failure, just log and exit quietly"""
        try:
            await self.download_and_cache(video_id, webpage_url, metadata)
        except Exception as e:
            logger.error(f"[Cache] Background download failed for {video_id}: {redact_input(e)}")

    async def download_and_cache(self, video_id: str, webpage_url: str, metadata: Optional[dict] = None) -> bool:
        """Download YouTube audio as MP3 and cache it

        Args:
            video_id: YouTube video ID
            webpage_url: YouTube video URL
            metadata: metadata to store (title, uploader, etc.)

        Returns:
            Whether the download succeeded
        """
        if self.is_cached(video_id):
            logger.debug(f"[Cache] Already cached: {video_id}")
            return True

        lock = await self._get_lock(video_id)
        async with lock:
            # Re-check after acquiring the lock (another task may have finished already)
            if self.is_cached(video_id):
                return True

            self._downloading.add(video_id)
            tmp_path = self._tmp_path(video_id)
            final_path = self._mp3_path(video_id)

            try:
                logger.info(f"[Cache] Downloading: {video_id} ({redact_input(webpage_url)})")

                ydl_opts = {
                    'format': 'bestaudio/best',
                    'outtmpl': str(tmp_path).replace('.mp3', '.%(ext)s'),
                    'restrictfilenames': True,
                    'noplaylist': True,
                    'nocheckcertificate': False,
                    'quiet': True,
                    'no_warnings': True,
                    'source_address': '0.0.0.0',
                    'socket_timeout': 30,
                    'retries': 3,
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }],
                }

                loop = asyncio.get_running_loop()

                def _download():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([webpage_url])

                await loop.run_in_executor(None, _download)

                # The output template and postprocessor make this filename
                # deterministic. Address it directly instead of scanning the
                # directory: macOS privacy controls can allow named-file I/O
                # through a container bind mount while denying listdir(2).
                if not tmp_path.is_file() or tmp_path.stat().st_size <= 0:
                    logger.error(f"[Cache] Download completed but MP3 file not found for {video_id}")
                    await self._report_failure(video_id)
                    return False

                # Atomic rename
                tmp_path.replace(final_path)
                logger.info(f"[Cache] Cached successfully: {video_id} ({final_path.stat().st_size / 1024 / 1024:.1f}MB)")

                # Save metadata JSON sidecar
                self._save_metadata(video_id, metadata)
                return True

            except Exception as e:
                logger.error(f"[Cache] Download error for {video_id}: {redact_input(e)}")
                await self._report_failure(video_id, e)
                # Do not scan the directory during cleanup for the same
                # reason as above. yt-dlp's final postprocessed path is known.
                try:
                    tmp_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as cleanup_error:
                    logger.warning(
                        f"[Cache] Failed to clean temp file for {video_id}: "
                        f"{redact_input(cleanup_error)}"
                    )
                return False
            finally:
                self._downloading.discard(video_id)
                # Clean up the lock once it is no longer in use
                async with self._locks_guard:
                    if video_id in self._download_locks and not self._download_locks[video_id].locked():
                        del self._download_locks[video_id]

    def _save_metadata(self, video_id: str, metadata: Optional[dict]):
        """Save the metadata JSON sidecar file"""
        if not metadata:
            return
        json_path = self._json_path(video_id)
        try:
            sidecar = {
                'title': metadata.get('title', ''),
                'uploader': metadata.get('uploader', metadata.get('channel', '')),
                'duration': metadata.get('duration', 0),
                'thumbnail': metadata.get('thumbnail', ''),
                'webpage_url': metadata.get('webpage_url', ''),
                'cached_at': datetime.now(timezone.utc).isoformat(),
            }
            json_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as e:
            logger.warning(f"[Cache] Failed to save metadata for {video_id}: {e}")

    def get_cache_stats(self) -> dict:
        """Return cache statistics"""
        mp3_files = list(self.cache_dir.glob("*.mp3"))
        total_size = sum(f.stat().st_size for f in mp3_files)
        return {
            'total_files': len(mp3_files),
            'total_size_mb': round(total_size / 1024 / 1024, 1),
            'currently_downloading': len(self._downloading),
            'downloading_ids': list(self._downloading),
            'cache_dir': str(self.cache_dir),
        }
