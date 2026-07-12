import re
import logging
import aiohttp
import asyncio
import subprocess
import os
import yt_dlp
from typing import Dict, Any, Optional
from .base import StreamSource
from ...core.config import Config
from ...core.exceptions import AuthenticationError, PlatformError

logger = logging.getLogger(__name__)

class YoutubeSource(StreamSource):
    """Source for YouTube Live streams"""

    def __init__(self, config: Config = None, user_settings: Dict[str, Any] = None):
        self.config = config
        self.user_settings = user_settings or {}
        self.api_key = self.user_settings.get('youtube_api_key') or os.getenv('YOUTUBE_API_KEY')
        self.base_url = "https://www.googleapis.com/youtube/v3"
        self._category_cache = {}

    async def authenticate(self) -> None:
        """YouTube source uses API key authentication."""
        if not self.api_key:
            raise AuthenticationError("YouTube API Key is required. Please set YOUTUBE_API_KEY in your .env file.")

    async def validate_credentials(self) -> bool:
        """Validate the API key by making a simple request."""
        if not self.api_key:
            return False
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.base_url}/videoCategories?part=id&regionCode=US&key={self.api_key}"
                async with session.get(url) as response:
                    return response.status == 200
        except Exception:
            return False

    async def get_stream_info(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Get stream info for a given identifier (compatibility wrapper)."""
        return await self.get_stream_info_with_options(identifier)

    async def get_stream_info_with_options(self, identifier: str, use_boxart: bool = False, use_keyart: bool = False, use_live_frame: bool = False) -> Optional[Dict[str, Any]]:
        """Get stream info with specific options like live frame capture, now using yt-dlp for quota-free operation."""
        try:
            url = identifier
            if re.match(r'^[a-zA-Z0-9_-]{11}$', identifier):
                url = f"https://www.youtube.com/watch?v={identifier}"
            elif identifier.startswith('@'):
                clean_id = identifier.lstrip('@')
                url = f"https://www.youtube.com/@{clean_id}/live"
            elif identifier.startswith('UC'):
                url = f"https://www.youtube.com/channel/{identifier}/live"
            elif identifier.startswith('http'):
                if ('/channel/' in identifier or '/@' in identifier or '/c/' in identifier or '/user/' in identifier) and not identifier.endswith('/live') and 'watch?v=' not in identifier:
                    url = f"{identifier.rstrip('/')}/live"

            logger.info(f"Extracting stream info for {identifier} (target URL: {url}) via yt-dlp...")
            
            def extract_info():
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'nocheckcertificate': True,
                    'extract_flat': False,
                    'skip_download': True,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)

            info = await asyncio.to_thread(extract_info)
            if not info:
                return None

            if info.get('_type') == 'playlist' and info.get('entries'):
                live_entry = None
                for entry in info['entries']:
                    if entry and (entry.get('is_live') or entry.get('live_status') == 'is_live'):
                        live_entry = entry
                        break
                if live_entry and live_entry.get('id'):
                    video_url = f"https://www.youtube.com/watch?v={live_entry['id']}"
                    def extract_video():
                        ydl_opts = {'quiet': True, 'no_warnings': True, 'nocheckcertificate': True, 'skip_download': True}
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            return ydl.extract_info(video_url, download=False)
                    info = await asyncio.to_thread(extract_video)
                else:
                    logger.info(f"YouTube channel {identifier} has no live stream entries.")
                    return None

            if not info or not info.get('is_live'):
                logger.info(f"YouTube channel {identifier} is not live (according to yt-dlp).")
                return None

            video_id = info.get('id')
            hls_url = info.get('url')

            title = info.get('title')
            channel_id = info.get('channel_id')
            viewer_count = info.get('concurrent_view_count', 0)
            
            game_name = await self._get_game_name_enhanced(info, video_id)
            thumbnail_url = info.get('thumbnail')
            
            if use_live_frame and hls_url:
                live_url = await self._capture_live_frame(video_id, hls_url)
                if live_url:
                    thumbnail_url = live_url

            return {
                'platform': 'youtube',
                'id': video_id,
                'channel_id': channel_id,
                'title': title,
                'game_name': game_name,
                'thumbnail_url': thumbnail_url,
                'url': f"https://www.youtube.com/watch?v={video_id}",
                'viewer_count': viewer_count,
                'identifier': identifier,
                'use_live_frame': use_live_frame
            }

        except Exception as e:
            logger.warning(f"yt-dlp extraction failed for {identifier}: {e}. Falling back to API if key is present.")
            if self.api_key:
                return await self._get_stream_info_api_fallback(identifier, use_live_frame)
            raise PlatformError(f"Failed to get YouTube stream info: {str(e)}")

    async def _get_game_name_enhanced(self, info: Dict[str, Any], video_id: str) -> str:
        """Enhanced game name detection using yt-dlp metadata and InnerTube fallbacks."""
        if video_id:
            innertube_game = await self._get_game_from_innertube(video_id)
            if innertube_game:
                logger.debug(f"Using InnerTube game name: {innertube_game}")
                return innertube_game

        tags = info.get('tags', [])
        if tags:
            for tag in tags[:3]:
                if len(tag) > 3:
                    final_name = self._capitalize_game_name(tag)
                    logger.debug(f"Using tag-based game name: {final_name}")
                    return final_name

        categories = info.get('categories', [])
        if categories:
            cat = categories[0]
            if cat.lower() != "gaming":
                return self._capitalize_game_name(cat)

        return "Live Stream"

    async def _get_stream_info_api_fallback(self, identifier: str, use_live_frame: bool = False) -> Optional[Dict[str, Any]]:
        """Fallback for legacy API info if yt-dlp fails completely."""
        logger.warning("API Fallback triggered.")
        return None

    def _capitalize_game_name(self, name: str) -> str:
        """Helper to ensure proper capitalization of game titles."""
        if not name:
            return name
        if any(c.isupper() for c in name[1:]):
            return name
        return name.title()

    async def _get_game_from_innertube(self, video_id: str) -> Optional[str]:
        """Fetch video metadata using YouTube's internal InnerTube API."""
        try:
            api_key = await self._get_innertube_key(video_id)
            if not api_key:
                return None

            url = f"https://www.youtube.com/youtubei/v1/next?key={api_key}"
            headers = {
                'Content-Type': 'application/json',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            payload = {
                "context": {
                    "client": {
                        "clientName": "WEB",
                        "clientVersion": "2.20240315.00.00"
                    }
                },
                "videoId": video_id
            }

            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        data = await response.json()
                        results = data.get('contents', {}).get('twoColumnWatchNextResults', {})
                        secondary = results.get('secondaryResults', {}).get('secondaryResults', {}).get('results', [])
                        
                        for item in secondary:
                            r_metadata = item.get('richMetadataRenderer', {})
                            if r_metadata and r_metadata.get('style') == 'RICH_METADATA_RENDERER_STYLE_BOX_ART':
                                title = r_metadata.get('title', {}).get('simpleText')
                                if title:
                                    logger.info(f"Successfully extracted game title from InnerTube: {title}")
                                    return title
        except Exception as e:
            logger.warning(f"Failed to fetch game title from InnerTube: {e}")
        return None

    async def _get_innertube_key(self, video_id: str) -> Optional[str]:
        """Extract the INNERTUBE_API_KEY from the YouTube watch page."""
        if hasattr(self, '_innertube_api_key_cache') and self._innertube_api_key_cache:
            return self._innertube_api_key_cache

        url = f"https://www.youtube.com/watch?v={video_id}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        html = await response.text()
                        match = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
                        if match:
                            key = match.group(1)
                            self._innertube_api_key_cache = key
                            return key
        except Exception as e:
            logger.warning(f"Failed to extract InnerTube API key: {e}")
        return None

    async def get_current_stream_info(self, stream_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get current stream info and check for changes."""
        identifier = stream_info.get('channel_id') or stream_info.get('identifier')
        logger.debug(f"Checking YouTube stream status for identifier: {identifier}")
        return await self.get_stream_info_with_options(
            identifier,
            use_live_frame=stream_info.get('use_live_frame', False)
        )

    async def _capture_live_frame(self, video_id: str, hls_url: str) -> Optional[str]:
        """Capture a single frame from the HLS URL using FFmpeg."""
        try:
            output_dir = os.path.join(os.getcwd(), 'generated_images', 'temp')
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, f"live_frame_{video_id}.jpg")
            
            cmd = [
                'ffmpeg', '-y', 
                '-i', hls_url, 
                '-frames:v', '1', 
                '-q:v', '2', 
                output_path
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()
            
            if process.returncode == 0 and os.path.exists(output_path):
                logger.info(f"Successfully captured live frame for {video_id}")
                return f"file://{os.path.abspath(output_path)}"
        except Exception as e:
            logger.warning(f"FFmpeg capture failed: {e}")
        return None

    async def is_stream_live(self, identifier: str) -> bool:
        """Check if a YouTube channel is live."""
        try:
            info = await self.get_stream_info(identifier)
            return info is not None
        except Exception:
            return False

    def get_platform_name(self) -> str:
        return "youtube"

    def parse_identifier(self, identifier: str) -> Optional[str]:
        """Parse YouTube handle, channel ID, or video ID from various formats."""
        if not identifier:
            return None
        identifier = identifier.strip()

        video_match = re.search(r'(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})', identifier)
        if video_match:
            return video_match.group(1)

        handle_match = re.search(r'(?:https?://)?(?:www\.)?youtube\.com/(@[a-zA-Z0-9._-]+)', identifier)
        if handle_match:
            return handle_match.group(1)

        if identifier.startswith('@'):
            return identifier

        id_match = re.search(r'(?:https?://)?(?:www\.)?youtube\.com/channel/(UC[a-zA-Z0-9_-]+)', identifier)
        if id_match:
            return id_match.group(1)

        if identifier.startswith('UC') and len(identifier) >= 20:
            return identifier

        user_match = re.search(r'(?:https?://)?(?:www\.)?youtube\.com/(?:c|user)/([a-zA-Z0-9._-]+)', identifier)
        if user_match:
            return f"@{user_match.group(1)}"

        if not identifier.startswith('http') and '/' not in identifier and not identifier.startswith('UC'):
            return f"@{identifier}"

        return None
