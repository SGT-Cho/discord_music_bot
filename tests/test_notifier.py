"""Tests for the error notification layer.

These cover the parts that are easy to get wrong and impossible to notice in
production: flood control (a broken playlist raises the same error once per
track) and redaction (exception text routinely carries signed stream URLs).
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.notifier import Notifier


@pytest.fixture
def clock(monkeypatch):
    """Controllable replacement for time.monotonic inside the notifier."""

    class Clock:
        def __init__(self):
            self.now = 1000.0

        def advance(self, seconds):
            self.now += seconds

    instance = Clock()
    monkeypatch.setattr(
        "src.utils.notifier.time.monotonic", lambda: instance.now
    )
    return instance


@pytest.fixture
def channel():
    fake = MagicMock(spec=discord.TextChannel)
    fake.send = AsyncMock()
    fake.name = "music"
    return fake


@pytest.fixture
def notifier(monkeypatch):
    monkeypatch.delenv("OPS_CHANNEL_ID", raising=False)
    return Notifier(MagicMock(), cooldown=60.0)


class TestUserNotifications:
    @pytest.mark.asyncio
    async def test_first_notification_is_sent(self, notifier, channel, clock):
        sent = await notifier.notify_user(
            channel, guild_id=1, kind="track_failed", message="boom"
        )
        assert sent is True
        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_repeat_within_cooldown_is_suppressed(self, notifier, channel, clock):
        await notifier.notify_user(channel, guild_id=1, kind="track_failed", message="boom")
        channel.send.reset_mock()

        clock.advance(5)
        for _ in range(10):
            sent = await notifier.notify_user(
                channel, guild_id=1, kind="track_failed", message="boom"
            )
            assert sent is False

        channel.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_suppressed_count_is_reported_after_cooldown(
        self, notifier, channel, clock
    ):
        await notifier.notify_user(channel, guild_id=1, kind="track_failed", message="boom")
        clock.advance(5)
        for _ in range(3):
            await notifier.notify_user(
                channel, guild_id=1, kind="track_failed", message="boom"
            )

        channel.send.reset_mock()
        clock.advance(120)
        sent = await notifier.notify_user(
            channel, guild_id=1, kind="track_failed", message="boom"
        )

        assert sent is True
        description = channel.send.await_args.kwargs["embed"].description
        assert "3" in description, f"suppressed count missing from: {description!r}"

    @pytest.mark.asyncio
    async def test_different_kinds_do_not_suppress_each_other(
        self, notifier, channel, clock
    ):
        await notifier.notify_user(channel, guild_id=1, kind="track_failed", message="a")
        sent = await notifier.notify_user(
            channel, guild_id=1, kind="stream_exhausted", message="b"
        )
        assert sent is True

    @pytest.mark.asyncio
    async def test_different_guilds_do_not_suppress_each_other(
        self, notifier, channel, clock
    ):
        await notifier.notify_user(channel, guild_id=1, kind="track_failed", message="a")
        sent = await notifier.notify_user(
            channel, guild_id=2, kind="track_failed", message="a"
        )
        assert sent is True

    @pytest.mark.asyncio
    async def test_missing_channel_is_not_an_error(self, notifier, clock):
        assert await notifier.notify_user(
            None, guild_id=1, kind="track_failed", message="boom"
        ) is False

    @pytest.mark.asyncio
    async def test_forbidden_channel_does_not_raise(self, notifier, channel, clock):
        channel.send.side_effect = discord.Forbidden(MagicMock(status=403), "nope")
        assert await notifier.notify_user(
            channel, guild_id=1, kind="track_failed", message="boom"
        ) is False

    @pytest.mark.asyncio
    async def test_reset_clears_only_the_given_guild(self, notifier, channel, clock):
        await notifier.notify_user(channel, guild_id=1, kind="track_failed", message="a")
        await notifier.notify_user(channel, guild_id=2, kind="track_failed", message="a")

        notifier.reset(1)
        channel.send.reset_mock()

        assert await notifier.notify_user(
            channel, guild_id=1, kind="track_failed", message="a"
        ) is True
        assert await notifier.notify_user(
            channel, guild_id=2, kind="track_failed", message="a"
        ) is False


class TestOpsNotifications:
    @pytest.mark.asyncio
    async def test_disabled_without_ops_channel_id(self, notifier, clock):
        assert await notifier.notify_ops(kind="k", title="t", message="m") is False

    @pytest.mark.asyncio
    async def test_sends_to_configured_ops_channel(self, monkeypatch, channel, clock):
        monkeypatch.setenv("OPS_CHANNEL_ID", "424242")
        bot = MagicMock()
        bot.get_channel.return_value = channel

        notifier = Notifier(bot, cooldown=60.0)
        assert await notifier.notify_ops(kind="k", title="t", message="m") is True
        bot.get_channel.assert_called_once_with(424242)

    @pytest.mark.asyncio
    async def test_invalid_ops_channel_id_disables_instead_of_crashing(
        self, monkeypatch, clock
    ):
        monkeypatch.setenv("OPS_CHANNEL_ID", "not-a-number")
        notifier = Notifier(MagicMock(), cooldown=60.0)
        assert notifier.ops_channel_id is None

    @pytest.mark.asyncio
    async def test_exception_report_redacts_signed_urls(
        self, monkeypatch, channel, clock
    ):
        monkeypatch.setenv("OPS_CHANNEL_ID", "424242")
        bot = MagicMock()
        bot.get_channel.return_value = channel
        notifier = Notifier(bot, cooldown=60.0)

        signed = (
            "HTTP Error 403: Forbidden for "
            "https://rr3---sn-abc.googlevideo.com/videoplayback"
            "?expire=1234567890&signature=SECRETSIGNATURE&ip=203.0.113.7"
        )
        await notifier.notify_ops_exception(
            kind="k", title="t", context="c", error=RuntimeError(signed)
        )

        description = channel.send.await_args.kwargs["embed"].description
        assert "SECRETSIGNATURE" not in description
        assert "203.0.113.7" not in description
        # The useful part still survives.
        assert "403" in description
        assert "googlevideo.com/videoplayback" in description
