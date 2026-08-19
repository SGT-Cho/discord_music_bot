import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import logging
from typing import Optional, List, Dict
import os

logger = logging.getLogger(__name__)

class SpotifyResolver:
    """Convert Spotify URLs to YouTube search queries"""
    
    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):
        self.client_id = client_id or os.getenv('SPOTIFY_CLIENT_ID')
        self.client_secret = client_secret or os.getenv('SPOTIFY_CLIENT_SECRET')
        self.sp = None
        
        if self.client_id and self.client_secret:
            try:
                auth_manager = SpotifyClientCredentials(
                    client_id=self.client_id,
                    client_secret=self.client_secret
                )
                self.sp = spotipy.Spotify(auth_manager=auth_manager)
                logger.info("Spotify client initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize Spotify client: {e}")
        else:
            logger.info("Spotify credentials not configured. Spotify URL resolution will use fallback search.")
    
    def is_available(self) -> bool:
        """Whether the Spotify client is available"""
        return self.sp is not None
    
    def resolve_track(self, track_id: str) -> Optional[str]:
        """Convert a Spotify track ID to a YouTube search query"""
        if not self.is_available():
            return None
        
        try:
            track = self.sp.track(track_id)
            if not track:
                return None
            
            # Extract artist names
            artists = [artist['name'] for artist in track['artists']]
            artist_name = ', '.join(artists)
            
            # Track name
            track_name = track['name']
            
            # Build the YouTube search query
            search_query = f"{artist_name} - {track_name}"
            
            logger.info(f"Resolved Spotify track {track_id} to: {search_query}")
            return search_query
            
        except Exception as e:
            logger.error(f"Failed to resolve Spotify track {track_id}: {e}")
            return None
    
    def resolve_album(self, album_id: str) -> List[str]:
        """Convert a Spotify album ID to a list of YouTube search queries"""
        if not self.is_available():
            return []
        
        try:
            album = self.sp.album(album_id)
            if not album:
                return []
            
            search_queries = []
            
            # Fetch all tracks in the album
            tracks = album['tracks']['items']
            for track in tracks:
                # Per-track artists (including featured artists)
                artists = [artist['name'] for artist in track['artists']]
                artist_name = ', '.join(artists)
                track_name = track['name']
                
                search_query = f"{artist_name} - {track_name}"
                search_queries.append(search_query)
            
            logger.info(f"Resolved Spotify album {album_id} to {len(search_queries)} tracks")
            return search_queries
            
        except Exception as e:
            logger.error(f"Failed to resolve Spotify album {album_id}: {e}")
            return []
    
    def resolve_playlist(self, playlist_id: str, limit: int = 50) -> List[str]:
        """Convert a Spotify playlist ID to a list of YouTube search queries"""
        if not self.is_available():
            return []
        
        try:
            # Fetch playlist tracks
            results = self.sp.playlist_tracks(playlist_id, limit=limit)
            if not results:
                return []
            
            search_queries = []
            
            for item in results['items']:
                track = item.get('track')
                if not track or track.get('type') != 'track':
                    continue
                
                # Artist names
                artists = [artist['name'] for artist in track['artists']]
                artist_name = ', '.join(artists)
                track_name = track['name']
                
                search_query = f"{artist_name} - {track_name}"
                search_queries.append(search_query)
            
            logger.info(f"Resolved Spotify playlist {playlist_id} to {len(search_queries)} tracks")
            return search_queries
            
        except Exception as e:
            logger.error(f"Failed to resolve Spotify playlist {playlist_id}: {e}")
            return []
    
    def resolve_artist_top_tracks(self, artist_id: str, country: str = 'US') -> List[str]:
        """Convert a Spotify artist's top tracks to YouTube search queries"""
        if not self.is_available():
            return []
        
        try:
            # Artist info
            artist = self.sp.artist(artist_id)
            if not artist:
                return []
            
            artist_name = artist['name']
            
            # Fetch top tracks
            top_tracks = self.sp.artist_top_tracks(artist_id, country=country)
            if not top_tracks:
                return []
            
            search_queries = []
            
            for track in top_tracks['tracks']:
                track_name = track['name']
                search_query = f"{artist_name} - {track_name}"
                search_queries.append(search_query)
            
            logger.info(f"Resolved Spotify artist {artist_id} to {len(search_queries)} top tracks")
            return search_queries[:10]  # top 10 tracks only
            
        except Exception as e:
            logger.error(f"Failed to resolve Spotify artist {artist_id}: {e}")
            return []
    
    def get_track_metadata(self, track_id: str) -> Optional[Dict]:
        """Fetch metadata for a Spotify track"""
        if not self.is_available():
            return None
        
        try:
            track = self.sp.track(track_id)
            if not track:
                return None
            
            # Album image
            album_images = track['album'].get('images', [])
            thumbnail = album_images[0]['url'] if album_images else None
            
            metadata = {
                'title': track['name'],
                'artists': [artist['name'] for artist in track['artists']],
                'album': track['album']['name'],
                'duration_ms': track['duration_ms'],
                'thumbnail': thumbnail,
                'spotify_url': track['external_urls']['spotify'],
                'preview_url': track.get('preview_url')
            }
            
            return metadata
            
        except Exception as e:
            logger.error(f"Failed to get Spotify track metadata {track_id}: {e}")
            return None