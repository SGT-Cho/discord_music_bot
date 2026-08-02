import discord
from discord import app_commands
import traceback
import logging

from src.i18n import t

logger = logging.getLogger(__name__)

class ErrorHandler:
    @staticmethod
    async def handle_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Slash command error handler"""
        
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                t("err_cooldown", retry_after=f"{error.retry_after:.1f}"),
                ephemeral=True
            )

        elif isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                t("err_missing_perms"),
                ephemeral=True
            )

        elif isinstance(error, app_commands.BotMissingPermissions):
            missing = ", ".join(error.missing_permissions)
            await interaction.response.send_message(
                t("err_bot_missing_perms", permissions=missing),
                ephemeral=True
            )

        elif isinstance(error, app_commands.CommandNotFound):
            await interaction.response.send_message(
                t("err_unknown_command"),
                ephemeral=True
            )
        
        else:
            logger.error(f"Unhandled error in command {interaction.command}: {error}")
            logger.error(traceback.format_exc())

            # Provide a specific message depending on the error type
            error_type = type(error).__name__

            if isinstance(error, discord.HTTPException):
                error_str = str(error)
                # Detect embed-related errors
                if "embed" in error_str.lower() and "256" in error_str:
                    error_message = t("err_embed_title_too_long")
                elif "embed" in error_str.lower():
                    error_message = t("err_embed_format")
                else:
                    error_message = t("err_discord_api", status=error.status, text=error.text if hasattr(error, 'text') else t("err_unknown_error"))
            else:
                error_message = t("err_command_failed", error_type=error_type)

            # Detailed log for developers
            logger.error(f"Error details - Type: {error_type}, Message: {error}")

            if interaction.response.is_done():
                await interaction.followup.send(error_message, ephemeral=True)
            else:
                await interaction.response.send_message(error_message, ephemeral=True)

    @staticmethod
    async def handle_voice_error(ctx, error):
        """Voice-related error handler"""
        
        if isinstance(error, discord.ClientException):
            return t("err_voice_already_connected")

        elif isinstance(error, discord.opus.OpusNotLoaded):
            return t("err_opus_not_loaded")

        else:
            logger.error(f"Voice error: {error}")
            return t("err_voice_generic")