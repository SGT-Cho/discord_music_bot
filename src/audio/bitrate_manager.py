import discord
import logging
from typing import Dict, Optional

from src.i18n import t

logger = logging.getLogger(__name__)

class BitrateManager:
    """Manages bitrate based on Discord server boost level"""

    # Default bitrate per server boost level (kbps)
    BOOST_LEVEL_BITRATES = {
        0: 128,  # No boost (default raised from 96 to 128)
        1: 192,  # Level 1
        2: 256,  # Level 2
        3: 384,  # Level 3
    }

    # Maximum bitrate limit
    MAX_BITRATE = 384  # Allow up to 384kbps for boost level 3 servers

    def __init__(self):
        # Per-guild custom bitrate settings
        self.custom_bitrates: Dict[int, int] = {}

    def get_channel_max_bitrate(self, voice_channel: discord.VoiceChannel) -> int:
        """Return the voice channel's maximum bitrate (bps)"""
        if not voice_channel:
            return 128000  # Default 128kbps (raised)

        # Discord stores bitrate in bps
        return voice_channel.bitrate

    def get_guild_boost_bitrate(self, guild: discord.Guild) -> int:
        """Return the recommended bitrate for the guild's boost level (kbps)"""
        boost_level = guild.premium_tier if guild else 0
        base_bitrate = self.BOOST_LEVEL_BITRATES.get(boost_level, 128)  # Default raised to 128

        # Apply maximum bitrate limit
        return min(base_bitrate, self.MAX_BITRATE)

    def get_optimal_bitrate(self, guild: discord.Guild, voice_channel: Optional[discord.VoiceChannel] = None) -> int:
        """Calculate the optimal bitrate (kbps)

        Logic:
        1. Use the custom setting if one exists
        2. Use the channel's maximum bitrate as the default
        3. Fall back to the boost level only when no channel is available
        4. Cap at 384kbps
        """
        guild_id = guild.id if guild else None

        # 1. Check for a custom setting (user explicitly lowered it)
        if guild_id and guild_id in self.custom_bitrates:
            custom_bitrate = self.custom_bitrates[guild_id]
            logger.info(f"Using custom bitrate for guild {guild_id}: {custom_bitrate}kbps")
            return min(custom_bitrate, self.MAX_BITRATE)

        # 2. Prefer the channel's maximum bitrate
        if voice_channel:
            channel_max_kbps = self.get_channel_max_bitrate(voice_channel) // 1000
            optimal_bitrate = channel_max_kbps  # Use the channel maximum as-is
        else:
            # 3. Use the boost level only when no channel is available
            boost_bitrate = self.get_guild_boost_bitrate(guild)
            optimal_bitrate = boost_bitrate

        # 4. Apply the final limit (max 384kbps)
        final_bitrate = min(optimal_bitrate, self.MAX_BITRATE)

        logger.info(
            f"Calculated optimal bitrate for guild {guild_id}: "
            f"{final_bitrate}kbps (channel max: {channel_max_kbps if voice_channel else 'N/A'}kbps, "
            f"using channel maximum by default)"
        )

        return final_bitrate

    def set_custom_bitrate(self, guild_id: int, bitrate: int) -> bool:
        """Set a custom bitrate for a guild

        Args:
            guild_id: Guild ID
            bitrate: Bitrate (kbps); valid values: 64, 96, 128, 256, 384

        Returns:
            Whether the setting was applied
        """
        valid_bitrates = [64, 96, 128, 256, 384]

        if bitrate not in valid_bitrates:
            logger.warning(f"Invalid bitrate value: {bitrate}. Must be one of {valid_bitrates}")
            return False

        self.custom_bitrates[guild_id] = bitrate
        logger.info(f"Set custom bitrate for guild {guild_id}: {bitrate}kbps")
        return True

    def clear_custom_bitrate(self, guild_id: int) -> None:
        """Remove the guild's custom bitrate setting"""
        if guild_id in self.custom_bitrates:
            del self.custom_bitrates[guild_id]
            logger.info(f"Cleared custom bitrate for guild {guild_id}")

    def get_ffmpeg_audio_options(self, bitrate_kbps: int) -> str:
        """Return FFmpeg audio options for the given bitrate"""
        return f"-b:a {bitrate_kbps}k"

    def get_status_string(self, guild: discord.Guild, voice_channel: Optional[discord.VoiceChannel] = None) -> str:
        """Return the current bitrate status string"""
        guild_id = guild.id if guild else None
        current_bitrate = self.get_optimal_bitrate(guild, voice_channel)

        status_parts = [t("bitrate_status_current", bitrate=current_bitrate)]

        if guild_id and guild_id in self.custom_bitrates:
            status_parts.append(t("bitrate_status_user_set"))
        else:
            if voice_channel:
                channel_max = self.get_channel_max_bitrate(voice_channel) // 1000
                status_parts.append(t("bitrate_status_channel_max", channel_max=channel_max))
            else:
                boost_level = guild.premium_tier if guild else 0
                status_parts.append(t("bitrate_status_boost_default", boost_level=boost_level))

        return " ".join(status_parts)

# Global instance
bitrate_manager = BitrateManager()
