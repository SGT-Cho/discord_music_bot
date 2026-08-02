"""
YoutubeDL Connection Manager
============================

YoutubeDL instance manager to fix TCP port exhaustion.
Uses the context manager pattern to automate instance creation and cleanup.

Problem:
- YoutubeDL instances were left around without close() after creation
- ~28,000 zombie TCP connections accumulated over 8 weeks
- Connection failures due to port exhaustion

Solution:
- Manage instance lifetime with a context manager
- Automatic cleanup and statistics tracking
- Resource leak monitoring
"""

import yt_dlp as youtube_dl
import threading
import logging
import gc
from contextlib import contextmanager
from typing import Dict, Any, Optional, Generator

logger = logging.getLogger(__name__)


class YTDLManager:
    """
    YoutubeDL instance manager (Singleton pattern)

    Usage:
        manager = YTDLManager.get_instance()
        with manager.get_ytdl(options) as ytdl:
            data = ytdl.extract_info(url, download=False)
        # cleaned up automatically
    """

    _instance: Optional['YTDLManager'] = None
    _lock = threading.Lock()

    def __init__(self):
        """Do not call directly. Use get_instance() instead."""
        self._active_count = 0
        self._total_created = 0
        self._total_closed = 0
        self._stats_lock = threading.Lock()
        logger.info("YTDLManager initialized")

    @classmethod
    def get_instance(cls) -> 'YTDLManager':
        """Return the singleton instance"""
        if cls._instance is None:
            with cls._lock:
                # Double-checked locking
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @contextmanager
    def get_ytdl(self, options: Dict[str, Any]) -> Generator[youtube_dl.YoutubeDL, None, None]:
        """
        Manage a YoutubeDL instance via context manager

        Args:
            options: yt-dlp options dictionary

        Yields:
            YoutubeDL instance

        Example:
            with manager.get_ytdl({'quiet': True}) as ytdl:
                data = ytdl.extract_info(url, download=False)
        """
        ytdl: Optional[youtube_dl.YoutubeDL] = None

        try:
            # Create instance
            ytdl = youtube_dl.YoutubeDL(options)

            with self._stats_lock:
                self._active_count += 1
                self._total_created += 1

            logger.debug(f"YoutubeDL instance created (active: {self._active_count})")
            yield ytdl

        finally:
            if ytdl is not None:
                try:
                    self._cleanup_ytdl_instance(ytdl)
                except Exception as e:
                    logger.warning(f"YoutubeDL cleanup warning: {e}")
                finally:
                    with self._stats_lock:
                        self._active_count -= 1
                        self._total_closed += 1
                    logger.debug(f"YoutubeDL instance closed (active: {self._active_count})")

    def _cleanup_ytdl_instance(self, ytdl: youtube_dl.YoutubeDL) -> None:
        """
        Clean up a YoutubeDL instance's internal resources

        Calls yt-dlp's close() method to release HTTP connections and other resources
        """
        try:
            if hasattr(ytdl, 'close') and callable(ytdl.close):
                ytdl.close()
                logger.debug("YoutubeDL.close() called successfully")

            # Extra cleanup: internal cache
            if hasattr(ytdl, '_download_retcode'):
                ytdl._download_retcode = None

            # Extra HTTP handler cleanup (in case close() missed it)
            if hasattr(ytdl, '_opener') and ytdl._opener is not None:
                try:
                    ytdl._opener.close()
                except Exception:
                    pass
                ytdl._opener = None

            # Remove urlopen reference
            if hasattr(ytdl, 'urlopen'):
                ytdl.urlopen = None

            # Remove params reference (saves memory)
            if hasattr(ytdl, 'params'):
                ytdl.params = None

        except Exception as e:
            logger.debug(f"Minor cleanup issue (safe to ignore): {e}")

    def get_stats(self) -> Dict[str, int]:
        """
        Return connection statistics

        Returns:
            dict with keys:
                - active: current number of active instances
                - total_created: total instances created
                - total_closed: total instances cleaned up
                - leaked: number of leaked instances (negative is normal)
        """
        with self._stats_lock:
            leaked = self._total_created - self._total_closed - self._active_count
            return {
                'active': self._active_count,
                'total_created': self._total_created,
                'total_closed': self._total_closed,
                'leaked': leaked
            }

    def force_gc(self) -> None:
        """Force a garbage collection run"""
        collected = gc.collect()
        logger.info(f"Garbage collection completed: {collected} objects collected")

    def reset_stats(self) -> None:
        """Reset statistics (for testing)"""
        with self._stats_lock:
            self._total_created = 0
            self._total_closed = 0
            # Do not reset active_count (tracks actual active instances)
        logger.info("YTDLManager stats reset")

    def log_stats(self) -> None:
        """Log current statistics"""
        stats = self.get_stats()
        if stats['leaked'] > 0:
            logger.warning(f"YTDLManager Stats (LEAK DETECTED): {stats}")
        else:
            logger.info(f"YTDLManager Stats: {stats}")


# Convenience functions
def get_ytdl_manager() -> YTDLManager:
    """Return the YTDLManager singleton instance"""
    return YTDLManager.get_instance()


@contextmanager
def managed_ytdl(options: Dict[str, Any]) -> Generator[youtube_dl.YoutubeDL, None, None]:
    """
    Convenience context manager backed by YTDLManager

    Example:
        from src.utils.ytdl_manager import managed_ytdl

        with managed_ytdl({'quiet': True}) as ytdl:
            data = ytdl.extract_info(url, download=False)
    """
    manager = YTDLManager.get_instance()
    with manager.get_ytdl(options) as ytdl:
        yield ytdl
