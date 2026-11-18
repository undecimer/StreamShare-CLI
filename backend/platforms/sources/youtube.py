import re
import logging
import aiohttp
import asyncio
import subprocess
import os
import yt_dlp
from typing import Dict, Any, Optional
from .base import StreamSource
from .orientation import normalize_live_frame_orientation, select_native_stream_url, captured_frame_matches
from .ytdlp_runtime import javascript_runtime_options
from ...core.config import Config
from ...core.config import get_temp_images_dir
from ...core.exceptions import AuthenticationError, PlatformError

logger = logging.getLogger(__name__)

class QuietYTDLPLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass

class YoutubeSource(StreamSource):
    """Source for YouTube Live streams"""

    def __init__(self, config: Config = None, user_settings: Dict[str, Any] = None):
        self.config = config
        self.user_settings = user_settings or {}
        self.api_key = self.user_settings.get('youtube_api_key')
        self.base_url = "https://www.googleapis.com/youtube/v3"
        self._category_cache = {}

    async def authenticate(self) -> None:
        """YouTube source uses API key authentication."""
        if not self.api_key:
            raise AuthenticationError("YouTube API Key is required. Please add it in Settings.")

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

    async def get_stream_info_with_options(self, identifier: str, use_boxart: bool = False, use_keyart: bool = False, use_live_frame: bool = False, live_frame_orientation: str = 'auto') -> Optional[Dict[str, Any]]:
        """Get stream info with specific options like live frame capture, now using yt-dlp for quota-free operation."""
        live_frame_orientation = normalize_live_frame_orientation(live_frame_orientation)
        try:
            # 1. Prepare yt-dlp options
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
                    'logger': QuietYTDLPLogger(),
                    **javascript_runtime_options(),
                }
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        return ydl.extract_info(url, download=False)
                except Exception:
                    return None

            info = await asyncio.to_thread(extract_info)
            if not info:
                return None

            # If yt-dlp returns a playlist/channel structure (with entries) instead of a direct video info dict:
            if info.get('_type') == 'playlist' and info.get('entries'):
                live_entry = None
                for entry in info['entries']:
                    if entry and (entry.get('is_live') or entry.get('live_status') == 'is_live'):
                        live_entry = entry
                        break
                if live_entry and live_entry.get('id'):
                    video_url = f"https://www.youtube.com/watch?v={live_entry['id']}"
                    def extract_video():
                        ydl_opts = {'quiet': True, 'no_warnings': True, 'nocheckcertificate': True, 'skip_download': True, 'logger': QuietYTDLPLogger(), **javascript_runtime_options()}
                        try:
                            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                                return ydl.extract_info(video_url, download=False)
                        except Exception:
                            return None
                    info = await asyncio.to_thread(extract_video)
                else:
                    logger.info(f"YouTube channel {identifier} has no live stream entries.")
                    return None

            if not info or not info.get('is_live'):
                logger.info(f"YouTube channel {identifier} is not live (according to yt-dlp).")
                return None

            video_id = info.get('id')
            hls_url = info.get('url') # This is the m3u8 URL

            # 2. Extract specific metadata from yt-dlp info
            title = info.get('title')
            channel_id = info.get('channel_id')
            viewer_count = info.get('concurrent_view_count', 0)
            
            # 3. Handle Game Name detection
            # yt-dlp sometimes provides 'game', but we also check tags and our InnerTube logic
            game_name = await self._get_game_name_enhanced(info, video_id)

            # 4. Handle thumbnails / Live frame
            thumbnail_url = info.get('thumbnail')
            
            live_url = None
            if use_live_frame:
                capture_url = select_native_stream_url(info, live_frame_orientation)
                if capture_url:
                    live_url = await self._capture_live_frame(video_id, capture_url, live_frame_orientation)
                    if live_url:
                        thumbnail_url = live_url

            return {
                'platform': 'youtube',
                'id': video_id,
                'video_id': video_id,
                'channel_id': channel_id,
                'title': title,
                'game_name': game_name,
                'thumbnail_url': thumbnail_url,
                'url': f"https://www.youtube.com/watch?v={video_id}",
                'embed_url': f"https://www.youtube.com/embed/{video_id}",
                'vod_url': f"https://www.youtube.com/watch?v={video_id}",
                'is_live': True,
                'viewer_count': viewer_count,
                'identifier': identifier,
                'use_live_frame': use_live_frame,
                'live_frame_captured': bool(live_url),
                'live_frame_orientation': live_frame_orientation
            }

        except Exception as e:
            logger.warning(f"yt-dlp extraction failed for {identifier}: {e}. Falling back to API if key is present.")
            if self.api_key:
                return await self._get_stream_info_api_fallback(identifier, use_live_frame)
            raise PlatformError(f"Failed to get YouTube stream info: {str(e)}")

    async def _get_game_name_enhanced(self, info: Dict[str, Any], video_id: str) -> str:
        """Enhanced game name and category detection using YouTube API, InnerTube, tags, and intelligent title parsing."""
        # 1. Official YouTube Data API v3 (if API key is present)
        if self.api_key and video_id:
            api_game = await self._get_game_from_youtube_api(video_id)
            if api_game:
                logger.info(f"Using YouTube Data API game name: {api_game}")
                return api_game

        # 2. InnerTube API / Web Scraper (Game Card, metadata rows, and structured description)
        if video_id:
            innertube_game = await self._get_game_from_innertube(video_id)
            if innertube_game:
                logger.info(f"Using InnerTube game name: {innertube_game}")
                return innertube_game

        # 3. Extract from stream title & description via intelligent pattern matching
        title = info.get('title', '')
        description = info.get('description', '')
        title_game = self._extract_game_from_text(title, description)
        if title_game:
            logger.info(f"Extracted game name from stream title/description: {title_game}")
            return title_game

        # 4. Extract from meaningful tags
        tags = info.get('tags', [])
        if tags:
            ignored_tags = {'live', 'directo', 'stream', 'streaming', 'gaming', 'shorts', 'youtube', 'viral', 'fyp', 'español', 'spanish', 'gameplay'}
            for tag in tags:
                if len(tag) > 2 and tag.lower() not in ignored_tags:
                    final_name = self._capitalize_game_name(tag)
                    logger.info(f"Using tag-based game name: {final_name}")
                    return final_name

        # 5. Fallback to categories (e.g. "Gaming", "Entertainment", "Music")
        categories = info.get('categories', [])
        if categories:
            for cat in categories:
                if cat:
                    return self._capitalize_game_name(cat)

        return "Gaming"

    async def _get_game_from_youtube_api(self, video_id: str) -> Optional[str]:
        """Fetch game metadata and topics from YouTube Data API v3."""
        if not self.api_key or not video_id:
            return None
        try:
            url = f"{self.base_url}/videos?part=snippet,topicDetails&id={video_id}&key={self.api_key}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        items = data.get('items', [])
                        if not items:
                            return None
                        snippet = items[0].get('snippet', {})
                        topic_details = items[0].get('topicDetails', {})
                        
                        # Check topic categories for specific games (Wikipedia URLs)
                        topic_categories = topic_details.get('topicCategories', [])
                        for topic_url in topic_categories:
                            topic_name = topic_url.split('/')[-1].replace('_', ' ')
                            generic_topics = {'Video game culture', 'Action-adventure game', 'Role-playing video game', 'Strategy video game', 'Action game', 'Simulation video game', 'Sports game', 'Casual game', 'Entertainment', 'Society', 'Lifestyle'}
                            if topic_name not in generic_topics and len(topic_name) > 2:
                                return topic_name
                                
                        # Check tags from snippet
                        tags = snippet.get('tags', [])
                        if tags:
                            ignored = {'live', 'directo', 'stream', 'gaming', 'shorts', 'youtube', 'gameplay'}
                            for tag in tags:
                                if tag.lower() not in ignored and len(tag) > 2:
                                    return self._capitalize_game_name(tag)
        except Exception as e:
            logger.warning(f"YouTube Data API error fetching game info: {e}")
        return None

    def _extract_game_from_text(self, title: str, description: str = "") -> Optional[str]:
        """Intelligently extract the game name from the stream title or description text."""
        if not title:
            return None
            
        known_patterns = [
            (r'\bGTA\s*(?:VI|6)\b|\bGRAND\s*THEFT\s*AUTO\s*(?:VI|6)\b', 'Grand Theft Auto VI'),
            (r'\bGTA\s*(?:V|5)\b|\bGRAND\s*THEFT\s*AUTO\s*(?:V|5)\b', 'Grand Theft Auto V'),
            (r'\bGTA\s*(?:IV|4)\b|\bGRAND\s*THEFT\s*AUTO\s*(?:IV|4)\b', 'Grand Theft Auto IV'),
            (r'\bMINECRAFT\b', 'Minecraft'),
            (r'\bVALORANT\b', 'VALORANT'),
            (r'\bFORTNITE\b', 'Fortnite'),
            (r'\bLEAGUE\s*OF\s*LEGENDS\b|\bLOL\b', 'League of Legends'),
            (r'\bWORLD\s*OF\s*WARCRAFT\b|\bWOW\b', 'World of Warcraft'),
            (r'\bCOUNTER[\s-]*STRIKE\s*2\b|\bCS2\b', 'Counter-Strike 2'),
            (r'\bCALL\s*OF\s*DUTY\b|\bWARZONE\b|\bCOD\b', 'Call of Duty: Warzone'),
            (r'\bAPEX\s*LEGENDS\b', 'Apex Legends'),
            (r'\bOVERWATCH\s*2?\b', 'Overwatch 2'),
            (r'\bROCKET\s*LEAGUE\b', 'Rocket League'),
            (r'\bELDEN\s*RING\b', 'Elden Ring'),
            (r'\bCYBERPUNK\s*2077\b', 'Cyberpunk 2077'),
            (r'\bROBLOX\b', 'Roblox'),
            (r'\bGENSHIN\s*IMPACT\b', 'Genshin Impact'),
            (r'\bHONKAI\s*STAR\s*RAIL\b', 'Honkai: Star Rail'),
            (r'\bBRAWL\s*STARS\b', 'Brawl Stars'),
            (r'\bCLASH\s*ROYALE\b', 'Clash Royale'),
            (r'\bEA\s*SPORTS\s*FC\s*\d*\b|\bEA\s*FC\s*\d*\b|\bFIFA\s*\d*\b', 'EA Sports FC 25'),
            (r'\bPOK[EÉ]MON\s*([A-Za-zÁÉÍÓÚáéíóú]+(?:\s*(?:Y|AND|\+)\s*[A-Za-zÁÉÍÓÚáéíóú]+)?)', 'Pokemon \\1'),
            (r'\bSUPER\s*MARIO\s*([A-Za-zÁÉÍÓÚáéíóú0-9]+(?:\s*[A-Za-zÁÉÍÓÚáéíóú0-9]+)?)', 'Super Mario \\1'),
            (r'\bZELDA\s*([A-Za-zÁÉÍÓÚáéíóú0-9]+(?:\s*[A-Za-zÁÉÍÓÚáéíóú0-9]+)?)', 'The Legend of Zelda: \\1'),
            (r'\bDIGIMON\s*([A-Za-zÁÉÍÓÚáéíóú0-9]+(?:\s*[A-Za-zÁÉÍÓÚáéíóú0-9]+)?)', 'Digimon \\1'),
            (r'\bMONSTER\s*HUNTER\s*([A-Za-zÁÉÍÓÚáéíóú0-9]+)?', 'Monster Hunter \\1'),
            (r'\bFINAL\s*FANTASY\s*([IVXLCDM0-9]+)?', 'Final Fantasy \\1'),
            (r'\bRESIDENT\s*EVIL\s*([IVXLCDM0-9]+)?', 'Resident Evil \\1'),
            (r'\bSILENT\s*HILL\s*([IVXLCDM0-9]+)?', 'Silent Hill \\1'),
            (r'\bASSASSIN\'?S\s*CREED\s*([A-Za-zÁÉÍÓÚáéíóú]+)?', 'Assassin\'s Creed \\1'),
            (r'\bRAINBOW\s*SIX\s*SIEGE\b|\bR6\b', 'Rainbow Six Siege'),
            (r'\bDEAD\s*BY\s*DAYLIGHT\b|\bDBD\b', 'Dead by Daylight'),
            (r'\bHELLDIVERS\s*2?\b', 'Helldivers 2'),
            (r'\bPALWORLD\b', 'Palworld'),
            (r'\bBALDUR\'?S\s*GATE\s*3?\b', 'Baldur\'s Gate 3'),
            (r'\bPEAK\b', 'PEAK')
        ]
        
        for pattern, game_name in known_patterns:
            m = re.search(pattern, title, flags=re.IGNORECASE)
            if m:
                if '\\1' in game_name:
                    sub = m.group(1).strip() if m.lastindex and m.group(1) else ''
                    return self._capitalize_game_name(game_name.replace('\\1', sub).strip(': '))
                return game_name
                
        # Check hashtags in title and description
        hashtags = re.findall(r'#([a-zA-Z0-9_]{3,})', f"{title} {description}")
        ignored_hashes = {'live', 'directo', 'stream', 'streaming', 'gaming', 'shorts', 'youtube', 'viral', 'fyp', 'español', 'spanish', 'gameplay', 'nuevo', 'video'}
        for h in hashtags:
            if h.lower() not in ignored_hashes:
                return self._capitalize_game_name(h)
                
        # Delimiter segments (e.g. "🔴 DIRECTO | THE LAST OF US | DÍA 2")
        parts = re.split(r'[|\-—–•/\[\]:]', title)
        for part in parts:
            cleaned = part.strip()
            cleaned = re.sub(r'^[🔴🟢\s]*(?:LIVE|DIRECTO|EN VIVO|STREAM|STREAMING|SUB|SUBS|CAMINO A|GRIND TO)\b', '', cleaned, flags=re.IGNORECASE).strip()
            if len(cleaned) >= 4 and not re.search(r'\b(?:HABLEMOS|CHARLA|NOTICIAS|NEWS|LEAKS|UPDATE|NUEVO|NUEVA|CON AMIGOS|CON SUBS|GAMEPLAY)\b', cleaned, flags=re.IGNORECASE):
                return self._capitalize_game_name(cleaned)
                
        return None

    async def _get_stream_info_api_fallback(self, identifier: str, use_live_frame: bool = False) -> Optional[Dict[str, Any]]:
        """Minimal fallback for legacy API info if yt-dlp fails completely."""
        logger.warning("API Fallback triggered. This is mostly deprecated in favor of yt-dlp.")
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
                        
                        # Search for richMetadataRenderer / metadataRowRenderer in response
                        def extract_game(obj):
                            if isinstance(obj, dict):
                                if 'richMetadataRenderer' in obj:
                                    r = obj['richMetadataRenderer']
                                    if r.get('style') in ['RICH_METADATA_RENDERER_STYLE_BOX_ART', 'RICH_METADATA_RENDERER_STYLE_TOPIC']:
                                        t = r.get('title', {}).get('simpleText') or ''.join([x.get('text', '') for x in r.get('title', {}).get('runs', [])])
                                        if t: return t
                                if 'metadataRowRenderer' in obj:
                                    m = obj['metadataRowRenderer']
                                    t = m.get('title', {}).get('simpleText') or ''.join([x.get('text', '') for x in m.get('title', {}).get('runs', [])])
                                    if t and t.lower() in ['game', 'juego', 'video game', 'videojuego']:
                                        for c in m.get('contents', []):
                                            ct = c.get('simpleText') or ''.join([x.get('text', '') for x in c.get('runs', [])])
                                            if ct: return ct
                                for v in obj.values():
                                    res = extract_game(v)
                                    if res: return res
                            elif isinstance(obj, list):
                                for item in obj:
                                    res = extract_game(item)
                                    if res: return res
                            return None
                            
                        game_title = extract_game(data)
                        if game_title:
                            logger.info(f"Successfully extracted game title from InnerTube: {game_title}")
                            return game_title
        except Exception as e:
            logger.warning(f"Failed to fetch game title from InnerTube: {e}")
        
        return None

    async def _get_innertube_key(self, video_id: str) -> Optional[str]:
        """Extract the INNERTUBE_API_KEY from the YouTube watch page."""
        # Use a small cache to avoid re-fetching the page every time
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
        # Log the channel ID we're checking
        identifier = stream_info.get('channel_id') or stream_info.get('identifier')
        logger.debug(f"Checking YouTube stream status for identifier: {identifier}")
        
        return await self.get_stream_info_with_options(
            identifier,
            use_live_frame=stream_info.get('use_live_frame', False),
            live_frame_orientation=stream_info.get('live_frame_orientation', 'auto')
        )

    async def _capture_live_frame(self, video_id: str, hls_url: str, orientation: str = 'auto') -> Optional[str]:
        """Capture a single frame from the HLS URL using FFmpeg."""
        try:
            output_dir = get_temp_images_dir()
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
                if not captured_frame_matches(output_path, orientation):
                    logger.warning(f"Captured live frame for {video_id} does not match requested orientation '{orientation}'")
                    try:
                        os.remove(output_path)
                    except OSError:
                        pass
                    return None
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

        # Video URLs or 11-char video IDs (e.g. watch?v=... or youtu.be/...)
        video_match = re.search(r'(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})', identifier)
        if video_match:
            return video_match.group(1)

        # Handles: @username, youtube.com/@username
        handle_match = re.search(r'(?:https?://)?(?:www\.)?youtube\.com/(@[a-zA-Z0-9._-]+)', identifier)
        if handle_match:
            return handle_match.group(1)

        if identifier.startswith('@'):
            return identifier

        # Channel IDs: youtube.com/channel/UC...
        id_match = re.search(r'(?:https?://)?(?:www\.)?youtube\.com/channel/(UC[a-zA-Z0-9_-]+)', identifier)
        if id_match:
            return id_match.group(1)

        if identifier.startswith('UC') and len(identifier) >= 20:
            return identifier

        # Custom/user URLs: youtube.com/c/name or youtube.com/user/name
        user_match = re.search(r'(?:https?://)?(?:www\.)?youtube\.com/(?:c|user)/([a-zA-Z0-9._-]+)', identifier)
        if user_match:
            return f"@{user_match.group(1)}"

        # Plain username / handle typed without @ (e.g. "SourceSpy91")
        if not identifier.startswith('http') and '/' not in identifier and not identifier.startswith('UC'):
            return f"@{identifier}"

        return None

    async def get_latest_vod(self, identifier: str) -> Optional[str]:
        """Fetch/return the VOD URL for a YouTube stream."""
        if identifier and ('watch?v=' in identifier or re.match(r'^[a-zA-Z0-9_-]{11}$', identifier)):
            if identifier.startswith('http'):
                return identifier
            return f"https://www.youtube.com/watch?v={identifier}"
        return None

