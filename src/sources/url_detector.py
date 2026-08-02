import re
from typing import Optional, Tuple
from enum import Enum

class SourceType(Enum):
    """Supported source types"""
    YOUTUBE = "youtube"
    SPOTIFY = "spotify"
    SOUNDCLOUD = "soundcloud"
    APPLE_MUSIC = "apple_music"
    DIRECT_URL = "direct_url"
    SEARCH_QUERY = "search_query"
    UNKNOWN = "unknown"

class URLDetector:
    """URL type detection and analysis"""

    # URL pattern definitions
    PATTERNS = {
        SourceType.YOUTUBE: [
            r'(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/playlist\?list=)[\w-]+',
            r'(?:https?://)?(?:www\.)?youtube\.com/shorts/[\w-]+',
            r'(?:https?://)?music\.youtube\.com/(?:watch\?v=|playlist\?list=)[\w-]+'
        ],
        SourceType.SPOTIFY: [
            r'(?:https?://)?(?:open\.)?spotify\.com/(?:track|album|playlist|artist)/[\w-]+',
            r'spotify:(?:track|album|playlist|artist):[\w-]+'
        ],
        SourceType.SOUNDCLOUD: [
            r'(?:https?://)?(?:www\.)?soundcloud\.com/[\w-]+/[\w-]+',
            r'(?:https?://)?(?:www\.)?soundcloud\.com/[\w-]+/sets/[\w-]+'
        ],
        SourceType.APPLE_MUSIC: [
            r'(?:https?://)?music\.apple\.com/[a-z]{2}/(?:album|playlist)/[\w-]+/[\w-]+',
            r'(?:https?://)?music\.apple\.com/[a-z]{2}/(?:album|playlist)/[\w-]+/pl\.[\w-]+'
        ],
        SourceType.DIRECT_URL: [
            r'https?://.*\.(mp3|mp4|m4a|webm|wav|flac|ogg|opus)(?:\?.*)?$'
        ]
    }
    
    @classmethod
    def detect_source(cls, url_or_query: str) -> Tuple[SourceType, Optional[str]]:
        """Detect the source type of a URL or search term

        Returns:
            (source type, extracted ID or None)
        """
        url_or_query = url_or_query.strip()
        
        # Match URL patterns
        for source_type, patterns in cls.PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, url_or_query, re.IGNORECASE)
                if match:
                    # Try to extract an ID
                    extracted_id = cls._extract_id(url_or_query, source_type)
                    return source_type, extracted_id
        
        # HTTP URL that doesn't match any pattern
        if url_or_query.startswith(('http://', 'https://')):
            return SourceType.UNKNOWN, None
        
        # Treat everything else as a search query
        return SourceType.SEARCH_QUERY, None
    
    @classmethod
    def _extract_id(cls, url: str, source_type: SourceType) -> Optional[str]:
        """Extract an ID from a URL"""
        
        if source_type == SourceType.YOUTUBE:
            # YouTube video ID
            video_match = re.search(r'(?:v=|youtu\.be/|shorts/)([\w-]+)', url)
            if video_match:
                return video_match.group(1)
            
            # YouTube playlist ID
            playlist_match = re.search(r'list=([\w-]+)', url)
            if playlist_match:
                return playlist_match.group(1)
        
        elif source_type == SourceType.SPOTIFY:
            # Spotify ID (track/album/playlist/artist)
            spotify_match = re.search(r'(?:track|album|playlist|artist)[/:](\w+)', url)
            if spotify_match:
                return spotify_match.group(1)
        
        elif source_type == SourceType.SOUNDCLOUD:
            # SoundCloud needs the full path
            return url
        
        elif source_type == SourceType.APPLE_MUSIC:
            # Apple Music ID
            apple_match = re.search(r'/(?:album|playlist)/[^/]+/(?:pl\.)?([\w-]+)', url)
            if apple_match:
                return apple_match.group(1)
        
        return None
    
    @classmethod
    def get_source_info(cls, url: str) -> dict:
        """Extract URL information"""
        source_type, extracted_id = cls.detect_source(url)
        
        info = {
            'source_type': source_type,
            'source_name': source_type.value,
            'id': extracted_id,
            'original_url': url
        }
        
        # Source-specific extra info
        if source_type == SourceType.SPOTIFY and extracted_id:
            # Distinguish Spotify URL content types
            if '/track/' in url or ':track:' in url:
                info['content_type'] = 'track'
            elif '/album/' in url or ':album:' in url:
                info['content_type'] = 'album'
            elif '/playlist/' in url or ':playlist:' in url:
                info['content_type'] = 'playlist'
            elif '/artist/' in url or ':artist:' in url:
                info['content_type'] = 'artist'
        
        elif source_type == SourceType.YOUTUBE and extracted_id:
            if 'list=' in url:
                info['content_type'] = 'playlist'
            else:
                info['content_type'] = 'video'
        
        return info