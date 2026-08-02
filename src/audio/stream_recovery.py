import asyncio
import logging
from typing import Optional, Callable, Any
import discord

logger = logging.getLogger(__name__)

class StreamRecoveryHandler:
    """Stream recovery handler"""

    def __init__(self, max_retries: int = 3, retry_delay: float = 2.0):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.recovery_callbacks = {}

    async def recover_stream(self,
                           voice_client: discord.VoiceClient,
                           track_data: dict,
                           create_source_func: Callable,
                           after_callback: Optional[Callable] = None) -> bool:
        """Attempt stream recovery

        Args:
            voice_client: Discord voice client
            track_data: Track info
            create_source_func: Audio source creation function
            after_callback: Callback invoked after playback finishes

        Returns:
            Whether recovery succeeded
        """
        if not voice_client or not voice_client.is_connected():
            logger.error("Voice client not connected, cannot recover stream")
            return False

        track_title = track_data.get('title', 'Unknown')
        track_url = track_data.get('url')

        if not track_url:
            logger.error(f"No URL available for track: {track_title}")
            return False

        for attempt in range(self.max_retries):
            try:
                logger.info(f"Stream recovery attempt {attempt + 1}/{self.max_retries} for: {track_title}")

                # Wait briefly
                await asyncio.sleep(self.retry_delay * (attempt + 1))

                # Create a new audio source
                new_source = await create_source_func(track_url, data=track_data)

                if new_source:
                    # Attempt playback
                    voice_client.play(new_source, after=after_callback)
                    logger.info(f"Stream recovery successful for: {track_title}")
                    return True

            except Exception as e:
                logger.error(f"Stream recovery attempt {attempt + 1} failed: {e}")
                continue

        logger.error(f"All stream recovery attempts failed for: {track_title}")
        return False

    def register_recovery_callback(self, guild_id: int, callback: Callable):
        """Register a recovery callback for a guild"""
        self.recovery_callbacks[guild_id] = callback

    def get_recovery_callback(self, guild_id: int) -> Optional[Callable]:
        """Return the recovery callback for a guild"""
        return self.recovery_callbacks.get(guild_id)

    async def monitor_voice_connection(self, voice_client: discord.VoiceClient, guild_id: int):
        """Monitor the voice connection"""
        while voice_client and voice_client.is_connected():
            try:
                # Check websocket ping
                if voice_client.ws and voice_client.ws.latency > 1.0:
                    logger.warning(f"High voice latency detected: {voice_client.ws.latency:.2f}s")

                # Check every 30 seconds
                await asyncio.sleep(30)

            except Exception as e:
                logger.error(f"Voice connection monitoring error: {e}")

                # Run the recovery callback
                callback = self.get_recovery_callback(guild_id)
                if callback:
                    await callback()
                break

# Global instance
stream_recovery = StreamRecoveryHandler()
