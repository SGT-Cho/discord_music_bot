"""Discord channel notifications for playback and operational events.

Two separate audiences:

- **User notifications** go to the text channel the music session lives in.
  They are for things a listener notices anyway ("the song stopped") and
  would otherwise have to guess about.
- **Operational notifications** go to the channel named by ``OPS_CHANNEL_ID``.
  They are for whoever runs the bot: cache failures, yt-dlp trouble, and
  other things listeners cannot act on.

Both paths share the same suppression logic. A failing playlist can raise the
same error once per track, so a naive implementation floods the channel. Every
send carries a dedup key; repeats inside the cooldown window are counted
instead of sent, and the next message that does go out reports the backlog.
"""

import logging
import os
import time
from typing import Optional

import discord

from .redaction import redact_input
from ..i18n import t

logger = logging.getLogger(__name__)

# Suppression window for repeated notifications with the same dedup key.
DEFAULT_COOLDOWN_SECONDS = 60.0

# Above this many tracked dedup keys, drop the ones whose cooldown has long
# expired. Keys are (guild_id, kind) pairs, so this only grows with guild
# count, but the bot is long-lived and nothing else would ever evict them.
_PRUNE_THRESHOLD = 512


class Notifier:
    """Sends user-facing and operational notifications with flood control."""

    def __init__(self, bot, *, cooldown: float = DEFAULT_COOLDOWN_SECONDS):
        self.bot = bot
        self.cooldown = cooldown
        self._last_sent: dict[tuple, float] = {}
        self._suppressed: dict[tuple, int] = {}

        raw_ops_channel = os.getenv("OPS_CHANNEL_ID", "").strip()
        self.ops_channel_id: Optional[int] = None
        if raw_ops_channel:
            try:
                self.ops_channel_id = int(raw_ops_channel)
            except ValueError:
                logger.warning(
                    "OPS_CHANNEL_ID is not a valid channel ID; "
                    "operational notifications are disabled"
                )

    # ------------------------------------------------------------------
    # Flood control
    # ------------------------------------------------------------------

    def _check_cooldown(self, key: tuple) -> tuple[bool, int]:
        """Decide whether to send, and how many sends were suppressed before.

        Returns (should_send, suppressed_count). When should_send is False the
        caller stays silent; the event is counted and reported by whichever
        message goes out after the cooldown expires.
        """
        now = time.monotonic()
        last = self._last_sent.get(key)

        if last is not None and (now - last) < self.cooldown:
            self._suppressed[key] = self._suppressed.get(key, 0) + 1
            return False, 0

        suppressed = self._suppressed.pop(key, 0)
        self._last_sent[key] = now
        self._prune(now)
        return True, suppressed

    def _prune(self, now: float) -> None:
        """Drop dedup keys whose cooldown expired long ago."""
        if len(self._last_sent) < _PRUNE_THRESHOLD:
            return
        stale_after = self.cooldown * 10
        stale = [k for k, ts in self._last_sent.items() if (now - ts) > stale_after]
        for key in stale:
            self._last_sent.pop(key, None)
            self._suppressed.pop(key, None)

    def reset(self, guild_id: int) -> None:
        """Forget suppression state for a guild.

        Called when a session ends so that the next session starts clean
        instead of silently swallowing its first error.
        """
        for store in (self._last_sent, self._suppressed):
            for key in [k for k in store if k[0] == guild_id]:
                store.pop(key, None)

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def notify_user(
        self,
        channel: Optional[discord.abc.Messageable],
        *,
        guild_id: int,
        kind: str,
        message: str,
        color: discord.Color = discord.Color.orange(),
    ) -> bool:
        """Send a user-facing notice to the session's text channel.

        Returns True if a message was sent. A missing channel is not an
        error: the bot can be playing without ever having posted to a text
        channel, and losing the notice is better than crashing the caller.
        """
        if channel is None:
            logger.debug(
                f"[Notify Guild {guild_id}] No target channel for '{kind}'; "
                f"logged only: {message}"
            )
            return False

        should_send, suppressed = self._check_cooldown((guild_id, kind))
        if not should_send:
            return False

        if suppressed:
            message = f"{message}\n{t('notify_suppressed_suffix', count=suppressed)}"

        try:
            await channel.send(embed=discord.Embed(description=message, color=color))
            return True
        except discord.Forbidden:
            logger.warning(
                f"[Notify Guild {guild_id}] Missing permission to post in "
                f"#{getattr(channel, 'name', 'unknown')}"
            )
        except discord.HTTPException as e:
            logger.warning(f"[Notify Guild {guild_id}] Failed to send '{kind}': {e}")
        return False

    async def notify_ops(
        self,
        *,
        kind: str,
        title: str,
        message: str,
        color: discord.Color = discord.Color.red(),
        guild_id: int = 0,
    ) -> bool:
        """Send an operator-facing notice to the ops channel, if configured."""
        if self.ops_channel_id is None:
            return False

        should_send, suppressed = self._check_cooldown((guild_id, f"ops:{kind}"))
        if not should_send:
            return False

        channel = self.bot.get_channel(self.ops_channel_id)
        if channel is None:
            logger.warning(
                f"OPS_CHANNEL_ID {self.ops_channel_id} is not visible to the bot; "
                f"dropping '{kind}' notification"
            )
            return False

        if suppressed:
            message = f"{message}\n{t('notify_suppressed_suffix', count=suppressed)}"

        embed = discord.Embed(title=title, description=message, color=color)
        try:
            await channel.send(embed=embed)
            return True
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"Failed to send ops notification '{kind}': {e}")
        return False

    async def notify_ops_exception(
        self, *, kind: str, title: str, context: str, error: BaseException
    ) -> bool:
        """Report an exception to the ops channel with the message redacted.

        Exception text routinely carries signed stream URLs, so it goes
        through the same redaction the logs use.
        """
        detail = f"{type(error).__name__}: {redact_input(error, limit=1400)}"
        return await self.notify_ops(
            kind=kind,
            title=title,
            message=f"{context}\n```\n{detail[:1500]}\n```",
        )
