import logging
import asyncio
from typing import List, Optional, Union
import yt_dlp

from .url_detector import URLDetector, SourceType
from .spotify_resolver import SpotifyResolver
from ..utils.ytdl_manager import YTDLManager
from ..utils.redaction import redact_input

logger = logging.getLogger(__name__)

class SourceResolver:
    """Unified resolver that converts various sources to YouTube URLs"""
    
    def __init__(self, spotify_client_id: Optional[str] = None, 
                 spotify_client_secret: Optional[str] = None):
        self.spotify_resolver = SpotifyResolver(spotify_client_id, spotify_client_secret)
        self.ytdl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'skip_download': True,
        }
    
    async def resolve_to_youtube(self, url_or_query: str, loop=None) -> List[str]:
        """Convert a URL or search term to a list of YouTube URLs

        Returns:
            List of YouTube URLs (or search queries)
        """
        loop = loop or asyncio.get_running_loop()
        
        # Detect URL type
        source_info = URLDetector.get_source_info(url_or_query)
        source_type = source_info['source_type']
        
        logger.info(f"Detected source type: {source_type.value} for: {redact_input(url_or_query)}")
        
        # Return YouTube URLs as-is
        if source_type == SourceType.YOUTUBE:
            return [url_or_query]
        
        # Handle Spotify URLs
        elif source_type == SourceType.SPOTIFY:
            return await self._resolve_spotify(source_info, loop)
        
        # Handle SoundCloud URLs
        elif source_type == SourceType.SOUNDCLOUD:
            return await self._resolve_soundcloud(url_or_query, loop)
        
        # Handle Apple Music URLs
        elif source_type == SourceType.APPLE_MUSIC:
            return await self._resolve_apple_music(source_info, loop)
        
        # Return direct URLs as-is
        elif source_type == SourceType.DIRECT_URL:
            return [url_or_query]
        
        # Convert search queries to YouTube searches
        elif source_type == SourceType.SEARCH_QUERY:
            return [f"ytsearch1:{url_or_query}"]
        
        # Unknown type
        else:
            logger.warning(f"Unknown source type for: {redact_input(url_or_query)}")
            # Fall back to a YouTube search
            return [f"ytsearch1:{url_or_query}"]
    
    async def _resolve_spotify(self, source_info: dict, loop) -> List[str]:
        """Convert a Spotify URL to YouTube search queries"""
        spotify_id = source_info.get('id')
        content_type = source_info.get('content_type', 'track')
        
        if not spotify_id:
            logger.error("Failed to extract Spotify ID")
            return []
        
        if not self.spotify_resolver.is_available():
            logger.warning("Spotify resolver not available. Using fallback search.")
            # Fallback: use the URL itself as a search query
            return [f"ytsearch1:spotify {content_type} {spotify_id}"]
        
        search_queries = []
        
        try:
            if content_type == 'track':
                query = await loop.run_in_executor(
                    None, self.spotify_resolver.resolve_track, spotify_id
                )
                if query:
                    search_queries = [f"ytsearch1:{query}"]
            
            elif content_type == 'album':
                queries = await loop.run_in_executor(
                    None, self.spotify_resolver.resolve_album, spotify_id
                )
                search_queries = [f"ytsearch1:{q}" for q in queries]
            
            elif content_type == 'playlist':
                queries = await loop.run_in_executor(
                    None, self.spotify_resolver.resolve_playlist, spotify_id
                )
                search_queries = [f"ytsearch1:{q}" for q in queries]
            
            elif content_type == 'artist':
                queries = await loop.run_in_executor(
                    None, self.spotify_resolver.resolve_artist_top_tracks, spotify_id
                )
                search_queries = [f"ytsearch1:{q}" for q in queries]
            
        except Exception as e:
            logger.error(f"Error resolving Spotify {content_type}: {redact_input(e)}")
        
        return search_queries
    
    async def _resolve_soundcloud(self, url: str, loop) -> List[str]:
        """Handle a SoundCloud URL"""
        try:
            # Extract SoundCloud metadata via yt-dlp (managed context manager prevents leaks)
            ytdl_manager = YTDLManager.get_instance()
            with ytdl_manager.get_ytdl(self.ytdl_opts) as ytdl:
                data = await loop.run_in_executor(
                    None, lambda: ytdl.extract_info(url, download=False)
                )
            
            if not data:
                return []
            
            # Extract track info
            if data.get('_type') == 'playlist':
                # Playlist
                search_queries = []
                for entry in data.get('entries', [])[:20]:  # up to 20 tracks
                    if entry:
                        title = entry.get('title', '')
                        uploader = entry.get('uploader', '')
                        if title:
                            query = f"{uploader} - {title}" if uploader else title
                            search_queries.append(f"ytsearch1:{query}")
                return search_queries
            else:
                # Single track
                title = data.get('title', '')
                uploader = data.get('uploader', '')
                if title:
                    query = f"{uploader} - {title}" if uploader else title
                    return [f"ytsearch1:{query}"]
            
        except Exception as e:
            logger.error(f"Error resolving SoundCloud URL: {redact_input(e)}")
        
        return []
    
    async def _resolve_apple_music(self, source_info: dict, loop) -> List[str]:
        """Convert an Apple Music URL to YouTube search queries"""
        # Apple Music API auth is complex, so use a simple search instead
        apple_id = source_info.get('id')
        
        if not apple_id:
            return []
        
        # Fallback: use the Apple Music ID as a search query
        logger.info("Apple Music resolver not implemented. Using fallback search.")
        return [f"ytsearch1:apple music {apple_id}"]
    
    def get_search_query_from_metadata(self, metadata: dict) -> str:
        """Build a YouTube search query from metadata"""
        artist = metadata.get('artist', '')
        title = metadata.get('title', '')
        
        if artist and title:
            return f"{artist} - {title}"
        elif title:
            return title
        else:
            return ""

# Global instance
source_resolver = SourceResolver()
