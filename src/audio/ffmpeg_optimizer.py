import discord
import logging
import shlex
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def _build_ffmpeg_header_args(http_headers: Optional[Dict[str, str]]) -> str:
    """Convert yt-dlp format-level http_headers into an ffmpeg before_options fragment.

    - 'User-Agent' is split out and passed via ffmpeg's dedicated -user_agent option.
      (The googlevideo CDN only returns 200 when the UA matches the client UA
       yt-dlp used; otherwise the request goes out with ffmpeg's default
       'Lavf/...' UA and gets 403 Forbidden.)
    - The remaining headers are joined into a single -headers argument as
      'Key: Value\\r\\n' lines.

    Returns an empty string for an empty dict / None so caller options are untouched.
    """
    if not http_headers:
        return ""

    parts = []
    ua = http_headers.get("User-Agent") or http_headers.get("user-agent")
    if ua:
        parts.append(f"-user_agent {shlex.quote(ua)}")

    others = {
        k: v for k, v in http_headers.items()
        if k.lower() != "user-agent" and v
    }
    if others:
        joined = "".join(f"{k}: {v}\r\n" for k, v in others.items())
        parts.append(f"-headers {shlex.quote(joined)}")

    return " ".join(parts)

class FFmpegOptimizer:
    """FFmpeg pipeline optimization class"""

    # Optimized FFmpeg options
    OPTIMIZED_FFMPEG_OPTIONS = {
        'before_options': (
            '-reconnect 1 '
            '-reconnect_streamed 1 '
            '-reconnect_delay_max 5 '
            '-analyzeduration 0 '  # Shorten analysis time
            '-probesize 32 '       # Minimize probe size
            '-fflags +nobuffer '   # Disable buffering
            '-flags low_delay'     # Low-latency mode
        ),
        'options': '-vn'  # discord.py FFmpegPCMAudio handles -f s16le output, so no codec needs to be specified
    }

    # Per-source optimization options
    SOURCE_SPECIFIC_OPTIONS = {
        'youtube': {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        },
        'spotify': {
            'before_options': '-analyzeduration 0 -probesize 32',
            'options': '-vn'
        },
        'local': {
            'before_options': '',
            'options': '-vn'
        }
    }

    @classmethod
    def get_optimized_options(cls, source_type: str = 'youtube', bitrate_kbps: Optional[int] = None) -> Dict[str, str]:
        """Return optimized FFmpeg options for the given source type

        Args:
            source_type: Source type ('youtube', 'spotify', 'local')
            bitrate_kbps: Bitrate (kbps); defaults are used when None

        Returns:
            Optimized FFmpeg options dictionary
        """
        if source_type in cls.SOURCE_SPECIFIC_OPTIONS:
            options = cls.SOURCE_SPECIFIC_OPTIONS[source_type].copy()
        else:
            options = cls.OPTIMIZED_FFMPEG_OPTIONS.copy()

        # Apply dynamic bitrate
        if bitrate_kbps:
            # Remove any existing bitrate setting and apply the new one
            import re
            options['options'] = re.sub(r'-b:a \d+k', f'-b:a {bitrate_kbps}k', options['options'])
            if '-b:a' not in options['options']:
                options['options'] += f' -b:a {bitrate_kbps}k'

        return options

    @classmethod
    def create_audio_source(cls, source_url: str, *,
                          volume: float = 0.5,
                          options: Optional[Dict[str, str]] = None,
                          source_type: str = 'youtube',
                          bitrate_kbps: Optional[int] = None,
                          http_headers: Optional[Dict[str, str]] = None) -> discord.PCMVolumeTransformer:
        """Create an optimized audio source

        Args:
            source_url: Stream URL
            volume: Initial volume (0.0-1.0)
            options: Custom FFmpeg options (defaults are used if omitted)
            source_type: Source type
            bitrate_kbps: Bitrate (kbps)
            http_headers: HTTP header dict yt-dlp provided with the format.
                YouTube streams (googlevideo CDN) require requests with the
                same UA as the client yt-dlp used to return 200 OK. Without
                it, ffmpeg calls with its default 'Lavf/...' UA and exits
                immediately with 403 (= the 1-second-skip symptom).

        Returns:
            PCMVolumeTransformer instance
        """
        if options is None:
            options = cls.get_optimized_options(source_type, bitrate_kbps)

        # If http_headers is present, prepend -user_agent / -headers to before_options
        if http_headers:
            header_args = _build_ffmpeg_header_args(http_headers)
            if header_args:
                options = dict(options)  # Avoid mutating the caller's dict
                existing_before = options.get('before_options', '') or ''
                options['before_options'] = f"{header_args} {existing_before}".strip()

        try:
            source = discord.FFmpegPCMAudio(source_url, **options)
            return discord.PCMVolumeTransformer(source, volume=volume)
        except Exception as e:
            logger.error(f"Failed to create audio source: {e}")
            raise

    @staticmethod
    def validate_opus_loaded() -> bool:
        """Check whether the Opus library is loaded"""
        if not discord.opus.is_loaded():
            logger.warning("Opus is not loaded, attempting to load...")
            opus_paths = [
                '/opt/homebrew/lib/libopus.0.dylib',  # macOS ARM64 (M1/M2) - Homebrew
                '/opt/homebrew/lib/libopus.dylib',    # macOS (Homebrew)
                '/usr/local/lib/libopus.0.dylib',     # macOS Intel
                '/usr/local/lib/libopus.dylib',       # macOS (alternative)
                '/usr/lib/x86_64-linux-gnu/libopus.so.0',  # Ubuntu/Debian (Docker)
                '/usr/lib/aarch64-linux-gnu/libopus.so.0', # Ubuntu/Debian ARM64
                '/usr/lib/libopus.so.0',               # Other Linux
                'libopus.0.dylib',
                'libopus.dylib',
                'libopus',
                'opus'
            ]

            for path in opus_paths:
                try:
                    discord.opus.load_opus(path)
                    logger.info(f"Successfully loaded Opus from: {path}")
                    return True
                except Exception:
                    continue

            logger.error("Failed to load Opus library from any known path")
            return False

        return True
