import asyncio
import discord
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv
import yt_dlp as youtube_dl # use yt_dlp under the youtube_dl alias
import datetime
import logging
from logging.handlers import RotatingFileHandler
import re
import sys
import shutil
import importlib.util
from collections import deque
from pathlib import Path

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from src.utils import ErrorHandler, measure_performance, performance_monitor, redact_input
from src.utils.notifier import Notifier
from src.utils.ytdl_manager import YTDLManager, managed_ytdl
from src.utils.connection_monitor import ConnectionMonitor, get_connection_monitor
from src.audio import FFmpegOptimizer, stream_recovery, bitrate_manager
from src.sources import source_resolver, SourceType
from src.cache import audio_cache_manager
from src.i18n import t

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('bot.log', maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

def _detect_js_runtime():
    """Return the first supported JavaScript runtime available on PATH."""
    for executable in ("deno", "node", "nodejs", "bun", "qjs"):
        if shutil.which(executable):
            return executable
    return None

_js_runtime = _detect_js_runtime()
if not _js_runtime:
    raise RuntimeError(
        "No JavaScript runtime detected for yt-dlp EJS. "
        "Install Deno or Node.js before starting the bot."
    )
logger.info(f"JavaScript runtime detected for yt-dlp EJS: {_js_runtime}")

if importlib.util.find_spec("yt_dlp_ejs") is None:
    raise RuntimeError(
        "yt-dlp-ejs is not installed. Install dependencies with "
        '\'python -m pip install -r requirements.txt\'.'
    )

# Preload Discord Opus library (performance optimization)
if not FFmpegOptimizer.validate_opus_loaded():
    logger.warning("Failed to load the Opus library. Voice playback may not work properly.")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="/", description=t("bot_description"), intents=intents)

youtube_dl.utils.bug_reports_message = lambda *args, **kwargs: ''

ytdl_format_options = {
    'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',  # prefer better formats
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': False,  # enable playlist processing
    'nocheckcertificate': False,
    'ignoreerrors': True,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',  # default to YouTube search
    'source_address': '0.0.0.0',
    'extract_flat': False,
    'skip_download': True,
    'force_generic_extractor': False,
    'http_chunk_size': 10485760,  # 10MB chunk size
    # Settings for the 2025.10 YouTube API changes
    'socket_timeout': 30,  # explicit timeout (30s)
    'retries': 3,  # automatic retries (3)
    'fragment_retries': 3,  # fragment stream retries (3)
    'cachedir': False,  # disable cache (prevents stale signatures - important!)
}

# Managed by FFmpegOptimizer (default options)
ffmpeg_options = FFmpegOptimizer.get_optimized_options('youtube')

