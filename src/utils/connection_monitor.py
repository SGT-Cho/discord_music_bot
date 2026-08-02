"""
TCP Connection Monitor
======================

Monitors system TCP connection state for early detection of port exhaustion.

Features:
- Periodic TCP connection count checks (default 5 minutes)
- Warning logs when thresholds are exceeded
- Notification when critical level is reached (optional)

Usage:
    monitor = ConnectionMonitor()
    asyncio.create_task(monitor.start_monitoring())
    # ...
    monitor.stop_monitoring()
"""

import asyncio
import logging
from typing import Optional, Callable, Awaitable, Dict, Any

logger = logging.getLogger(__name__)

# psutil is an optional dependency
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logger.warning("psutil not installed. Connection monitoring will be limited. Install with: pip install psutil")


class ConnectionMonitor:
    """
    TCP connection state monitoring class

    Thresholds:
        - warning_threshold: warning level (default 5,000)
        - critical_threshold: critical level (default 10,000)
        - emergency_threshold: emergency level (default 15,000)

    Example:
        monitor = ConnectionMonitor(warning_threshold=3000, critical_threshold=8000)
        asyncio.create_task(monitor.start_monitoring(interval=300))
    """

    def __init__(
        self,
        warning_threshold: int = 5000,
        critical_threshold: int = 10000,
        emergency_threshold: int = 15000,
        on_critical: Optional[Callable[[], Awaitable[None]]] = None
    ):
        """
        Args:
            warning_threshold: connection count at which warning logging starts
            critical_threshold: connection count at which critical warnings start
            emergency_threshold: connection count requiring emergency action
            on_critical: async callback invoked when critical level is reached
        """
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        self.emergency_threshold = emergency_threshold
        self.on_critical = on_critical

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_count = 0
        self._check_count = 0

    async def start_monitoring(self, interval: int = 300) -> None:
        """
        Start monitoring

        Args:
            interval: check interval in seconds, default 300 (5 minutes)
        """
        if not PSUTIL_AVAILABLE:
            logger.warning("psutil not available. Connection monitoring disabled.")
            return

        if self._running:
            logger.warning("Connection monitoring already running")
            return

        self._running = True
        logger.info(f"Connection monitoring started (interval: {interval}s, "
                   f"thresholds: warn={self.warning_threshold}, "
                   f"critical={self.critical_threshold}, "
                   f"emergency={self.emergency_threshold})")

        while self._running:
            try:
                await self._check_connections()
            except Exception as e:
                logger.error(f"Connection monitoring error: {e}")

            await asyncio.sleep(interval)

    def stop_monitoring(self) -> None:
        """Stop monitoring"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("Connection monitoring stopped")

    async def _check_connections(self) -> None:
        """Check TCP connection state"""
        if not PSUTIL_AVAILABLE:
            return

        try:
            connections = psutil.net_connections(kind='tcp')
            count = len(connections)
            self._last_count = count
            self._check_count += 1

            # Categorize connections by state
            states = self._categorize_connections(connections)

            if count >= self.emergency_threshold:
                logger.critical(
                    f"TCP EMERGENCY: {count} connections "
                    f"(limit: {self.emergency_threshold}) - IMMEDIATE ACTION REQUIRED\n"
                    f"States: {states}"
                )
                if self.on_critical:
                    try:
                        await self.on_critical()
                    except Exception as e:
                        logger.error(f"Critical callback failed: {e}")

            elif count >= self.critical_threshold:
                logger.critical(
                    f"TCP CRITICAL: {count} connections "
                    f"(limit: {self.critical_threshold})\n"
                    f"States: {states}"
                )
                if self.on_critical:
                    try:
                        await self.on_critical()
                    except Exception as e:
                        logger.error(f"Critical callback failed: {e}")

            elif count >= self.warning_threshold:
                logger.warning(
                    f"TCP WARNING: {count} connections "
                    f"(threshold: {self.warning_threshold})\n"
                    f"States: {states}"
                )

            else:
                # Normal range - log only every 10 checks or on large changes
                if self._check_count % 10 == 0:
                    logger.info(f"TCP OK: {count} connections. States: {states}")
                else:
                    logger.debug(f"TCP Status: {count} connections")

        except psutil.AccessDenied:
            logger.warning("Access denied for connection monitoring. Run with elevated privileges.")
        except Exception as e:
            logger.error(f"Failed to check connections: {e}")

    def _categorize_connections(self, connections) -> Dict[str, int]:
        """Categorize connections by state"""
        states: Dict[str, int] = {}
        for conn in connections:
            state = conn.status if hasattr(conn, 'status') else 'UNKNOWN'
            states[state] = states.get(state, 0) + 1
        return states

    def get_current_count(self) -> int:
        """Return the last observed connection count"""
        return self._last_count

    async def get_connection_stats(self) -> Dict[str, Any]:
        """
        Return detailed info about the current connection state

        Returns:
            dict with connection statistics
        """
        if not PSUTIL_AVAILABLE:
            return {'error': 'psutil not available', 'count': -1}

        try:
            connections = psutil.net_connections(kind='tcp')
            states = self._categorize_connections(connections)

            return {
                'count': len(connections),
                'states': states,
                'warning_threshold': self.warning_threshold,
                'critical_threshold': self.critical_threshold,
                'status': self._get_status_level(len(connections)),
                'check_count': self._check_count
            }
        except Exception as e:
            return {'error': str(e), 'count': -1}

    def _get_status_level(self, count: int) -> str:
        """Return the status level for the given connection count"""
        if count >= self.emergency_threshold:
            return 'EMERGENCY'
        elif count >= self.critical_threshold:
            return 'CRITICAL'
        elif count >= self.warning_threshold:
            return 'WARNING'
        else:
            return 'OK'


# Singleton instance
_monitor_instance: Optional[ConnectionMonitor] = None


def get_connection_monitor(
    warning_threshold: int = 5000,
    critical_threshold: int = 10000
) -> ConnectionMonitor:
    """Return the singleton ConnectionMonitor instance"""
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = ConnectionMonitor(
            warning_threshold=warning_threshold,
            critical_threshold=critical_threshold
        )
    return _monitor_instance


async def quick_connection_check() -> int:
    """Quick one-off connection count check"""
    if not PSUTIL_AVAILABLE:
        return -1
    try:
        connections = psutil.net_connections(kind='tcp')
        return len(connections)
    except Exception:
        return -1
