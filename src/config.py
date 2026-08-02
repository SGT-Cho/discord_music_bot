# NOTE: This Config class is currently unused by music_bot.py.
# The main bot file uses its own inline configuration.
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Discord Bot
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("discord_token")
    
    # Music Services
    SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
    SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
    APPLE_MUSIC_API_KEY = os.getenv("APPLE_MUSIC_API_KEY")
    
    # Bot Settings
    DEFAULT_VOLUME = 0.5
    MAX_QUEUE_SIZE = 1000
    MAX_PLAYLIST_ITEMS = 20
    AUTOPLAY_ENABLED_DEFAULT = True
    
    # Timeouts (seconds)
    TIMEOUT_INITIAL_EXTRACT_FLAT_FALSE = 180.0
    TIMEOUT_INITIAL_EXTRACT_FLAT_TRUE = 20.0
    TIMEOUT_PLAYLIST_RESCAN_ENTRIES = 180.0
    TIMEOUT_INDIVIDUAL_ITEM_DETAIL = 20.0
    
    # YouTube DL Options
    YTDL_FORMAT_OPTIONS = {
        'format': 'bestaudio/best',
        'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        'restrictfilenames': True,
        'noplaylist': False,
        'nocheckcertificate': False,
        'ignoreerrors': True,
        'logtostderr': False,
        'quiet': True,
        'no_warnings': True,
        'default_search': 'auto',
        'source_address': '0.0.0.0',
        'extract_flat': False,
        'skip_download': True,
        'force_generic_extractor': False,
    }
    
    # FFmpeg Options
    FFMPEG_OPTIONS = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn',
    }
    
    # Opus Library Paths
    OPUS_PATHS = [
        '/opt/homebrew/lib/libopus.dylib',
        '/usr/local/lib/libopus.dylib',
        'libopus.0.dylib',
        'libopus.dylib',
        'opus'
    ]