class YTDLSource(discord.PCMVolumeTransformer):
    _playlist_semaphore = asyncio.Semaphore(5)  # limit concurrent playlist item extraction

    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        # Try various title fields to find a usable title
        self.title = (
            data.get("title") or 
            data.get("alt_title") or 
            data.get("track") or 
            data.get("fulltitle") or 
            data.get("description", "")[:50] or
            t("track_untitled")
        )
        # Use a fallback title if 'videoplayback' or empty
        if self.title.lower() in ['videoplayback', '', 'none', 'null']:
            # Try extracting video ID from URL
            if data.get('id'):
                self.title = t("track_youtube_video", video_id=data.get('id'))
            else:
                self.title = t("track_untitled")

        # Truncate overly long titles (leave room for embed prefix)
        if len(self.title) > 200:
            logger.warning(f"Track title too long ({len(self.title)} chars), truncating: {self.title[:50]}...")
            self.title = self.title[:197] + "..."

        # Set video_url: extract the actual YouTube video ID
        video_id = data.get('id', '')
        webpage_url = data.get("webpage_url", "")

        # Debug: log raw data (extended)
        logger.info(f"[YTDLSource] Creating track: {self.title}")
        logger.info(f"[YTDLSource] - data['id']: {data.get('id', 'None')}")
        logger.info(f"[YTDLSource] - data['display_id']: {data.get('display_id', 'None')}")
        logger.info(f"[YTDLSource] - data['webpage_url']: {redact_input(webpage_url)}")
        logger.info(f"[YTDLSource] - data.keys(): {list(data.keys())}")  # print all available keys
        logger.info(f"[YTDLSource] - data.get('original_url'): {redact_input(data.get('original_url'))}")
        logger.info(f"[YTDLSource] - data.get('url'): {redact_input(data.get('url'))}")

        # Filter invalid IDs and probe fallback fields
        if not video_id or 'videoplayback' in video_id or '?' in video_id or len(video_id) > 15:
            logger.warning(f"[YTDLSource] Invalid video_id from data['id']: {video_id}, trying alternatives...")
            # Try fallback fields
            video_id = (
                data.get('display_id') or
                data.get('video_id') or
                data.get('watch_id') or
                ''
            )
            logger.info(f"[YTDLSource] - Alternative ID attempt: {video_id}")

        # Try extracting video ID from webpage_url
        if not video_id or 'videoplayback' in video_id or '?' in video_id:
            if "watch?v=" in webpage_url:
                extracted_id = webpage_url.split("watch?v=")[-1].split("&")[0]
                if extracted_id and len(extracted_id) == 11:
                    video_id = extracted_id
                    logger.info(f"[YTDLSource] - Extracted ID from webpage_url: {video_id}")
            elif "youtu.be/" in webpage_url:
                extracted_id = webpage_url.split("youtu.be/")[-1].split("?")[0]
                if extracted_id and len(extracted_id) == 11:
                    video_id = extracted_id
                    logger.info(f"[YTDLSource] - Extracted ID from youtu.be URL: {video_id}")

        # Try extracting directly from the URL field (last resort)
        if not video_id or 'videoplayback' in video_id or '?' in video_id:
            url_field = data.get("url", "")
            if "youtube.com" in url_field and "watch?v=" in url_field:
                extracted_id = url_field.split("watch?v=")[-1].split("&")[0]
                if extracted_id and len(extracted_id) == 11:
                    video_id = extracted_id
                    logger.info(f"[YTDLSource] - Extracted ID from data['url']: {video_id}")

        # Validate video ID (11 chars, alphanumeric plus -_)
        if video_id and len(video_id) == 11 and all(c.isalnum() or c in '-_' for c in video_id):
            self.video_url = f"https://www.youtube.com/watch?v={video_id}"
            logger.info(f"[YTDLSource] ✅ Valid video ID: {video_id}")
        else:
            # Fall back to webpage_url if invalid
            if webpage_url and "youtube.com" in webpage_url and "googlevideo" not in webpage_url:
                self.video_url = webpage_url
                logger.warning(f"[YTDLSource] ⚠️ Invalid video ID '{video_id}', using webpage_url")
            else:
                self.video_url = "https://www.youtube.com/"
                logger.error(f"[YTDLSource] ❌ No valid URL found! ID: {video_id}, webpage_url: {redact_input(webpage_url)}")

        logger.info(f"[YTDLSource] - Final video_url: {redact_input(self.video_url)}")

        self.thumbnail = data.get("thumbnail", "https://i.imgur.com/Tt6jwFk.png")
        self.duration = data.get("duration")
        self.uploader = data.get("uploader")

    @classmethod
    def _normalize_youtube_url(cls, url: str) -> str:
        """Convert a YouTube Music URL to a regular YouTube URL"""
        if 'music.youtube.com' in url:
            # Convert YouTube Music URL to regular YouTube URL
            url = url.replace('music.youtube.com', 'www.youtube.com')
            # Normalize to watch?v= format
            if '/watch/' in url:
                url = url.replace('/watch/', '/watch?v=')
            # Handle playlists
            if '&list=' in url or '?list=' in url:
                # Extract playlist ID
                playlist_match = re.search(r'list=([A-Za-z0-9_-]+)', url)
                if playlist_match:
                    playlist_id = playlist_match.group(1)
                    # Convert to playlist-only URL
                    if 'watch?v=' not in url:
                        url = f'https://www.youtube.com/playlist?list={playlist_id}'
        return url
    
    @classmethod
    @measure_performance("youtube_dl_extract")
    async def from_url(cls, url_or_search, *, loop=None, stream=False, playlist_items_limit=20):
        loop = loop or asyncio.get_running_loop()
        
        # Normalize YouTube Music URL
        url_or_search = cls._normalize_youtube_url(url_or_search)

        # Strip &list= params from watch?v= URLs (user wants a specific video)
        # playlist?list= URLs are left unchanged (explicit playlist request)
        lower_check = url_or_search.lower()
        if "watch?v=" in lower_check and ("&list=" in lower_check or "?list=" in lower_check):
            original_for_log = url_or_search
            url_or_search = re.sub(r'[&?]list=[^&]*', '', url_or_search)
            url_or_search = re.sub(r'[&?]index=[^&]*', '', url_or_search)
            url_or_search = re.sub(r'[&?]start_radio=[^&]*', '', url_or_search)
            logger.info(f"[from_url] 🔧 Stripped playlist params from video URL: {redact_input(original_for_log)} → {redact_input(url_or_search)}")

        logger.info(f"🔍 Attempting extraction for URL/search: {redact_input(url_or_search)}")
        data = None
        original_input = url_or_search

        TIMEOUT_INITIAL_EXTRACT_FLAT_FALSE = 60.0  # reduced
        TIMEOUT_INITIAL_EXTRACT_FLAT_TRUE = 45.0   # increased for Mix/playlists (15→45s)
        TIMEOUT_PLAYLIST_RESCAN_ENTRIES = 90.0     # reduced
        TIMEOUT_INDIVIDUAL_ITEM_DETAIL = 15.0      # reduced
        
        async def _run_ytdl_extract_with_timeout(options, timeout_val, attempt_name, current_url_to_extract):
            logger.debug(f"[_run_ytdl_extract] >> Starting attempt '{attempt_name}' (URL: {redact_input(current_url_to_extract)}, timeout: {timeout_val}s)")
            _extracted_data = None
            ytdl_manager = YTDLManager.get_instance()

            # Retry logic
            max_retries = 2
            for retry in range(max_retries):
                try:
                    # Manage the YoutubeDL instance via context manager (prevents TCP connection leaks)
                    with ytdl_manager.get_ytdl(options) as _ytdl_inst:
                        def _sync_extract_operation():
                            return _ytdl_inst.extract_info(current_url_to_extract, download=not stream)

                        _extracted_data = await asyncio.wait_for(
                            loop.run_in_executor(None, _sync_extract_operation),
                            timeout=timeout_val
                        )
                    break  # exit loop on success
                except asyncio.TimeoutError:
                    if retry < max_retries - 1:
                        logger.warning(f"⏰ '{attempt_name}' timed out ({timeout_val}s), retry {retry + 1}/{max_retries - 1}")
                        await asyncio.sleep(1)  # wait 1s before retry
                    else:
                        logger.error(f"❌ '{attempt_name}' timed out ({timeout_val}s) (URL: {redact_input(current_url_to_extract)}).")
                        return None
                except youtube_dl.utils.DownloadError as de:
                    error_msg = str(de)
                    # Some errors are retryable
                    if 'HTTP Error 429' in error_msg or 'too many requests' in error_msg.lower():
                        if retry < max_retries - 1:
                            wait_time = 2 ** retry  # exponential backoff
                            logger.warning(f"⏰ Rate limit detected, retrying after {wait_time}s...")
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(f"❌ '{attempt_name}' rate limit error: {redact_input(de)}")
                            return None
                    else:
                        logger.error(f"❌ '{attempt_name}' DownloadError: {redact_input(de)}")
                        return None
                except Exception as e:
                    logger.error(f"❌ '{attempt_name}' unexpected error: {type(e).__name__}: {redact_input(e)}")
                    return None

            if _extracted_data:
                data_type = _extracted_data.get('_type', 'video')
                entries_count = len(_extracted_data.get('entries', [])) if 'entries' in _extracted_data else 0
                extracted_title = _extracted_data.get('title', 'N/A')
                logger.info(f"✅ '{attempt_name}' succeeded (Type: {data_type}, Title: {extracted_title[:50]}, Entries: {entries_count})")
            else:
                logger.warning(f"⚠️ '{attempt_name}' returned no data")
            return _extracted_data
        
        def id_is_playlist_heuristic(id_str):
            if not id_str or not isinstance(id_str, str): return False
            # Improved YouTube playlist ID patterns
            playlist_prefixes = ['PL', 'RD', 'UU', 'FL', 'LL', 'OL', 'WL']
            return any(id_str.startswith(prefix) for prefix in playlist_prefixes) or \
                   (len(id_str) > 15 and not id_str.startswith('UC'))  # UC is a channel ID

        lower_url = url_or_search.lower()
        # Detect playlist URLs (&list= already stripped from watch?v= URLs, so only explicit playlist URLs match)
        is_direct_playlist_url = ("playlist?list=" in lower_url) or \
                                 ("/playlist/" in lower_url)

        # Detect YouTube Mix URLs and route to the dedicated handler (dynamic playlists whose list= starts with RD)
        if "list=rd" in lower_url and "watch?v=" in lower_url:
            logger.info(f"🎵 [from_url] YouTube Mix URL detected → routing to from_mix_url()")
            video_id = cls.extract_video_id_from_url(url_or_search)
            if video_id:
                logger.info(f"✅ [from_url] Extracted video ID from Mix: {video_id}")
                mix_url = cls.get_youtube_mix_link(video_id)
                return await cls.from_mix_url(mix_url, loop=loop, stream=stream, playliststart=1)
            else:
                logger.warning(f"⚠️ [from_url] Failed to extract video ID from Mix URL, falling back to regular extraction")

        # Detect search queries (ytsearch, ytsearch1:, etc.) - video ID required for autoplay
        is_search_query = url_or_search.lower().startswith('ytsearch')

        # Log URL type (for debugging)
        url_type = "search query" if is_search_query else ("playlist" if is_direct_playlist_url else "single video")
        logger.info(f"📋 [from_url] URL type: {url_type}")

        if not is_direct_playlist_url:
            logger.debug("[from_url] Trying 'default options (single/search detail)' (extract_flat=False).")
            base_options = ytdl_format_options.copy()
            base_options['noplaylist'] = True  # prevent auto-expanding albums/playlists from single-video URLs
            base_options['extract_flat'] = False
            data = await _run_ytdl_extract_with_timeout(base_options, TIMEOUT_INITIAL_EXTRACT_FLAT_FALSE, "default options (single/search detail)", url_or_search)

        # ✅ Skip the extract_flat=True fallback for search queries to preserve video IDs
        if not data and not is_search_query:
            reason = "default options failed or timed out" if not is_direct_playlist_url else "direct playlist URL"
            logger.info(f"[DEBUG from_url] ({reason}) Trying 'fast options (ID list)' (extract_flat=True, timeout: {TIMEOUT_INITIAL_EXTRACT_FLAT_TRUE}s).")
            fast_options = ytdl_format_options.copy()
            fast_options.update({'extract_flat': True, 'socket_timeout': 10, 'noplaylist': not is_direct_playlist_url})
            data = await _run_ytdl_extract_with_timeout(fast_options, TIMEOUT_INITIAL_EXTRACT_FLAT_TRUE, "fast options (ID list)", url_or_search)

        if not data and not is_search_query:
            logger.info(f"[DEBUG from_url] Trying 'minimal options (ID list)' (extract_flat=True, timeout: {TIMEOUT_INITIAL_EXTRACT_FLAT_TRUE}s).")
            minimal_options = ytdl_format_options.copy()
            minimal_options.update({'format': 'worst', 'extract_flat': True, 'socket_timeout': 7, 'noplaylist': not is_direct_playlist_url})
            data = await _run_ytdl_extract_with_timeout(minimal_options, TIMEOUT_INITIAL_EXTRACT_FLAT_TRUE, "minimal options (ID list)", url_or_search)

        if not data:
            logger.error(f"No valid data obtained after all initial extraction attempts. (input: {redact_input(original_input)})")
            return []
        
        data_type_from_ytdl = data.get('_type', 'video') 
        extractor_key = data.get('extractor_key', data.get('extractor', '')).lower()
        extracted_id = data.get('id') 
        extracted_title = data.get('title', 'No title')

        logger.debug(f"[from_url] Initial data acquired: Type='{data_type_from_ytdl}', Extractor='{extractor_key}', Title='{extracted_title}', ID='{extracted_id}'")
        processed_tracks = []

        attempt_playlist_processing = False
        if data_type_from_ytdl == 'playlist':
            attempt_playlist_processing = True
            logger.debug("[from_url] Data _type is 'playlist'. Attempting playlist processing.")
        elif "entries" in data and data.get("entries") and isinstance(data["entries"], list) and data["entries"]:
            attempt_playlist_processing = True
            logger.debug("[from_url] Data has valid 'entries'. Attempting playlist processing.")
        elif extractor_key in ['youtubeplaylist', 'youtubetab'] and id_is_playlist_heuristic(extracted_id):
            attempt_playlist_processing = True
            logger.debug(f"[from_url] Extractor '{extractor_key}' and ID '{extracted_id}' look like a playlist. Attempting playlist processing (re-extract if no entries).")

        if attempt_playlist_processing:
            playlist_title = data.get('title', original_input)
            logger.debug(f"[from_url] Entering playlist processing path: '{playlist_title}'.")
            has_entries = "entries" in data and data.get("entries") and isinstance(data["entries"], list) and data["entries"]

            if not has_entries: 
                current_playlist_id_for_refetch = data.get('id') 
                if current_playlist_id_for_refetch and id_is_playlist_heuristic(current_playlist_id_for_refetch):
                    logger.debug(f"[from_url] Playlist '{playlist_title}' (ID: {current_playlist_id_for_refetch}) has no 'entries'. Attempting full re-extraction (extract_flat=False).")
                    playlist_url_to_refetch_full_entries = current_playlist_id_for_refetch 
                    entries_full_options = ytdl_format_options.copy()
                    entries_full_options.update({'extract_flat': False, 'noplaylist': False})
                    logger.debug(f"[from_url] Using this URL(ID) for full re-extraction of '{playlist_title}': {playlist_url_to_refetch_full_entries}")
                    data_with_full_entries = await _run_ytdl_extract_with_timeout(
                        entries_full_options, TIMEOUT_PLAYLIST_RESCAN_ENTRIES,
                        f"full playlist re-extraction ({current_playlist_id_for_refetch})",
                        playlist_url_to_refetch_full_entries 
                    )
                    if data_with_full_entries:
                        logger.debug(f"[from_url] Full re-extraction result: Type='{data_with_full_entries.get('_type')}', Extractor='{data_with_full_entries.get('extractor_key')}', HasEntriesKey={'entries' in data_with_full_entries}, EntriesCount={len(data_with_full_entries.get('entries', [])) if isinstance(data_with_full_entries.get('entries'), list) else 'N/A (not a list)'}")
                    else:
                        logger.debug("[from_url] Full re-extraction result: data_with_full_entries is None")
                    if data_with_full_entries and data_with_full_entries.get("entries") and \
                       isinstance(data_with_full_entries["entries"], list) and data_with_full_entries["entries"]:
                        logger.debug(f"[from_url] Full re-extraction of '{playlist_title}' succeeded. (entry count: {len(data_with_full_entries['entries'])})")
                        data = data_with_full_entries 
                        has_entries = True
                    else:
                        logger.error(f"Playlist '{playlist_title}' (ID: {current_playlist_id_for_refetch}): full re-extraction of a valid 'entries' list failed.")
                        return [] 
                else:
                    logger.warning(f"Playlist '{playlist_title}' has no 'entries' and no valid playlist ID to re-extract.")
                    return []
            
            if has_entries:
                entries_to_process = data.get("entries", [])
                logger.debug(f"[from_url] Starting per-item detail extraction for {len(entries_to_process)} entries of '{playlist_title}' (max {playlist_items_limit}).")
                # Async task list for parallel processing
                tasks = []
                for i, entry_meta in enumerate(entries_to_process):
                    if i >= playlist_items_limit: 
                        logger.debug(f"[from_url] Playlist processing limit ({playlist_items_limit}) reached.")
                        break
                    if not entry_meta or not isinstance(entry_meta, dict): 
                        logger.debug(f"[from_url] Skipping invalid playlist entry {i+1}.")
                        continue
                    
                    # Entries carrying a usable URL go straight through. The rest
                    # are checked by ID so that playlist-shaped entries are
                    # dropped here; process_playlist_item below rebuilds the URL
                    # from the entry itself, so nothing needs to be kept.
                    if not (entry_meta.get('url') and entry_meta['url'].startswith('http')):
                        video_id_from_entry = entry_meta.get("id")
                        entry_title_from_meta = entry_meta.get("title", "No title")
                        if not video_id_from_entry or id_is_playlist_heuristic(video_id_from_entry):
                            logger.debug(f"[from_url] Playlist entry '{entry_title_from_meta}' ID '{video_id_from_entry}' is not a valid video ID. Skipping.")
                            continue
                    # Create async task
                    async def process_playlist_item(item_meta, item_index):
                        try:
                            if not item_meta or not isinstance(item_meta, dict):
                                return None
                            
                            # Get URL: if it is a googlevideo URL, build a YouTube URL from the ID
                            item_url_from_meta = item_meta.get('url', '')
                            if item_url_from_meta and item_url_from_meta.startswith('http') and \
                               'googlevideo' not in item_url_from_meta and 'videoplayback' not in item_url_from_meta:
                                item_url = item_url_from_meta
                                logger.info(f"[PlaylistEntry] ✅ Using meta URL: {redact_input(item_url)}")
                            else:
                                # Build a YouTube URL from the ID when the URL is missing or googlevideo
                                item_id = item_meta.get('id')
                                # If the ID is also videoplayback, try extracting from webpage_url
                                if not item_id or 'videoplayback' in item_id or id_is_playlist_heuristic(item_id):
                                    # Use webpage_url if present and a valid YouTube URL
                                    item_webpage = item_meta.get('webpage_url', '')
                                    if item_webpage and 'youtube.com/watch?v=' in item_webpage:
                                        item_url = item_webpage
                                        logger.info(f"[PlaylistEntry] ✅ Using webpage_url from entry: {redact_input(item_url)}")
                                    else:
                                        logger.warning(f"[PlaylistEntry] ❌ No valid URL/ID for entry: {item_meta.get('title', 'Unknown')}")
                                        return None
                                else:
                                    item_url = f"https://www.youtube.com/watch?v={item_id}"
                                    logger.info(f"[PlaylistEntry] ✅ Constructed URL from ID: {item_id}")
                            
                            item_title = item_meta.get('title', 'No title')

                            # Per-item options
                            item_opts = ytdl_format_options.copy()
                            item_opts.update({
                                'extract_flat': False,
                                'socket_timeout': 10,
                                'noplaylist': True,
                                'quiet': True
                            })
                            
                            item_data = await _run_ytdl_extract_with_timeout(
                                item_opts,
                                TIMEOUT_INDIVIDUAL_ITEM_DETAIL,
                                f"entry {item_index+1}: {item_title[:30]}",
                                item_url
                            )

                            if item_data and "url" in item_data and item_data["url"]:
                                # ✅ Preserve original webpage_url: overwrite with the real YouTube URL if it is a googlevideo URL
                                if item_url and (not item_data.get("webpage_url") or "googlevideo" in item_data.get("webpage_url", "")):
                                    item_data["webpage_url"] = item_url
                                    logger.info(f"[PlaylistItem] ✅ webpage_url preserved: {redact_input(item_url)}")

                                # Check cache
                                playlist_item_vid = item_data.get('id', '')
                                cached_path = audio_cache_manager.get(playlist_item_vid) if playlist_item_vid else None

                                if cached_path:
                                    logger.info(f"[Cache] HIT (playlist): {playlist_item_vid}")
                                    audio_source = FFmpegOptimizer.create_audio_source(
                                        cached_path, volume=0.5, source_type='local'
                                    )
                                else:
                                    logger.info(f"[Cache] MISS (playlist): {playlist_item_vid}")
                                    audio_source = FFmpegOptimizer.create_audio_source(
                                        item_data["url"], volume=0.5, source_type='youtube',
                                        http_headers=item_data.get('http_headers'),
                                    )
                                    if playlist_item_vid:
                                        await audio_cache_manager.schedule_cache(
                                            playlist_item_vid,
                                            item_data.get("webpage_url", item_url),
                                            item_data
                                        )

                                return cls(audio_source, data=item_data)
                            return None
                        except Exception as e:
                            logger.warning(f"Entry {item_index+1} processing failed: {redact_input(e)}")
                            return None
                    
                    async def _limited_process(em, idx):
                        async with cls._playlist_semaphore:
                            return await process_playlist_item(em, idx)
                    task = _limited_process(entry_meta, i)
                    tasks.append(task)
                
                # Run in parallel and collect results
                if tasks:
                    logger.info(f"Processing {len(tasks)} playlist entries in parallel...")
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for result in results:
                        if result and not isinstance(result, Exception):
                            processed_tracks.append(result)
                
                if processed_tracks: logger.info(f"Finished processing {len(processed_tracks)} playlist entries.")
                elif entries_to_process: logger.warning(f"All entries in playlist '{playlist_title}' failed to process (no resulting tracks).")
        else: 
            target_data_for_single = data 
            single_title = target_data_for_single.get("title", "No title")
            single_video_id = target_data_for_single.get("id")
            if id_is_playlist_heuristic(single_video_id):
                logger.warning(f"Entered single-track path but ID '{single_video_id}' looks like a playlist ID (Title: '{single_title}'). Skipping.")
                return []
            logger.debug(f"[from_url] Starting single-track processing: '{single_title}' (ID: {single_video_id})")
            if target_data_for_single.get("requires_premium", False): logger.warning(f"'{single_title}' is a premium-only video."); return []
            has_valid_stream_url = False
            current_stream_url = target_data_for_single.get("url")
            if current_stream_url and isinstance(current_stream_url, str) and current_stream_url.startswith("http") and \
               ("googlevideo.com" in current_stream_url or 
                "youtube.com" in current_stream_url or 
                any(current_stream_url.endswith(ext) for ext in (".mp4", ".webm", ".m4a", ".m3u8", ".opus"))):
                logger.debug(f"[from_url] '{single_title}' initial data already has a valid streaming URL.")
                has_valid_stream_url = True
            final_data_for_stream = target_data_for_single
            # Preserve original webpage_url (for autoplay)
            original_webpage_url = target_data_for_single.get("webpage_url") or f"https://www.youtube.com/watch?v={single_video_id}" if single_video_id else None

            if not has_valid_stream_url:
                logger.debug(f"[from_url] '{single_title}' has no streaming URL; re-extraction needed.")
                if not single_video_id: logger.error(f"'{single_title}' has no video ID required for re-extraction."); return []
                standard_video_url_for_reextract = f"https://www.youtube.com/watch?v={single_video_id}"
                logger.debug(f"[from_url] Re-extracting single-track details via standard URL: {redact_input(standard_video_url_for_reextract)}")
                re_extract_options = ytdl_format_options.copy()
                re_extract_options.update({'extract_flat': False, 'socket_timeout': 15, 'noplaylist': True})
                re_extracted_data = await _run_ytdl_extract_with_timeout(
                    re_extract_options, TIMEOUT_INDIVIDUAL_ITEM_DETAIL,
                    "single-track detail re-extraction", standard_video_url_for_reextract
                )
                if re_extracted_data and re_extracted_data.get("url") and isinstance(re_extracted_data.get("url"), str) and \
                   re_extracted_data.get("url","").startswith("http") and "googlevideo.com" in re_extracted_data["url"]:
                    final_data_for_stream = re_extracted_data
                    # ✅ Restore original webpage_url (for building autoplay Mix URLs)
                    if original_webpage_url and not final_data_for_stream.get("webpage_url"):
                        final_data_for_stream["webpage_url"] = original_webpage_url
                        logger.info(f"'{single_title}' re-extraction succeeded, streaming URL acquired. webpage_url preserved: {redact_input(original_webpage_url)}")
                    else:
                        logger.info(f"'{single_title}' re-extraction succeeded, streaming URL acquired.")
                else: logger.error(f"'{single_title}' re-extraction failed. Data was present: {re_extracted_data is not None}"); return []
            final_stream_url = final_data_for_stream.get("url")
            if not final_stream_url or not isinstance(final_stream_url, str) or not final_stream_url.startswith("http"):
                logger.warning(f"'{single_title}' final streaming URL is invalid")
                return []

            # Improved URL validation
            valid_domains = ['googlevideo.com', 'youtube.com', 'ytimg.com', 'ggpht.com']
            valid_extensions = ['.mp4', '.webm', '.m4a', '.m3u8', '.opus', '.ogg', '.mp3']
            
            if not any(domain in final_stream_url for domain in valid_domains) and \
               not any(final_stream_url.endswith(ext) for ext in valid_extensions):
                logger.warning(f"'{single_title}' streaming URL validation failed, trying anyway")
            try:
                logger.debug(f"[from_url] Creating audio source from final streaming URL: {redact_input(final_stream_url)}")
                # Check cache
                single_vid = final_data_for_stream.get('id', '')
                cached_path = audio_cache_manager.get(single_vid) if single_vid else None

                if cached_path:
                    logger.info(f"[Cache] HIT: {single_vid} - '{single_title}'")
                    audio_source = FFmpegOptimizer.create_audio_source(
                        cached_path, volume=0.5, source_type='local'
                    )
                else:
                    logger.info(f"[Cache] MISS: {single_vid} - '{single_title}'")
                    audio_source = FFmpegOptimizer.create_audio_source(
                        final_stream_url, volume=0.5, source_type='youtube',
                        http_headers=final_data_for_stream.get('http_headers'),
                    )
                    if single_vid:
                        await audio_cache_manager.schedule_cache(
                            single_vid,
                            final_data_for_stream.get("webpage_url", original_webpage_url or ""),
                            final_data_for_stream
                        )

                processed_tracks.append(cls(audio_source, data=final_data_for_stream))
                logger.info(f"Single track '{single_title}' processed.")
            except Exception as e_final_source: logger.error(f"Audio source creation failed ('{single_title}'): {redact_input(e_final_source)}"); return []

        if not processed_tracks: logger.warning(f"[from_url] No tracks were ultimately processed (input: {redact_input(original_input)}).")
        return processed_tracks

    @staticmethod
    def get_youtube_mix_link(video_id):
        return f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"

    @staticmethod
    def extract_video_id_from_url(url: str):
        """
        Extract clean 11-character video ID from any YouTube URL format.
        Returns None if URL is invalid or contains videoplayback/googlevideo.

        Supported formats:
        - https://www.youtube.com/watch?v=VIDEO_ID
        - https://youtu.be/VIDEO_ID
        - https://www.youtube.com/shorts/VIDEO_ID
        - https://music.youtube.com/watch?v=VIDEO_ID
        """
        if not url or not isinstance(url, str):
            return None

        # Skip videoplayback and googlevideo URLs (not valid video IDs)
        if 'videoplayback' in url or 'googlevideo' in url:
            return None

        video_id = None

        # Pattern 1: watch?v=VIDEO_ID
        if "watch?v=" in url:
            video_id = url.split("watch?v=")[-1].split("&")[0]
        # Pattern 2: youtu.be/VIDEO_ID
        elif "youtu.be/" in url:
            video_id = url.split("youtu.be/")[-1].split("?")[0]
        # Pattern 3: shorts/VIDEO_ID
        elif "shorts/" in url:
            video_id = url.split("shorts/")[-1].split("?")[0]

        # Validate: must be exactly 11 characters, alphanumeric + '-' + '_'
        if video_id and len(video_id) == 11 and all(c.isalnum() or c in '-_' for c in video_id):
            return video_id

        return None

    @classmethod
    async def from_mix_url(cls, mix_url, *, loop=None, stream=False, playliststart=1, max_retries=3):
        loop = loop or asyncio.get_running_loop()
        logger.info(f"[from_mix_url] Called: {redact_input(mix_url)} (start={playliststart})")
        mix_tracks = []

        def id_is_playlist_heuristic_static(id_str):
            if not id_str or not isinstance(id_str, str): return False
            return id_str.startswith('PL') or id_str.startswith('RD') or \
                   id_str.startswith('UU') or id_str.startswith('FL') or len(id_str) > 15

        # Retry logic for fetching mix playlist
        data = None
        ytdl_manager = YTDLManager.get_instance()
        for attempt in range(max_retries):
            try:
                mix_options = ytdl_format_options.copy()
                mix_options.update({
                    'extract_flat': True, 'playliststart': playliststart,
                    'playlistend': playliststart, 'noplaylist': False
                })
                # Manage the YoutubeDL instance via context manager (prevents TCP connection leaks)
                with ytdl_manager.get_ytdl(mix_options) as ytdl_mix_extract:
                    data = await loop.run_in_executor(None, lambda: ytdl_mix_extract.extract_info(mix_url, download=False))

                if data and "entries" in data and data["entries"]:
                    logger.info(f"[from_mix_url] ✅ Got {len(data['entries'])} entries (attempt {attempt+1})")
                    break
                else:
                    logger.warning(f"[from_mix_url] ⚠️ Empty entries (attempt {attempt+1}/{max_retries})")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2)  # Wait before retry
                        continue
            except Exception as e:
                logger.error(f"[from_mix_url] ❌ Mix URL error (attempt {attempt+1}/{max_retries}): {type(e).__name__}: {redact_input(e)}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                    continue
                return []

        if data and "entries" in data and data["entries"]:
            entry_meta = data["entries"][0]
            video_id = entry_meta.get("id")
            entry_title = entry_meta.get("title", "No title")
            if not video_id or id_is_playlist_heuristic_static(video_id):
                logger.error(f"[from_mix_url] ❌ Invalid mix entry ID '{video_id}': {entry_title}")
                return []
            standard_video_url = f"https://www.youtube.com/watch?v={video_id}"
            logger.info(f"[from_mix_url] Extracting details for '{entry_title}': {redact_input(standard_video_url)}")
            try:
                single_track_options = ytdl_format_options.copy()
                single_track_options.update({'extract_flat': False, 'socket_timeout': 10, 'noplaylist': True})
                # Manage the YoutubeDL instance via context manager (prevents TCP connection leaks)
                with ytdl_manager.get_ytdl(single_track_options) as ytdl_single_track:
                    full_info = await loop.run_in_executor(None, lambda: ytdl_single_track.extract_info(standard_video_url, download=False))
                if full_info and "url" in full_info and isinstance(full_info.get("url"), str) and \
                   full_info.get("url","").startswith("http") and "googlevideo.com" in full_info["url"]:
                    # Check cache
                    mix_vid = full_info.get('id', video_id)
                    cached_path = audio_cache_manager.get(mix_vid) if mix_vid else None

                    if cached_path:
                        logger.info(f"[Cache] HIT (mix): {mix_vid} - '{entry_title}'")
                        audio_source = FFmpegOptimizer.create_audio_source(
                            cached_path, volume=0.5, source_type='local'
                        )
                    else:
                        logger.info(f"[Cache] MISS (mix): {mix_vid} - '{entry_title}'")
                        audio_source = FFmpegOptimizer.create_audio_source(
                            full_info["url"], volume=0.5, source_type='youtube',
                            http_headers=full_info.get('http_headers'),
                        )
                        if mix_vid:
                            await audio_cache_manager.schedule_cache(
                                mix_vid, standard_video_url, full_info
                            )

                    mix_tracks.append(cls(audio_source, data=full_info))
                    logger.info(f"[from_mix_url] ✅ Mix track added: '{entry_title}'")
                else:
                    logger.warning(f"[from_mix_url] ⚠️ No streaming URL for '{entry_title}'")
            except Exception as e_mix_detail:
                logger.error(f"[from_mix_url] ❌ Detail extraction failed for '{entry_title}': {type(e_mix_detail).__name__}: {redact_input(e_mix_detail)}")
        else:
            logger.warning(f"[from_mix_url] ❌ No valid entries found (start={playliststart})")

        return mix_tracks

class Music(commands.Cog):
    # Settings for avoiding duplicate autoplay tracks
    AUTOPLAY_HISTORY_SIZE = 30  # keep history of last 30 tracks
    AUTOPLAY_MAX_RETRIES = 5   # max retries on duplicates
    STREAM_RECOVERY_MAX_ATTEMPTS = 3  # re-extract attempts before giving up on a track
    AUTOPLAY_REFRESH_INTERVAL = 10  # refresh reference track every 10 tracks

    def __init__(self, bot):
        self.bot = bot
        self.queue = {}
        self.current = {}
        self.reference_track = {}
        self.autoplay_index = {}
        self.is_playing = {}
        self.nowplaying_message = {}
        self.autoplay = {}
        self.prefetched_track = {}
        self.prefetch_lock = asyncio.Lock()
        self.alone_timer_task = {}  # per-guild timer for when the bot is alone
        # Playback history for autoplay duplicate prevention
        self.played_history = {}  # {guild_id: deque([video_id1, video_id2, ...])}
        self.autoplay_count = {}  # {guild_id: int} - consecutive autoplay count (for reference refresh)
        # Reconnect guard (prevents race conditions)
        self._reconnecting = {}          # guild_id -> bool (reconnect-in-progress flag)
        self._intentional_disconnect = {} # guild_id -> bool (marks intentional disconnects)
        # Cleanup task to prevent TCP connection leaks
        self._cleanup_task = None
        self._connection_monitor = None
        # Voice connection monitor tasks (one per guild)
        self._voice_monitor_tasks: dict[int, asyncio.Task] = {}
        # Error notifications (user-facing + operational)
        self.notifier = Notifier(bot)
        # Last text channel a command was used in, per guild. The nowplaying
        # message is the preferred notification target, but it does not exist
        # yet when the very first track fails to load.
        self.last_command_channel: dict[int, discord.abc.Messageable] = {}
        # Consecutive load/playback failures, so a fully broken queue reports
        # once instead of once per track.
        self._consecutive_failures: dict[int, int] = {}
    
    async def cog_load(self):
        for guild in self.bot.guilds:
            await self.reset_state(guild.id)

        # Background cache downloads fail silently otherwise
        audio_cache_manager.attach_notifier(self.notifier)

        # Start periodic cleanup task to prevent TCP connection leaks
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
        logger.info("Periodic cleanup task started")

        # Start connection monitoring (warning: 5000, critical: 10000)
        self._connection_monitor = get_connection_monitor(
            warning_threshold=5000,
            critical_threshold=10000
        )
        asyncio.create_task(self._connection_monitor.start_monitoring(interval=300))  # every 5 minutes
        logger.info("TCP connection monitoring started")

    async def cog_unload(self):
        """Stop cleanup tasks on cog unload"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            logger.info("Periodic cleanup task stopped")

        if self._connection_monitor:
            self._connection_monitor.stop_monitoring()
            logger.info("TCP connection monitoring stopped")

        # Clean up voice monitor tasks
        for task in self._voice_monitor_tasks.values():
            task.cancel()
        self._voice_monitor_tasks.clear()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Remember where each command came from, then allow it.

        Runs for every app command in this cog. The channel recorded here is
        the fallback notification target for failures that happen before a
        nowplaying message exists.
        """
        if interaction.guild_id and interaction.channel:
            self.last_command_channel[interaction.guild_id] = interaction.channel
        return True

    def session_channel(self, guild_id: int):
        """Return the text channel this guild's music session belongs to.

        Prefers the channel holding the nowplaying message, since that is
        where the user is already looking, and falls back to the last channel
        a command was used in. Returns None when the bot has never posted for
        this guild, in which case callers log instead of notifying.
        """
        message = self.nowplaying_message.get(guild_id)
        if message is not None:
            return message.channel
        return self.last_command_channel.get(guild_id)

    async def notify_user(self, guild_id: int, kind: str, message_key: str, **kwargs):
        """Post a user-facing notice to the guild's music channel."""
        return await self.notifier.notify_user(
            self.session_channel(guild_id),
            guild_id=guild_id,
            kind=kind,
            message=t(message_key, **kwargs),
        )

    async def _periodic_cleanup(self):
        """Periodic garbage collection and stats logging (hourly)"""
        import gc
        while True:
            try:
                await asyncio.sleep(3600)  # 1 hour

                # Force garbage collection
                collected = gc.collect()
                logger.debug(f"Garbage collection: {collected} objects collected")

                # Log YTDLManager stats
                ytdl_manager = YTDLManager.get_instance()
                stats = ytdl_manager.get_stats()
                if stats['leaked'] > 0:
                    logger.warning(f"YTDL Stats (LEAK DETECTED): {stats}")
                else:
                    logger.info(f"YTDL Stats: {stats}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Periodic cleanup error: {e}")

    def _get_video_id_from_track(self, track) -> str:
        """Extract video ID from a track"""
        if not track:
            return None

        # Try extracting from video_url
        if hasattr(track, 'video_url') and track.video_url:
            vid = YTDLSource.extract_video_id_from_url(track.video_url)
            if vid:
                return vid

        # Try data['id']
        if hasattr(track, 'data'):
            raw_id = track.data.get('id', '')
            if raw_id and len(raw_id) == 11 and all(c.isalnum() or c in '-_' for c in raw_id):
                return raw_id

            # Try fallback fields
            for field in ['display_id', 'video_id', 'watch_id']:
                alt_id = track.data.get(field, '')
                if alt_id and len(alt_id) == 11 and all(c.isalnum() or c in '-_' for c in alt_id):
                    return alt_id

        return None

    def _add_to_history(self, guild_id: int, video_id: str):
        """Add a video ID to the playback history"""
        if not video_id:
            return

        if guild_id not in self.played_history:
            self.played_history[guild_id] = deque(maxlen=self.AUTOPLAY_HISTORY_SIZE)

        # Skip if already in history (duplicate prevention)
        if video_id not in self.played_history[guild_id]:
            self.played_history[guild_id].append(video_id)
            logger.debug(f"[History Guild {guild_id}] Added: {video_id} (total: {len(self.played_history[guild_id])})")

    def _is_in_history(self, guild_id: int, video_id: str) -> bool:
        """Check whether a video ID is in the playback history"""
        if not video_id or guild_id not in self.played_history:
            return False
        return video_id in self.played_history[guild_id]

    def _should_refresh_reference(self, guild_id: int) -> bool:
        """Check whether the reference track should be refreshed"""
        count = self.autoplay_count.get(guild_id, 0)
        return count > 0 and count % self.AUTOPLAY_REFRESH_INTERVAL == 0

    def _get_queue_list(self, guild_id: int) -> list:
        """Return queue contents as a list (public API only)"""
        q = self.queue.get(guild_id)
        if q is None or q.empty():
            return []
        items = []
        while not q.empty():
            try:
                items.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break
        for item in items:
            q.put_nowait(item)
        return items

    async def reset_state(self, guild_id):
        self.queue[guild_id] = asyncio.Queue()
        self.current[guild_id] = None
        self.reference_track[guild_id] = None
        self.autoplay_index[guild_id] = 2
        self.is_playing[guild_id] = False
        self.autoplay[guild_id] = True
        self.prefetched_track[guild_id] = None
        # Reset playback history
        self.played_history[guild_id] = deque(maxlen=self.AUTOPLAY_HISTORY_SIZE)
        self.autoplay_count[guild_id] = 0
        self._reconnecting[guild_id] = False
        self._intentional_disconnect[guild_id] = False
        # Start the next session with a clean notification slate, so its first
        # error is never swallowed by the previous session's cooldown.
        self._consecutive_failures[guild_id] = 0
        self.notifier.reset(guild_id)
        # Clean up voice monitor tasks
        if guild_id in self._voice_monitor_tasks:
            self._voice_monitor_tasks[guild_id].cancel()
            del self._voice_monitor_tasks[guild_id]
        if guild_id in self.nowplaying_message and self.nowplaying_message[guild_id]: # check the message object exists
            try: await self.nowplaying_message[guild_id].delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException): pass
            del self.nowplaying_message[guild_id]
        # print(f"[DEBUG] Guild {guild_id} state reset complete.")

    async def check_alone_and_schedule_disconnect(self, guild_id):
        """Check whether the bot is alone in the voice channel and start/cancel the 5-minute timer"""
        voice_client = self.bot.get_guild(guild_id).voice_client

        # Cancel the timer if the bot is not connected to a voice channel
        if not voice_client or not voice_client.channel:
            if guild_id in self.alone_timer_task:
                self.alone_timer_task[guild_id].cancel()
                del self.alone_timer_task[guild_id]
                logger.info(f"[Guild {guild_id}] Bot not in a voice channel, timer cancelled")
            return

        # Count members in the channel (excluding bots)
        members = [m for m in voice_client.channel.members if not m.bot]

        # Bot is alone
        if len(members) == 0:
            # Don't create a new timer if one is already running
            if guild_id not in self.alone_timer_task or self.alone_timer_task[guild_id].done():
                logger.info(f"[Guild {guild_id}] Only the bot remains, starting 5-minute auto-disconnect timer")
                self.alone_timer_task[guild_id] = asyncio.create_task(
                    self.auto_disconnect_after_timeout(guild_id)
                )
        else:
            # Cancel the timer if other users are present
            if guild_id in self.alone_timer_task:
                self.alone_timer_task[guild_id].cancel()
                del self.alone_timer_task[guild_id]
                logger.info(f"[Guild {guild_id}] User joined, auto-disconnect timer cancelled")

    async def auto_disconnect_after_timeout(self, guild_id):
        """Wait 5 minutes and leave the voice channel if still alone"""
        try:
            # Wait 5 minutes (300s)
            await asyncio.sleep(300)

            # Re-check whether still alone after the timeout
            voice_client = self.bot.get_guild(guild_id).voice_client
            if not voice_client or not voice_client.channel:
                logger.info(f"[Guild {guild_id}] Post-timeout check: already disconnected")
                return

            members = [m for m in voice_client.channel.members if not m.bot]
            if len(members) == 0:
                logger.info(f"[Guild {guild_id}] Alone for 5 minutes, leaving voice channel automatically")
                self._intentional_disconnect[guild_id] = True
                await voice_client.disconnect()
                await self.reset_state(guild_id)
            else:
                logger.info(f"[Guild {guild_id}] Post-timeout check: users present, staying connected")

        except asyncio.CancelledError:
            logger.info(f"[Guild {guild_id}] Auto-disconnect timer cancelled (user rejoined)")
        except Exception as e:
            logger.error(f"[Guild {guild_id}] Error during auto-disconnect: {e}", exc_info=True)
        finally:
            # Clean up timer task
            if guild_id in self.alone_timer_task:
                del self.alone_timer_task[guild_id]

    async def update_nowplaying_ui(self, guild_or_interaction):
        guild = guild_or_interaction.guild if isinstance(guild_or_interaction, discord.Interaction) else guild_or_interaction
        if not guild : return

        guild_id = guild.id
        # Decide which channel to send the nowplaying message to
        target_channel = None
        if guild_id in self.nowplaying_message and self.nowplaying_message[guild_id]:
            target_channel = self.nowplaying_message[guild_id].channel
        elif isinstance(guild_or_interaction, discord.Interaction): # no message, but we have an interaction context
            target_channel = guild_or_interaction.channel

        if not target_channel: # still no channel; cannot update
            # print(f"[DEBUG UI Guild {guild_id}] Could not find nowplaying message channel; skipping UI update.")
            return

        nowplaying_embed = await self.create_nowplaying_embed(guild)
        try:
            if guild_id in self.nowplaying_message and self.nowplaying_message[guild_id]:
                await self.nowplaying_message[guild_id].edit(content="", embed=nowplaying_embed, view=None)
            else: # Send a new message if none exists (mostly when called after the play command's followup)
                  # or on the first play_next after the message was deleted by stop, etc.
                self.nowplaying_message[guild_id] = await target_channel.send(embed=nowplaying_embed)
        except (discord.NotFound, discord.HTTPException) as e: # NotFound: message already deleted, HTTPException: other issues
            logger.debug(f"[UI Guild {guild_id}] Could not update the nowplaying message ({e}); sending a new one.")
            try: # On failure, send a new message (if we have a channel)
                self.nowplaying_message[guild_id] = await target_channel.send(embed=nowplaying_embed)
            except Exception as e_send:
                logger.warning(f"[UI Guild {guild_id}] Failed to post a nowplaying message: {e_send}")
                if guild_id in self.nowplaying_message : del self.nowplaying_message[guild_id] # remove key on failure


    async def join_logic(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            # defer() is called in the play command; use edit_original_response here
            await interaction.edit_original_response(content=t("join_need_voice"), embed=None, view=None)
            return False
        channel = interaction.user.voice.channel
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_connected():
            if voice_client.channel != channel:
                await voice_client.move_to(channel)
                # Consider sending the move notice ephemerally, separate from play's loading message, or merging it in
                # Since join_logic is called inside play, proceed without a separate message; play shows the final one
        else:
            try:
                await channel.connect()
                await self.reset_state(interaction.guild.id)
            except Exception as e:
                logger.error(f"Voice channel connection failed: {e}")
                await interaction.edit_original_response(content=t("join_connect_failed_channel", channel=channel.name, error=e), embed=None, view=None)
                return False
        return True

    @app_commands.command(name="join", description=t("join_desc"))
    async def join(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send(t("join_need_voice"), ephemeral=True); return
        channel = interaction.user.voice.channel
        voice_client = interaction.guild.voice_client
        msg = ""
        if voice_client and voice_client.is_connected():
            if voice_client.channel != channel: await voice_client.move_to(channel); msg = t("join_moved", channel=channel.name)
            else: msg = t("join_already_connected")
        else:
            try: await channel.connect(); await self.reset_state(interaction.guild.id); msg = t("join_connected", channel=channel.name)
            except Exception as e: msg = t("join_failed", error=e)
        await interaction.followup.send(msg, ephemeral=True)

    @app_commands.command(name="play", description=t("play_desc"))
    @app_commands.describe(url_or_search=t("play_describe_url"))
    async def play(self, interaction: discord.Interaction, url_or_search: str):
        guild_id = interaction.guild.id
        logger.debug(f"[/play Guild {guild_id}] ====== Command START with: {redact_input(url_or_search)} ======")
        if guild_id not in self.queue: await self.reset_state(guild_id)

        await interaction.response.defer(ephemeral=False, thinking=True) # public response

        if not await self.join_logic(interaction): return # exit if join_logic returns False (user error, etc.)

        voice_client = interaction.guild.voice_client # re-fetch voice_client after join_logic
        if not voice_client or not voice_client.is_connected(): # if join_logic failed to connect
             # join_logic already sent the user an error message
            logger.debug(f"[/play Guild {guild_id}] voice_client still not connected after join_logic.")
            # Sending another message here could duplicate; rely on join_logic's message,
            # or make join_logic's return/exception handling clearer and decide the final message here
            return

        # Clean up existing nowplaying message
        if guild_id in self.nowplaying_message and self.nowplaying_message[guild_id]:
            try: await self.nowplaying_message[guild_id].delete(); del self.nowplaying_message[guild_id]
            except (discord.NotFound, discord.Forbidden, discord.HTTPException): pass
        
        loading_embed = discord.Embed(title=t("play_loading_title"), description=t("play_loading_desc", query=url_or_search), color=discord.Color.gold())
        try:
            # Always use followup.send after defer. wait=True guarantees a message object is returned
            self.nowplaying_message[guild_id] = await interaction.followup.send(embed=loading_embed, wait=True)
        except Exception as e:
            logger.error(f"[/play Guild {guild_id}] Failed to send 'Loading...' embed: {e}"); return

        # Normalize YouTube Music URL first
        normalized_url = YTDLSource._normalize_youtube_url(url_or_search)
        logger.info(f"[/play Guild {guild_id}] Original: {redact_input(url_or_search)} → Normalized: {redact_input(normalized_url)}")

        # Multi-source support: resolve the URL to YouTube
        resolved_urls = await source_resolver.resolve_to_youtube(normalized_url, loop=self.bot.loop)
        logger.info(f"[/play Guild {guild_id}] Resolved URLs: {[redact_input(url) for url in resolved_urls]}")
        
        if not resolved_urls:
            error_embed = discord.Embed(
                title=t("play_resolve_failed_title"),
                description=t("play_resolve_failed_desc", query=url_or_search),
                color=discord.Color.red()
            )
            await self.nowplaying_message[guild_id].edit(embed=error_embed)
            return
        
        # Update the loading message (with multi-source info)
        source_info = t("play_processing_multi", query=url_or_search, count=len(resolved_urls))
        loading_embed.description = source_info
        await self.nowplaying_message[guild_id].edit(embed=loading_embed)
        
        all_tracks = []
        for resolved_url in resolved_urls:
            try:
                tracks = await YTDLSource.from_url(resolved_url, loop=self.bot.loop, stream=True)
                if tracks:
                    all_tracks.extend(tracks)
            except Exception as e:
                logger.error(f"Failed to process resolved URL {redact_input(resolved_url)}: {redact_input(e)}")
                continue
        
        if not all_tracks:
            error_embed = discord.Embed(
                title=t("play_failed_title"),
                description=t("play_failed_desc", query=url_or_search),
                color=discord.Color.red()
            )
            await self.nowplaying_message[guild_id].edit(embed=error_embed)
            return
        
        num_added = 0; first_track_title = t("track_unknown")
        if all_tracks : first_track_title = all_tracks[0].title
        for track in all_tracks: await self.queue[guild_id].put(track); num_added += 1
        
        if num_added == 0 : # tracks list was returned but nothing was added due to filtering, etc. (should not happen in theory)
             error_embed = discord.Embed(title=t("play_no_result_title"), description=t("play_no_result_desc"), color=discord.Color.orange())
             await self.nowplaying_message[guild_id].edit(embed=error_embed); return

        # Notify the user of completion (edit the loading message)
        play_embed_title = t("play_added_single_title", title=first_track_title) if num_added == 1 else t("play_added_playlist_title", count=num_added)
        # Discord embed titles are limited to 256 chars (truncate if over)
        if len(play_embed_title) > 256:
            logger.warning(f"Embed title truncated from {len(play_embed_title)} to 256 chars")
            play_embed_title = play_embed_title[:253] + "..."
        play_embed_desc = t("play_added_single_desc") if num_added == 1 else t("play_added_playlist_desc", count=num_added)
        add_embed = discord.Embed(title=play_embed_title, description=play_embed_desc, color=discord.Color.green())
        if all_tracks and all_tracks[0].thumbnail: add_embed.set_thumbnail(url=all_tracks[0].thumbnail)
        await self.nowplaying_message[guild_id].edit(embed=add_embed)

        # Set reference track and index for autoplay (always based on the last added track)
        if all_tracks: self.reference_track[guild_id] = all_tracks[-1]; self.autoplay_index[guild_id] = 2

        # Start playback immediately if nothing is playing
        if not self.is_playing.get(guild_id, False) and not voice_client.is_playing() and not voice_client.is_paused():
            await self.play_next(guild_id)
        else: # If something is already playing, just update the UI (reflect queue changes)
            await self.update_nowplaying_ui(interaction.guild) # pass the guild object
        
        logger.debug(f"[/play Guild {guild_id}] ====== Command END ======")

    async def prefetch_autoplay_track(self, guild_id):
        if not self.autoplay.get(guild_id, True) or not self.reference_track.get(guild_id):
            logger.info(f"[Prefetch Guild {guild_id}] Skipped - autoplay off or no reference track")
            return
        if self.prefetch_lock.locked() or self.prefetched_track.get(guild_id) is not None:
            logger.info(f"[Prefetch Guild {guild_id}] Skipped - locked or already prefetched")
            return

        async with self.prefetch_lock:
            if self.prefetched_track.get(guild_id) is not None: return
            ref_track = self.reference_track[guild_id]

            # Enhanced video ID extraction (uses helper function)
            video_id = None

            # Primary: Use track.video_url (already validated in __init__)
            if ref_track.video_url:
                video_id = YTDLSource.extract_video_id_from_url(ref_track.video_url)
                if video_id:
                    logger.info(f"[Prefetch Guild {guild_id}] ✅ Extracted from video_url: {video_id}")

            # Fallback 1: Try data['id'] with strict validation
            if not video_id:
                raw_id = ref_track.data.get('id', '')
                if raw_id and len(raw_id) == 11 and all(c.isalnum() or c in '-_' for c in raw_id):
                    video_id = raw_id
                    logger.info(f"[Prefetch Guild {guild_id}] ✅ Valid ID from data['id']: {video_id}")
                elif raw_id:
                    logger.warning(f"[Prefetch Guild {guild_id}] ⚠️ Invalid data['id']: {raw_id[:50]}...")

            # Fallback 2: Try alternative data fields
            if not video_id:
                for field in ['display_id', 'video_id', 'watch_id']:
                    alt_id = ref_track.data.get(field, '')
                    if alt_id and len(alt_id) == 11 and all(c.isalnum() or c in '-_' for c in alt_id):
                        video_id = alt_id
                        logger.info(f"[Prefetch Guild {guild_id}] ✅ Valid ID from data['{field}']: {video_id}")
                        break

            if not video_id:
                logger.error(f"[Prefetch Guild {guild_id}] ❌ No valid video ID found in reference track")
                return

            logger.info(f"[Prefetch Guild {guild_id}] Starting prefetch with video_id: {video_id}")

            mix_url = YTDLSource.get_youtube_mix_link(video_id)
            idx = self.autoplay_index.get(guild_id, 2)

            # Duplicate prevention: try up to AUTOPLAY_MAX_RETRIES times to find a track not in history
            for retry in range(self.AUTOPLAY_MAX_RETRIES):
                try:
                    current_idx = idx + retry
                    fetched_list = await YTDLSource.from_mix_url(mix_url, loop=self.bot.loop, stream=True, playliststart=current_idx)

                    if not fetched_list:
                        logger.warning(f"[Prefetch Guild {guild_id}] ⚠️ Empty result from mix URL (index {current_idx})")
                        continue

                    fetched_track = fetched_list[0]
                    fetched_video_id = self._get_video_id_from_track(fetched_track)

                    # Check whether it is in history
                    if fetched_video_id and self._is_in_history(guild_id, fetched_video_id):
                        logger.info(f"[Prefetch Guild {guild_id}] 🔁 Duplicate found: {fetched_track.title} (id: {fetched_video_id}), trying next...")
                        continue  # try next index

                    # Not a duplicate - use it
                    self.prefetched_track[guild_id] = fetched_track
                    self.prefetched_track[guild_id].data['autoplay'] = True
                    # Update index (for the next prefetch)
                    self.autoplay_index[guild_id] = current_idx + 1
                    logger.info(f"[Prefetch Guild {guild_id}] ✅ Success: {fetched_track.title} (index {current_idx}, retry {retry})")
                    return  # done

                except Exception as e:
                    logger.error(f"[Prefetch Guild {guild_id}] ❌ Error at index {idx + retry}: {type(e).__name__}: {redact_input(e)}")

            # All retries failed
            logger.warning(f"[Prefetch Guild {guild_id}] ⚠️ All {self.AUTOPLAY_MAX_RETRIES} retries exhausted (duplicates or errors)")
            self.prefetched_track[guild_id] = None

    async def handle_voice_disconnect(self, guild_id):
        """Handle automatic reconnection when the voice connection drops"""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        voice_client = guild.voice_client

        # Ignore if already connected
        if voice_client and voice_client.is_connected():
            return

        # Clean up stale voice_client
        if voice_client:
            try:
                await voice_client.disconnect(force=True)
            except Exception:
                pass

        # Attempt reconnect if a track was playing
        if self.current.get(guild_id) or not self.queue[guild_id].empty():
            logger.warning(f"[Guild {guild_id}] Voice disconnected, attempting to reconnect...")

            for channel in guild.voice_channels:
                non_bot_members = [m for m in channel.members if not m.bot]
                if len(non_bot_members) > 0:
                    try:
                        await channel.connect()
                        logger.info(f"[Guild {guild_id}] Successfully reconnected to {channel.name}")

                        # Resume playback
                        if self.current.get(guild_id):
                            await self.play_next(guild_id)
                        return
                    except Exception as e:
                        logger.error(f"[Guild {guild_id}] Failed to reconnect: {e}")

            logger.warning(f"[Guild {guild_id}] No suitable voice channel found for reconnection")
            await self.reset_state(guild_id)
    
    async def play_next_after_song(self, error, guild_id):
        # Set when the track is given up on, so the fall-through below knows
        # whether to tell the channel the stream died or simply failed.
        recovery_outcome = None
        failed_title = t("track_unknown")
        if error:
            logger.error(f"[Guild {guild_id}] Playback error: {error}")
            failed_track = self.current.get(guild_id)
            if failed_track is not None:
                failed_title = getattr(failed_track, "title", failed_title)

            # Attempt recovery on stream errors
            error_str = str(error).lower()
            if any(keyword in error_str for keyword in ['ffmpeg', 'stream', 'broken pipe', 'connection', '403', '404']):
                guild = self.bot.get_guild(guild_id)
                voice_client = guild.voice_client if guild else None
                current_track = self.current.get(guild_id)
                
                if voice_client and current_track:
                    logger.info(f"[Guild {guild_id}] Stream error detected, attempting recovery...")
                    
                    # Limit recovery attempts
                    if not hasattr(current_track, '_recovery_attempts'):
                        current_track._recovery_attempts = 0
                    
                    if current_track._recovery_attempts < self.STREAM_RECOVERY_MAX_ATTEMPTS:
                        current_track._recovery_attempts += 1
                        
                        try:
                            # Try re-extracting the URL
                            if current_track.video_url:
                                logger.info(f"[Recovery] Attempting to re-extract URL for: {current_track.title}")
                                
                                # Re-extract for YouTube URLs
                                if 'youtube.com' in current_track.video_url or 'youtu.be' in current_track.video_url:
                                    new_tracks = await YTDLSource.from_url(
                                        current_track.video_url, 
                                        loop=self.bot.loop, 
                                        stream=True, 
                                        playlist_items_limit=1
                                    )
                                    
                                    if new_tracks and len(new_tracks) > 0:
                                        new_track = new_tracks[0]
                                        new_track._recovery_attempts = current_track._recovery_attempts
                                        self.current[guild_id] = new_track
                                        
                                        # Reset bitrate
                                        optimal_bitrate = bitrate_manager.get_optimal_bitrate(
                                            guild, 
                                            voice_client.channel if voice_client else None
                                        )
                                        self.current[guild_id].data['bitrate'] = optimal_bitrate
                                        
                                        # Start playback
                                        voice_client.play(
                                            self.current[guild_id], 
                                            after=lambda e: self.bot.loop.create_task(self.play_next_after_song(e, guild_id))
                                        )
                                        
                                        logger.info(f"[Guild {guild_id}] Stream recovery successful (attempt {current_track._recovery_attempts})")
                                        return
                        except Exception as recovery_error:
                            logger.error(f"[Guild {guild_id}] Recovery failed: {recovery_error}")
                            await self.notifier.notify_ops_exception(
                                kind="recovery_failed",
                                title=t("notify_ops_playback_title"),
                                context=t("notify_ops_playback_body", guild_id=guild_id, title=failed_title),
                                error=recovery_error,
                            )
                    else:
                        logger.warning(f"[Guild {guild_id}] Max recovery attempts reached for: {current_track.title}")
                        recovery_outcome = "exhausted"

        if error:
            # The track is being abandoned: every path that could have saved it
            # already returned. Tell the channel, because from the listener's
            # side the music simply stopped.
            self._consecutive_failures[guild_id] = self._consecutive_failures.get(guild_id, 0) + 1
            if recovery_outcome == "exhausted":
                await self.notify_user(
                    guild_id, "stream_exhausted", "notify_stream_exhausted",
                    title=failed_title, attempts=self.STREAM_RECOVERY_MAX_ATTEMPTS,
                )
            else:
                await self.notify_user(
                    guild_id, "track_failed", "notify_track_failed", title=failed_title,
                )
        else:
            self._consecutive_failures[guild_id] = 0

        logger.info(f"[AfterSong Guild {guild_id}] Song finished, checking voice connection...")

        # Check voice connection state and attempt reconnect
        guild = self.bot.get_guild(guild_id)
        voice_client = guild.voice_client if guild else None

        if not voice_client or not voice_client.is_connected():
            logger.warning(f"[AfterSong Guild {guild_id}] Voice disconnected after song")
            # Reconnection is handled in on_voice_state_update → handle_voice_disconnect
            self.is_playing[guild_id] = False
            self.current[guild_id] = None
            return

        logger.info(f"[AfterSong Guild {guild_id}] Voice still connected")

        self.is_playing[guild_id] = False
        self.current[guild_id] = None
        await self.play_next(guild_id)

    @measure_performance("play_next")
    async def play_next(self, guild_id):
        logger.info(f"[PlayNext Guild {guild_id}] === play_next() called ===")

        guild = self.bot.get_guild(guild_id); voice_client = guild.voice_client if guild else None

        logger.info(f"[PlayNext Guild {guild_id}] Voice client: {voice_client is not None}, Connected: {voice_client.is_connected() if voice_client else False}")

        if not voice_client or not voice_client.is_connected():
            logger.warning(f"[PlayNext Guild {guild_id}] ❌ Voice not connected - exiting play_next")
            if guild : await self.update_nowplaying_ui(guild) # clean up UI when the bot has left
            return

        if self.is_playing.get(guild_id, False):
            logger.info(f"[PlayNext Guild {guild_id}] ⚠️ Already playing - skipping")
            return # prevent duplicate execution

        logger.info(f"[PlayNext Guild {guild_id}] Queue empty: {self.queue[guild_id].empty()}, Autoplay: {self.autoplay.get(guild_id, True)}, Reference track: {self.reference_track.get(guild_id) is not None}")

        next_track_to_play = None
        is_autoplay_track = False # whether the upcoming track came from autoplay

        if not self.queue[guild_id].empty():
            next_track_to_play = await self.queue[guild_id].get()
            is_autoplay_track = next_track_to_play.data.get('autoplay', False) # marked when enqueued
        elif self.autoplay.get(guild_id, True): # queue empty and autoplay enabled
            logger.info(f"[PlayNext Guild {guild_id}] 🔄 Attempting autoplay...")
            if self.prefetched_track.get(guild_id):
                next_track_to_play = self.prefetched_track.pop(guild_id)
                is_autoplay_track = True # already marked
                logger.info(f"[PlayNext Guild {guild_id}] ✅ Using prefetched track: {next_track_to_play.title}")
            elif self.reference_track.get(guild_id):
                logger.info(f"[PlayNext Guild {guild_id}] 🎵 Fetching autoplay track from Mix URL...")
                ref_track = self.reference_track[guild_id]
                logger.info(f"[PlayNext Guild {guild_id}] Reference track URL: {redact_input(ref_track.video_url)}")

                # Extract video ID from various sources with validation (uses helper function)
                video_id = None

                # Primary: Use track.video_url (already validated in __init__)
                if ref_track.video_url:
                    video_id = YTDLSource.extract_video_id_from_url(ref_track.video_url)
                    if video_id:
                        logger.info(f"[PlayNext Guild {guild_id}] ✅ Extracted from video_url: {video_id}")

                # Fallback 1: Try data['id'] with strict validation
                if not video_id:
                    raw_id = ref_track.data.get('id', '')
                    if raw_id and len(raw_id) == 11 and all(c.isalnum() or c in '-_' for c in raw_id):
                        video_id = raw_id
                        logger.info(f"[PlayNext Guild {guild_id}] ✅ Valid ID from data['id']: {video_id}")
                    elif raw_id:
                        logger.warning(f"[PlayNext Guild {guild_id}] ⚠️ Invalid data['id']: {raw_id[:50]}...")

                # Fallback 2: Try alternative data fields
                if not video_id:
                    for field in ['display_id', 'video_id', 'watch_id']:
                        alt_id = ref_track.data.get(field, '')
                        if alt_id and len(alt_id) == 11 and all(c.isalnum() or c in '-_' for c in alt_id):
                            video_id = alt_id
                            logger.info(f"[PlayNext Guild {guild_id}] ✅ Valid ID from data['{field}']: {video_id}")
                            break

                # Final validation and proceed
                if video_id and len(video_id) == 11:
                    logger.info(f"[PlayNext Guild {guild_id}] ✅ Final validated video ID: {video_id}")
                    mix_url = YTDLSource.get_youtube_mix_link(video_id)
                    current_autoplay_idx = self.autoplay_index.get(guild_id, 2)
                    logger.info(f"[PlayNext Guild {guild_id}] Mix URL: {redact_input(mix_url)}, Index: {current_autoplay_idx}")

                    # Duplicate prevention: try up to AUTOPLAY_MAX_RETRIES times
                    for retry in range(self.AUTOPLAY_MAX_RETRIES):
                        try:
                            fetch_idx = current_autoplay_idx + retry
                            fetched_list = await YTDLSource.from_mix_url(mix_url, loop=self.bot.loop, stream=True, playliststart=fetch_idx)
                            if fetched_list:
                                fetched_track = fetched_list[0]
                                fetched_video_id = self._get_video_id_from_track(fetched_track)

                                # Check whether it is in history
                                if fetched_video_id and self._is_in_history(guild_id, fetched_video_id):
                                    logger.info(f"[PlayNext Guild {guild_id}] 🔁 Duplicate found: {fetched_track.title}, trying next...")
                                    continue  # try next index

                                next_track_to_play = fetched_track
                                next_track_to_play.data['autoplay'] = True
                                is_autoplay_track = True
                                self.autoplay_index[guild_id] = fetch_idx + 1  # update to next index
                                logger.info(f"[PlayNext Guild {guild_id}] ✅ Autoplay track fetched: {next_track_to_play.title} (index {fetch_idx}, retry {retry})")
                                break  # success
                            else:
                                logger.warning(f"[PlayNext Guild {guild_id}] ⚠️ Mix URL returned empty list at index {fetch_idx}")
                        except Exception as e:
                            logger.error(f"[PlayNext Guild {guild_id}] ❌ Autoplay error at index {fetch_idx}: {type(e).__name__}: {redact_input(e)}")
                    else:
                        logger.warning(f"[PlayNext Guild {guild_id}] ⚠️ All autoplay retries exhausted")
                else:
                    logger.warning(f"[PlayNext Guild {guild_id}] ⚠️ Could not extract valid video ID (got: {video_id if video_id else 'None'})")
        
        if next_track_to_play:
            self.current[guild_id] = next_track_to_play
            self.is_playing[guild_id] = True

            # Add to playback history (duplicate prevention)
            track_video_id = self._get_video_id_from_track(next_track_to_play)
            if track_video_id:
                self._add_to_history(guild_id, track_video_id)

            if not is_autoplay_track: # playing a user-added track
                self.reference_track[guild_id] = next_track_to_play # new autoplay reference
                logger.info(f"[PlayNext Guild {guild_id}] 🎯 Set new reference track: {next_track_to_play.title}, URL: {redact_input(next_track_to_play.video_url)}")
                self.autoplay_index[guild_id] = 2 # reset index
                self.prefetched_track[guild_id] = None # invalidate previous prefetch
                self.autoplay_count[guild_id] = 0  # reset autoplay count
            else: # playing an autoplay track (prefetched or direct)
                # Increment autoplay count
                self.autoplay_count[guild_id] = self.autoplay_count.get(guild_id, 0) + 1
                autoplay_count = self.autoplay_count[guild_id]

                # Periodically refresh the reference track (to get fresh recommendations)
                if self._should_refresh_reference(guild_id):
                    self.reference_track[guild_id] = next_track_to_play  # current track becomes the new reference
                    self.autoplay_index[guild_id] = 2  # reset index
                    self.prefetched_track[guild_id] = None  # invalidate prefetch
                    logger.info(f"[PlayNext Guild {guild_id}] 🔄 Reference track refreshed after {autoplay_count} autoplay tracks: {next_track_to_play.title}")

            # Determine and apply bitrate
            optimal_bitrate = bitrate_manager.get_optimal_bitrate(guild, voice_client.channel if voice_client else None)

            # Store bitrate info on the current track
            self.current[guild_id].data['bitrate'] = optimal_bitrate
            
            logger.info(f"[Guild {guild_id}] Now playing: {self.current[guild_id].title} {'(autoplay)' if is_autoplay_track else ''} at {optimal_bitrate}kbps")
            voice_client.play(self.current[guild_id], after=lambda e: self.bot.loop.create_task(self.play_next_after_song(e, guild_id)))
            await self.update_nowplaying_ui(guild)
            
            # Start voice connection monitoring (cancel any previous task)
            if guild_id in self._voice_monitor_tasks:
                self._voice_monitor_tasks[guild_id].cancel()
            self._voice_monitor_tasks[guild_id] = asyncio.create_task(
                stream_recovery.monitor_voice_connection(voice_client, guild_id)
            )
            
            # Prefetch the next autoplay track (attempted whenever autoplay is enabled, regardless of the current track's origin)
            if self.autoplay.get(guild_id, True) and self.reference_track.get(guild_id):
                 asyncio.create_task(self.prefetch_autoplay_track(guild_id))
        else:
            logger.warning(f"[PlayNext Guild {guild_id}] ❌ No track to play (queue empty, autoplay failed or disabled)")
            self.current[guild_id] = None; self.is_playing[guild_id] = False
            if self.autoplay.get(guild_id, True) and self.reference_track.get(guild_id):
                 logger.info(f"[PlayNext Guild {guild_id}] Autoplay was enabled but no track found - playback stopped")
                 # Whether to disable autoplay after failure (to avoid infinite loops) is a policy decision
                 pass # keep it on for now; the UI will show the 'stopped' state
            await self.update_nowplaying_ui(guild)

    async def create_nowplaying_embed(self, guild: discord.Guild):
        guild_id = guild.id; current_track = self.current.get(guild_id); voice_client = guild.voice_client
        if not voice_client or not voice_client.is_connected() or not current_track:
            return discord.Embed(title=t("np_stopped_title"), description=t("np_stopped_desc"), color=discord.Color.dark_gray()).set_footer(text=f"{self.bot.user.name}")
        
        queue_list = self._get_queue_list(guild_id); queue_display = []
        if queue_list:
            for i, track_in_q in enumerate(queue_list[:5]): queue_display.append(f"{i+1}. {track_in_q.title[:50]}{'...' if len(track_in_q.title)>50 else ''}") # limit title length
            if len(queue_list) > 5: queue_display.append(t("queue_overflow", count=len(queue_list) - 5))
        else: queue_display.append(t("np_queue_empty"))

        is_autoplaying_now = current_track.data.get("autoplay", False)
        title_prefix = t("np_autoplay_prefix") if is_autoplaying_now else t("np_playing_prefix")
        embed_color = discord.Color.purple() if is_autoplaying_now else discord.Color.blue()
        
        embed_title = f"{title_prefix}: {current_track.title}"
        if len(embed_title) > 256: embed_title = embed_title[:253] + "..." # limit embed title length

        embed = discord.Embed(title=embed_title, url=current_track.video_url, color=embed_color)
        if current_track.thumbnail: embed.set_thumbnail(url=current_track.thumbnail)
        
        duration_str = str(datetime.timedelta(seconds=int(current_track.duration))) if current_track.duration else "N/A"
        embed.add_field(name=t("np_field_duration"), value=f"`{duration_str}`", inline=True)
        uploader_name = current_track.uploader or 'N/A'
        if len(uploader_name) > 20: uploader_name = uploader_name[:17] + "..." # limit uploader name length
        embed.add_field(name=t("np_field_uploader"), value=f"`{uploader_name}`", inline=True)
        
        autoplay_setting_text = t("np_autoplay_on") if self.autoplay.get(guild_id, True) else t("np_autoplay_off")
        embed.add_field(name=t("np_field_autoplay"), value=autoplay_setting_text, inline=True)
        
        queue_section_title = "─" * 10 + t("np_queue_header") + "─" * 10
        embed.add_field(name=queue_section_title, value="\n".join(queue_display) or t("common_none"), inline=False)
        
        vol_val = 50 # default; try to fetch the actual volume
        if voice_client and voice_client.source and hasattr(voice_client.source, 'volume'):
            vol_val = int(voice_client.source.volume * 100)
        
        # Show current bitrate
        current_bitrate = current_track.data.get('bitrate', 128) if current_track else 128
        
        footer_text = t("np_footer", channel=voice_client.channel.name, volume=vol_val, bitrate=current_bitrate)
        embed.set_footer(text=footer_text, icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None)
        return embed

    @app_commands.command(name="nowplaying", description=t("np_desc"))
    async def nowplaying(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        embed = await self.create_nowplaying_embed(interaction.guild)
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    @app_commands.command(name="performance", description=t("perf_desc"))
    async def performance(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        metrics = performance_monitor.get_metrics()
        
        embed = discord.Embed(
            title=t("perf_title"),
            description=t("perf_description"),
            color=discord.Color.green()
        )
        
        if not metrics:
            embed.add_field(name=t("perf_no_data_name"), value=t("perf_no_data_value"), inline=False)
        else:
            for operation, stats in metrics.items():
                embed.add_field(
                    name=operation,
                    value=t(
                        "perf_stats",
                        average=f"{stats['average']:.3f}",
                        min=f"{stats['min']:.3f}",
                        max=f"{stats['max']:.3f}",
                        count=stats['count']
                    ),
                    inline=True
                )
        
        # Add Opus status
        opus_status = t("perf_opus_loaded") if discord.opus.is_loaded() else t("perf_opus_not_loaded")
        embed.add_field(name=t("perf_opus_field"), value=opus_status, inline=False)
        
        embed.set_footer(text=f"{self.bot.user.name} Performance Monitor")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    @app_commands.command(name="bitrate", description=t("bitrate_desc"))
    @app_commands.describe(value=t("bitrate_describe_value"))
    @app_commands.choices(value=[
        app_commands.Choice(name=t("bitrate_choice_64"), value=64),
        app_commands.Choice(name=t("bitrate_choice_96"), value=96),
        app_commands.Choice(name=t("bitrate_choice_128"), value=128),
        app_commands.Choice(name=t("bitrate_choice_256"), value=256),
        app_commands.Choice(name=t("bitrate_choice_384"), value=384)
    ])
    async def bitrate_command(self, interaction: discord.Interaction, value: int):
        await interaction.response.defer(ephemeral=True)
        
        guild = interaction.guild
        if not guild:
            await interaction.followup.send(t("common_guild_only"), ephemeral=True)
            return
        
        # Set bitrate
        success = bitrate_manager.set_custom_bitrate(guild.id, value)
        
        if success:
            voice_client = guild.voice_client
            voice_channel = voice_client.channel if voice_client else None
            status = bitrate_manager.get_status_string(guild, voice_channel)
            
            embed = discord.Embed(
                title=t("bitrate_set_title"),
                description=t("bitrate_set_desc", value=value),
                color=discord.Color.green()
            )
            embed.add_field(name=t("bitrate_field_status"), value=status, inline=False)
            embed.add_field(
                name=t("bitrate_field_note"),
                value=t("bitrate_note_value"),
                inline=False
            )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(t("bitrate_invalid"), ephemeral=True)
    
    @app_commands.command(name="bitrate-auto", description=t("bitrate_auto_desc"))
    async def bitrate_auto_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        guild = interaction.guild
        if not guild:
            await interaction.followup.send(t("common_guild_only"), ephemeral=True)
            return
        
        # Remove custom bitrate setting (use channel max)
        bitrate_manager.clear_custom_bitrate(guild.id)
        
        voice_client = guild.voice_client
        voice_channel = voice_client.channel if voice_client else None
        optimal_bitrate = bitrate_manager.get_optimal_bitrate(guild, voice_channel)
        status = bitrate_manager.get_status_string(guild, voice_channel)
        
        embed = discord.Embed(
            title=t("bitrate_auto_title"),
            description=t("bitrate_auto_embed_desc"),
            color=discord.Color.blue()
        )
        embed.add_field(name=t("bitrate_field_status"), value=status, inline=False)
        embed.add_field(
            name=t("bitrate_field_server"),
            value=t("bitrate_server_info", boost_level=guild.premium_tier, bitrate=optimal_bitrate),
            inline=False
        )
        
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="skip", description=t("skip_desc"))
    async def skip(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False) # skips are announced publicly
        guild_id = interaction.guild.id
        voice_client = interaction.guild.voice_client
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            current_title = self.current.get(guild_id).title if self.current.get(guild_id) else t("track_current")
            await interaction.followup.send(embed=discord.Embed(description=t("skip_announce", user=interaction.user.mention, title=current_title), color=discord.Color.light_grey()))
            voice_client.stop() # the after callback (play_next_after_song) runs automatically
        else:
            # use followup after defer
            await interaction.followup.send(t("skip_nothing"), ephemeral=True)


    @app_commands.command(name="volume", description=t("volume_desc"))
    @app_commands.describe(value=t("volume_describe_value"))
    async def volume(self, interaction: discord.Interaction, value: app_commands.Range[int, 0, 100]):
        await interaction.response.defer(ephemeral=True)
        voice_client = interaction.guild.voice_client
        if not voice_client or not hasattr(voice_client, 'source') or not voice_client.source:
            await interaction.followup.send(t("volume_no_track"), ephemeral=True)
            return
        
        new_volume = value / 100.0
        voice_client.source.volume = new_volume
        
        await interaction.followup.send(t("volume_set", value=value), ephemeral=True)
        await self.update_nowplaying_ui(interaction.guild)


    @app_commands.command(name="stop", description=t("stop_desc"))
    async def stop(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False) # public response
        guild_id = interaction.guild.id
        voice_client = interaction.guild.voice_client

        if voice_client and voice_client.is_connected():
            stop_embed = discord.Embed(
                title=t("stop_title"),
                description=t("stop_embed_desc", user=interaction.user.mention),
                color=discord.Color.dark_red()
            )
            # Handle nowplaying message (delete or edit)
            if guild_id in self.nowplaying_message and self.nowplaying_message[guild_id]:
                try:
                    await self.nowplaying_message[guild_id].edit(embed=stop_embed, view=None) # view=None removes the buttons
                    # use delete() if the message should not remain
                    # await self.nowplaying_message[guild_id].delete()
                    # del self.nowplaying_message[guild_id]
                except (discord.NotFound, discord.Forbidden, discord.HTTPException): pass
            
            await self.reset_state(guild_id) # clear the queue, reset state (nowplaying message ID is removed here too)
            self._intentional_disconnect[guild_id] = True
            await voice_client.disconnect() # leave the voice channel

            await interaction.followup.send(embed=stop_embed) # first response after defer, so use followup
        else:
            await interaction.followup.send(t("stop_not_connected"), ephemeral=True)


    @app_commands.command(name="pause", description=t("pause_desc"))
    async def pause(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_playing():
            voice_client.pause(); await interaction.followup.send(t("pause_done"), ephemeral=True)
            await self.update_nowplaying_ui(interaction.guild)
        else: await interaction.followup.send(t("pause_nothing"), ephemeral=True)

    @app_commands.command(name="resume", description=t("resume_desc"))
    async def resume(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_paused():
            voice_client.resume(); await interaction.followup.send(t("resume_done"), ephemeral=True)
            await self.update_nowplaying_ui(interaction.guild)
        else: await interaction.followup.send(t("resume_nothing"), ephemeral=True)

    @app_commands.command(name="queue", description=t("queue_desc"))
    async def queue_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        if guild_id not in self.queue or self.queue[guild_id].empty():
            await interaction.followup.send(t("queue_empty"), ephemeral=True); return
        queue_list = self._get_queue_list(guild_id)
        embed = discord.Embed(title=t("queue_title"), color=discord.Color.blurple())
        current_track_title = self.current.get(guild_id).title if self.current.get(guild_id) else t("common_none")
        embed.add_field(name=t("queue_now_playing_field"), value=current_track_title[:1000], inline=False) # length limit
        desc_parts = [f"`{i+1}.` {track.title[:80]}{'...' if len(track.title)>80 else ''}" for i, track in enumerate(queue_list[:10])]
        if len(queue_list) > 10: desc_parts.append(t("queue_overflow", count=len(queue_list) - 10))
        embed.description = "\n".join(desc_parts) if desc_parts else t("common_none")
        embed.set_footer(text=t("queue_footer", count=len(queue_list)))
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="remove", description=t("remove_desc"))
    @app_commands.describe(index=t("remove_describe_index"))
    async def remove(self, interaction: discord.Interaction, index: int):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        if guild_id not in self.queue or self.queue[guild_id].empty():
            await interaction.followup.send(t("queue_empty"), ephemeral=True); return
        if index <= 0: await interaction.followup.send(t("remove_invalid_index"), ephemeral=True); return
        temp_q = self._get_queue_list(guild_id)
        if 0 < index <= len(temp_q):
            removed = temp_q.pop(index - 1); new_async_q = asyncio.Queue()
            for item in temp_q: await new_async_q.put(item)
            self.queue[guild_id] = new_async_q
            await interaction.followup.send(t("remove_done", title=removed.title), ephemeral=True)
            await self.update_nowplaying_ui(interaction.guild)
        else: await interaction.followup.send(t("remove_out_of_range", max=len(temp_q)), ephemeral=True)
    
    @app_commands.command(name="help", description=t("help_desc"))
    async def help_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        embed = discord.Embed(
            title=t("help_title"),
            description=t("help_description"),
            color=discord.Color.blue()
        )
        
        commands_info = [
            (t("help_play_usage"), t("help_play_desc")),
            (t("help_join_usage"), t("help_join_desc")),
            (t("help_skip_usage"), t("help_skip_desc")),
            (t("help_pause_usage"), t("help_pause_desc")),
            (t("help_resume_usage"), t("help_resume_desc")),
            (t("help_stop_usage"), t("help_stop_desc")),
            (t("help_volume_usage"), t("help_volume_desc")),
            (t("help_bitrate_usage"), t("help_bitrate_desc")),
            (t("help_bitrate_auto_usage"), t("help_bitrate_auto_desc")),
            (t("help_nowplaying_usage"), t("help_nowplaying_desc")),
            (t("help_queue_usage"), t("help_queue_desc")),
            (t("help_remove_usage"), t("help_remove_desc")),
            (t("help_autoplay_usage"), t("help_autoplay_desc")),
            (t("help_performance_usage"), t("help_performance_desc")),
            (t("help_help_usage"), t("help_help_desc"))
        ]
        
        for cmd, desc in commands_info:
            embed.add_field(name=cmd, value=desc, inline=False)
        
        embed.set_footer(
            text=t("help_footer", bot_name=self.bot.user.name),
            icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
        )
        
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="autoplay", description=t("autoplay_desc"))
    @app_commands.describe(state=t("autoplay_describe_state"))
    async def autoplay_toggle(self, interaction: discord.Interaction, state: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id; state_lower = state.lower() # renamed variable
        msg = ""
        if state_lower == "on": self.autoplay[guild_id] = True; msg = t("autoplay_enabled")
        elif state_lower == "off": self.autoplay[guild_id] = False; self.prefetched_track[guild_id] = None; msg = t("autoplay_disabled")
        else: msg = t("autoplay_usage"); await interaction.followup.send(msg, ephemeral=True); return
        
        await interaction.followup.send(msg, ephemeral=True)
        if state_lower == "on" and not self.is_playing.get(guild_id) and self.queue[guild_id].empty():
             # If nothing is playing and the queue is empty, try fetching the next track right after enabling autoplay
            if self.reference_track.get(guild_id): # needs a reference track
                asyncio.create_task(self.play_next(guild_id)) # play_next handles prefetch or direct fetch
            else: # no reference track; just update the UI
                await self.update_nowplaying_ui(interaction.guild)
        else: # Otherwise (already playing, queue has tracks, or autoplay turned off) update the UI
            await self.update_nowplaying_ui(interaction.guild)

    @app_commands.command(name="cache-info", description=t("cache_info_desc"))
    async def cache_info(self, interaction: discord.Interaction):
        stats = audio_cache_manager.get_cache_stats()
        embed = discord.Embed(
            title=t("cache_title"),
            color=discord.Color.blue()
        )
        embed.add_field(name=t("cache_field_tracks"), value=t("cache_track_count", count=stats['total_files']), inline=True)
        embed.add_field(name=t("cache_field_size"), value=f"{stats['total_size_mb']}MB", inline=True)
        embed.add_field(name=t("cache_field_downloading"), value=t("cache_track_count", count=stats['currently_downloading']), inline=True)
        if stats['downloading_ids']:
            embed.add_field(
                name=t("cache_field_downloading_ids"),
                value=", ".join(stats['downloading_ids'][:5]),
                inline=False
            )
        embed.set_footer(text=t("cache_footer", path=stats['cache_dir']))
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user.name} (ID: {bot.user.id})")
    logger.info(f"Opus loaded: {discord.opus.is_loaded()}")
    logger.info("------")
    activity = discord.Activity(type=discord.ActivityType.listening, name=t("presence_activity"))
    await bot.change_presence(status=discord.Status.online, activity=activity)
    try:
        # Docker uses this per-container marker for bounded deployment
        # readiness. /tmp starts fresh for every recreated container.
        Path("/tmp/musicbot-ready").touch()
        music_cog = bot.get_cog("Music")
        if music_cog:
            for guild in bot.guilds:
                await music_cog.reset_state(guild.id)
        synced = await bot.tree.sync()
        logger.info(f"✅ Synced {len(synced)} slash commands!")
    except Exception as e:
        logger.error(f"❌ Error while syncing slash commands: {e}")

@bot.event
async def on_command_error(ctx, error):
    """Handle command errors (including Korean typos)"""
    if isinstance(error, commands.CommandNotFound):
        # Ignore Korean commands and typos
        command_name = ctx.message.content.split()[0] if ctx.message.content else ""
        # Ignore commands containing Korean or short typos
        if any(ord(char) >= 0xAC00 and ord(char) <= 0xD7A3 for char in command_name) or len(command_name) <= 2:
            return  # silently ignore
        # Only log English command errors
        logger.debug(f"Unknown command: {command_name}")
    else:
        # Log other errors
        logger.error(f"Command error: {error}")

@bot.event
async def on_guild_join(guild: discord.Guild):
    logger.info(f"Bot joined a new server: {guild.name} (ID: {guild.id})")
    music_cog = bot.get_cog("Music")
    if music_cog:
        await music_cog.reset_state(guild.id)

@bot.event
async def on_voice_state_update(member, before, after):
    """Handle voice state updates (reconnect logic + auto-disconnect)"""
    music_cog = bot.get_cog("Music")
    if not music_cog:
        return

    # Handle the bot's own voice state changes
    if member.id == bot.user.id:
        # Disconnected from a voice channel
        if before.channel and not after.channel:
            guild_id = before.channel.guild.id

            # Don't reconnect on intentional disconnects (/stop, timer, etc.)
            if music_cog._intentional_disconnect.get(guild_id, False):
                music_cog._intentional_disconnect[guild_id] = False
                if guild_id in music_cog.alone_timer_task:
                    music_cog.alone_timer_task[guild_id].cancel()
                    del music_cog.alone_timer_task[guild_id]
                return

            # Avoid duplicates if a reconnect is already in progress
            if music_cog._reconnecting.get(guild_id, False):
                return

            logger.warning(f"[Guild {guild_id}] Bot was disconnected from voice channel: {before.channel.name}")

            # Cancel any auto-disconnect timer
            if guild_id in music_cog.alone_timer_task:
                music_cog.alone_timer_task[guild_id].cancel()
                del music_cog.alone_timer_task[guild_id]

            # Give discord.py's internal reconnect enough time (30s)
            music_cog._reconnecting[guild_id] = True
            try:
                await asyncio.sleep(30)

                # Check whether the internal reconnect succeeded
                guild = bot.get_guild(guild_id)
                vc = guild.voice_client if guild else None
                if vc and vc.is_connected():
                    logger.info(f"[Guild {guild_id}] Library auto-reconnected, no action needed")
                    return

                # Internal reconnect failed → attempt manual reconnect
                await music_cog.handle_voice_disconnect(guild_id)
            finally:
                music_cog._reconnecting[guild_id] = False
        return

    # Handle regular users' voice state changes (auto-disconnect check)
    # When a user joins or leaves the channel the bot is connected to
    guild_id = member.guild.id
    voice_client = member.guild.voice_client

    if voice_client and voice_client.channel:
        # User left or joined the same channel as the bot
        if (before.channel == voice_client.channel) or (after.channel == voice_client.channel):
            await music_cog.check_alone_and_schedule_disconnect(guild_id)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Slash command error handler"""
    await ErrorHandler.handle_app_command_error(interaction, error)

async def main():
    discord.utils.setup_logging() # enable the discord library's own logging (optional)
    async with bot:
        await bot.add_cog(Music(bot))
        token = os.getenv("DISCORD_TOKEN") or os.getenv("discord_token")
        if not token:
            raise RuntimeError("DISCORD_TOKEN is not configured")
        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
