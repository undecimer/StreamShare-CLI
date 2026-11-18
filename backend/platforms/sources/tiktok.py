import os
import re
import json
import asyncio
import logging
import subprocess
import aiohttp
import yt_dlp
from typing import Optional, Dict, Any

from TikTokLive import TikTokLiveClient
from TikTokLive.client.errors import UserOfflineError, UserNotFoundError

from ...core.config import Config
from ...core.config import get_temp_images_dir
from ...core.exceptions import PlatformError
from .base import StreamSource
from .orientation import normalize_live_frame_orientation, select_native_stream_url, captured_frame_matches
from .ytdlp_runtime import javascript_runtime_options

logger = logging.getLogger(__name__)

class QuietYTDLPLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass

class TikTokSource(StreamSource):
    """Source for TikTok streams"""

    def __init__(self, config: Config, user_settings: Dict[str, Any] = None):
        self.config = config
        self.user_settings = user_settings or {}
        self.client = None

    def authenticate(self) -> None:
        """Initializes the TikTok client."""
        logger.info("TikTok source ready.")

    def validate_credentials(self) -> bool:
        """Basic credential check for TikTok source"""
        return True

    async def validate_session(self) -> Dict[str, Any]:
        """Validate whether the user's TikTok session cookies are configured and active"""
        session_id = self.user_settings.get('tiktok_session_id') or self.config.get('tiktok_session_id')
        target_idc = self.user_settings.get('tiktok_target_idc') or self.config.get('tiktok_target_idc')
        if not session_id:
            return {"valid": False, "authenticated": False, "message": "No TikTok session ID configured (optional for public streams)"}

        # Actually exercise the session — constructing a client proves nothing.
        # passport/web/account/info returns the account record only when the
        # sessionid is accepted; an expired cookie answers with an error body.
        try:
            cookies = {'sessionid': session_id, 'sessionid_ss': session_id, 'sid_tt': session_id}
            if target_idc:
                cookies['tt-target-idc'] = target_idc
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            async with aiohttp.ClientSession(headers=headers, cookies=cookies) as http:
                async with http.get("https://www.tiktok.com/passport/web/account/info/", timeout=aiohttp.ClientTimeout(total=12)) as resp:
                    body = await resp.json(content_type=None)
            account = body.get('data') or {}
            if account.get('username') or account.get('user_id_str') or account.get('user_id'):
                return {"valid": True, "authenticated": True, "message": "TikTok session cookies configured and active"}
            return {
                "valid": False,
                "authenticated": False,
                "message": "TikTok session cookies are expired or invalid — paste a fresh sessionid. Audience-restricted (+18) lives cannot be detected or captured without a valid session."
            }
        except Exception as e:
            return {"valid": False, "authenticated": False, "message": f"TikTok session validation error: {str(e)}"}

    async def _fetch_public_room_info(self, username: str) -> Optional[Dict[str, Any]]:
        """Fetch the public live-room blob (api-live/user/room).

        This endpoint backs the public /@user/live page, so it exposes status,
        title and cover art even for audience-restricted rooms where the
        authenticated webcast room/info call is denied.
        """
        try:
            session_id = self.user_settings.get('tiktok_session_id') or self.config.get('tiktok_session_id')
            cookies = {}
            if session_id:
                cookies = {'sessionid': session_id, 'sessionid_ss': session_id, 'sid_tt': session_id}
            url = (
                "https://www.tiktok.com/api-live/user/room/"
                "?aid=1988&app_name=tiktok_web&device_platform=web_pc"
                f"&channel=tiktok_web&webcast_language=en&sourceType=54&uniqueId={username}"
            )
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
            async with aiohttp.ClientSession(headers=headers, cookies=cookies) as http:
                async with http.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    body = await resp.json(content_type=None)
            data = body.get('data') or {}
            room = data.get('liveRoom') or {}
            stats = room.get('liveRoomStats') or {}
            return {
                'status': room.get('status'),
                'title': room.get('title') or None,
                'cover_url': room.get('coverUrl') or None,
                'square_cover_url': room.get('squareCoverImg') or None,
                'user_count': stats.get('userCount') or 0,
                'live_sub_only': room.get('liveSubOnly'),
            }
        except Exception as e:
            logger.debug(f"TikTok public room lookup failed for @{username}: {e}")
            return None

    @staticmethod
    def _is_age_restriction_error(exc: Exception) -> bool:
        return 'age restricted' in str(exc).lower() or 'age_restricted' in str(exc).lower()

    async def get_stream_info(self, identifier: str, ultrawide: bool = False, resolution: str = None) -> Optional[Dict[str, Any]]:
        """Standard interface for getting stream info (Satisfies ABC)"""
        return await self.get_stream_info_with_options(identifier)

    async def get_stream_info_with_options(self, username: str, use_boxart: bool = False, use_keyart: bool = False, use_live_frame: bool = False, live_frame_orientation: str = 'auto') -> Optional[Dict[str, Any]]:
        """Gets the stream info for a given TikTok user with options like live frame capture.
        Now uses yt-dlp with session cookies for primary discovery.
        """
        live_frame_orientation = normalize_live_frame_orientation(live_frame_orientation)
        logger.info(f"Checking TikTok live status for @{username} via yt-dlp...")
        
        discovery_hls_url = None
        session_id = self.user_settings.get('tiktok_session_id') or self.config.get('tiktok_session_id')
        target_idc = self.user_settings.get('tiktok_target_idc') or self.config.get('tiktok_target_idc')
        
        try:
            url = f"https://www.tiktok.com/@{username}/live"
            cookie_file = None
            
            # Create a temporary cookie file for yt-dlp to avoid domain scoping issues with headers
            if session_id:
                import tempfile
                fd, cookie_file = tempfile.mkstemp(suffix='.txt', prefix='tiktok_cookies_')
                with os.fdopen(fd, 'w') as f:
                    # Netscape cookie format
                    f.write("# Netscape HTTP Cookie File\n")
                    # Use .tiktok.com for broad scoping
                    domains = [".tiktok.com", ".www.tiktok.com", ".webcast.tiktok.com"]
                    for domain in domains:
                        flag = "TRUE" if domain.startswith(".") else "FALSE"
                        f.write(f"{domain}\t{flag}\t/\tTRUE\t0\tsessionid\t{session_id}\n")
                        f.write(f"{domain}\t{flag}\t/\tTRUE\t0\tsessionid_ss\t{session_id}\n")
                        f.write(f"{domain}\t{flag}\t/\tTRUE\t0\tsid_tt\t{session_id}\n")
                        if target_idc:
                            f.write(f"{domain}\t{flag}\t/\tTRUE\t0\ttt-target-idc\t{target_idc}\n")
            
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
                
                if cookie_file:
                    ydl_opts['cookiefile'] = cookie_file
                
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        return ydl.extract_info(url, download=False)
                except Exception:
                    return None

            try:
                info = await asyncio.to_thread(extract_info)
                if info and info.get('is_live'):
                    logger.info(f"TikTok user @{username} is LIVE (detected via yt-dlp)")
                    discovery_hls_url = select_native_stream_url(info, live_frame_orientation)
            except Exception as e:
                logger.debug(f"yt-dlp primary discovery failed for @{username}: {e}")
            finally:
                if cookie_file and os.path.exists(cookie_file):
                    os.remove(cookie_file)

        except Exception as e:
            logger.warning(f"yt-dlp setup failed for TikTok user @{username}: {e}")

        # 2. Use TikTokLive library for rich metadata and fallback discovery
        # We pass discovery_hls_url so it can be used for frame capture even if metadata fetch fails
        return await self._get_stream_info_tiktok_live_fallback(username, use_live_frame, discovery_hls_url, live_frame_orientation)

    async def _get_stream_info_tiktok_live_fallback(self, username: str, use_live_frame: bool = False, override_hls_url: str = None, live_frame_orientation: str = 'auto') -> Optional[Dict[str, Any]]:
        """Fallback to TikTokLive library / direct web API if yt-dlp discovery fails."""
        web_kwargs = {}
        session_id = self.user_settings.get('tiktok_session_id') or self.config.get('tiktok_session_id')
        target_idc = self.user_settings.get('tiktok_target_idc') or self.config.get('tiktok_target_idc')
        
        if session_id:
            # Handle the security whitelist for session IDs
            whitelist_host = (
                self.user_settings.get('tiktok_sign_host')
                or self.config.get('tiktok_sign_host')
                or os.environ.get('WHITELIST_AUTHENTICATED_SESSION_ID_HOST')
                or 'tiktok.eulerstream.com'
            )
            os.environ['WHITELIST_AUTHENTICATED_SESSION_ID_HOST'] = whitelist_host
            
            httpx_kwargs = web_kwargs.setdefault('httpx_kwargs', {})
            cookies = httpx_kwargs.setdefault('cookies', {})
            cookies['sessionid'] = session_id
            cookies['sessionid_ss'] = session_id
            cookies['sid_tt'] = session_id
            if target_idc:
                cookies['tt-target-idc'] = target_idc

        client = TikTokLiveClient(unique_id=username, web_kwargs=web_kwargs)

        # In v6, setting session enables authenticated WebSocket and bypasses some blocks
        if session_id and target_idc:
            try:
                client.web.set_session(session_id, target_idc)
            except Exception as e:
                logger.warning(f"Failed to set TikTokLive session: {e}")

        try:
            room_info = None
            age_restricted = False
            # The public live-page endpoint still reports status/title/covers for
            # audience-restricted rooms where the authenticated room/info call is
            # denied — fetch it early so it can fill gaps.
            public_room = await self._fetch_public_room_info(username)

            # 1. Try fetching room_info directly via web client API first (most reliable, no WebSocket dependency)
            try:
                room_id = await client.web.fetch_room_id_from_api(username)
                if room_id:
                    room_info = await client.web.fetch_room_info(room_id=str(room_id))
            except Exception as e:
                if self._is_age_restriction_error(e):
                    age_restricted = True
                    logger.warning(
                        f"TikTok room for @{username} is audience-restricted and the configured session "
                        "cookies are missing or expired — restricted room data (playback URLs, full metadata) "
                        "is unavailable. Update the TikTok session in Settings to restore full capture."
                    )
                else:
                    logger.debug(f"Direct TikTokLive room_info fetch failed for @{username}: {e}")

            # 2. If direct fetch failed, fallback to client.connect
            if not room_info:
                try:
                    if override_hls_url or (public_room or {}).get('status') == 2 or await client.is_live():
                        await asyncio.wait_for(client.connect(fetch_room_info=True), timeout=10.0)
                        room_info = client.room_info
                except Exception as e:
                    logger.debug(f"TikTokLive room connection failed: {e}")

            if room_info:
                # TikTok status enum: 2 = LIVE, 4 = FINISHED/OFFLINE
                room_status = room_info.get('status')
                is_active_live = (room_status == 2) or (override_hls_url is not None) or (public_room or {}).get('status') == 2
                if not is_active_live:
                    logger.info(f"TikTok room for @{username} exists but status is {room_status} (offline).")
                    return None

                thumbnail_url = self._extract_thumbnail(room_info)
                if not thumbnail_url and public_room:
                    thumbnail_url = public_room.get('square_cover_url') or public_room.get('cover_url')
                game_name = self._get_room_game_name(room_info)

                info = {
                    'title': room_info.get('title') or (public_room or {}).get('title') or "Live",
                    'thumbnail_url': thumbnail_url,
                    'viewer_count': room_info.get('user_count') or (public_room or {}).get('user_count') or 0,
                    'url': f"https://www.tiktok.com/@{username}/live",
                    'game_name': game_name,
                    'platform': 'tiktok',
                    'identifier': username,
                    'room_id': str(room_info.get('id') or room_info.get('room_id') or '') or None,
                    'use_live_frame': use_live_frame,
                    'live_frame_captured': False,
                    'live_frame_orientation': live_frame_orientation
                }

                if use_live_frame:
                    # Try to extract HLS URL from room_info directly, or use override from yt-dlp discovery
                    candidates = []
                    if override_hls_url:
                        candidates.append(override_hls_url)
                    candidates.extend(self._extract_stream_urls(room_info))

                    seen_candidates = set()
                    for hls_url in candidates:
                        if hls_url in seen_candidates:
                            continue
                        seen_candidates.add(hls_url)
                        live_image_url = await self._capture_live_frame_from_url(username, hls_url, live_frame_orientation)
                        if live_image_url:
                            info['thumbnail_url'] = live_image_url
                            info['live_frame_captured'] = True
                            logger.info(f"Using dynamic frame (via stream URL) for @{username}")
                            break

                    # If direct extraction and override failed, try secondary capture methods
                    if not info.get('thumbnail_url') or 'live_frame' not in info['thumbnail_url']:
                        live_image_url = await self._capture_live_frame(username, live_frame_orientation)
                        if live_image_url:
                            info['thumbnail_url'] = live_image_url
                            info['live_frame_captured'] = True

                return info
            
            # If authenticated room info is unavailable but yt-dlp discovery or
            # the public live-page endpoint confirms the room is live, provide
            # info built from whatever is reachable (public covers/title included).
            public_live = bool(public_room and public_room.get('status') == 2)
            if override_hls_url or public_live:
                info = {
                    'title': (public_room or {}).get('title') or "Live Stream",
                    'thumbnail_url': (public_room or {}).get('square_cover_url') or (public_room or {}).get('cover_url'),
                    'viewer_count': (public_room or {}).get('user_count') or 0,
                    'url': f"https://www.tiktok.com/@{username}/live",
                    'embed_url': f"https://www.tiktok.com/@{username}/live",
                    'vod_url': f"https://www.tiktok.com/@{username}",
                    'is_live': True,
                    'game_name': "Live",
                    'platform': 'tiktok',
                    'identifier': username,
                    'use_live_frame': use_live_frame,
                    'live_frame_captured': False,
                    'live_frame_orientation': live_frame_orientation,
                    'age_restricted': age_restricted
                }

                if use_live_frame:
                    live_image_url = None
                    if override_hls_url:
                        live_image_url = await self._capture_live_frame_from_url(username, override_hls_url, live_frame_orientation)
                    if not live_image_url:
                        live_image_url = await self._capture_live_frame(username, live_frame_orientation)
                    if live_image_url:
                        info['thumbnail_url'] = live_image_url
                        info['live_frame_captured'] = True
                        logger.info(f"Using minimal stream info (discovery/public) for @{username}")
                    elif age_restricted:
                        logger.warning(
                            f"Could not capture a live frame for @{username}: audience-restricted rooms "
                            "with invalid/expired session cookies expose no playback URLs."
                        )

                return info

        except PlatformError:
            raise
        except Exception as e:
            logger.error(f"TikTokLive fallback failed for @{username}: {e}")
        finally:
            if client.connected:
                await client.disconnect()

        return None

    def _extract_thumbnail(self, room_info: Dict[str, Any]) -> Optional[str]:
        """Extracts the best available thumbnail URL from room info."""
        square_cover_img = room_info.get('square_cover_img')
        if square_cover_img and isinstance(square_cover_img, dict) and square_cover_img.get('url_list'):
            return square_cover_img['url_list'][0]
        
        cover_img = room_info.get('cover')
        if cover_img and isinstance(cover_img, dict) and cover_img.get('url_list'):
            return cover_img['url_list'][0]
        
        return None

    def _get_room_game_name(self, room_info: Dict[str, Any]) -> str:
        """Extracts the game name from room info, game tags, or title patterns."""
        # 1. Check official TikTok game_tag
        game_tag = room_info.get('game_tag')
        if game_tag and isinstance(game_tag, list) and len(game_tag) > 0:
            name = game_tag[0].get('show_name')
            if name and name.lower() not in ("live", "gaming", "other"):
                return self._capitalize_game_name(name)
        elif game_tag and isinstance(game_tag, dict):
            name = game_tag.get('show_name')
            if name and name.lower() not in ("live", "gaming", "other"):
                return self._capitalize_game_name(name)
                
        # 2. Check hashtag / challenge info
        hashtag = room_info.get('hashtag') or room_info.get('sub_tag')
        if hashtag and isinstance(hashtag, str) and len(hashtag) > 2:
            if hashtag.lower() not in ("live", "gaming", "stream", "fyp", "tiktok"):
                return self._capitalize_game_name(hashtag)

        # 3. Extract game candidates from stream title
        title = room_info.get('title') or ""
        game_from_title = self._extract_game_from_text(title)
        if game_from_title:
            return game_from_title

        return "Live"

    def _capitalize_game_name(self, name: str) -> str:
        """Helper to ensure proper capitalization of game titles."""
        if not name:
            return name
        if any(c.isupper() for c in name[1:]):
            return name
        return name.title()

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
            (r'\bFREE\s*FIRE\b', 'Garena Free Fire'),
            (r'\bMOBILE\s*LEGENDS\b|\bMLBB\b', 'Mobile Legends: Bang Bang'),
            (r'\bPUBG\b|\bPLAYERUNKNOWN\b', 'PUBG: BATTLEGROUNDS'),
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
        ignored_hashes = {'live', 'directo', 'stream', 'streaming', 'gaming', 'shorts', 'youtube', 'viral', 'fyp', 'español', 'spanish', 'gameplay', 'nuevo', 'video', 'tiktok'}
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

    def _extract_hls_url(self, room_info: Dict[str, Any]) -> Optional[str]:
        """Extracts the best available video stream URL (FLV/RTMP preferred over HLS) from room info."""
        urls = self._extract_stream_urls(room_info)
        return urls[0] if urls else None

    def _extract_stream_urls(self, room_info: Dict[str, Any]) -> list[str]:
        urls = []
        seen = set()

        def add(url):
            if isinstance(url, str) and url and url not in seen:
                seen.add(url)
                urls.append(url)

        def add_map_values(mapping, preferred_keys):
            if isinstance(mapping, dict):
                for key in preferred_keys:
                    add(mapping.get(key))
                for value in mapping.values():
                    add(value)
            elif isinstance(mapping, str):
                add(mapping)

        def add_pull_data(pull_data):
            if not isinstance(pull_data, dict):
                return
            stream_data = pull_data.get('stream_data')
            if isinstance(stream_data, str):
                try:
                    stream_data = json.loads(stream_data)
                except (ValueError, TypeError):
                    return
            if not isinstance(stream_data, dict):
                return
            qualities = stream_data.get('data')
            if not isinstance(qualities, dict):
                return
            for quality in qualities.values():
                if not isinstance(quality, dict):
                    continue
                main = quality.get('main')
                if not isinstance(main, dict):
                    continue
                for field in ('hls', 'flv'):
                    value = main.get(field)
                    if isinstance(value, str):
                        add(value)
                    elif isinstance(value, dict):
                        for item in value.get('url_list') or []:
                            add(item)

        try:
            stream_url = room_info.get('stream_url')
            if not isinstance(stream_url, dict):
                return urls

            add_map_values(stream_url.get('flv_pull_url'), ['FULL_HD1', 'HD1', 'ORIGIN', 'SD1', 'LD1'])
            add(stream_url.get('rtmp_pull_url'))
            add_map_values(stream_url.get('hls_pull_url_map'), ['ORIGIN', 'FULL_HD1', 'HD1', 'SD1'])
            add(stream_url.get('hls_pull_url'))

            sdk_data = stream_url.get('live_core_sdk_data')
            if isinstance(sdk_data, dict):
                add_pull_data(sdk_data.get('pull_data'))

            pull_datas = stream_url.get('pull_datas')
            if isinstance(pull_datas, dict):
                for pull_data in pull_datas.values():
                    add_pull_data(pull_data)
            elif isinstance(pull_datas, list):
                for pull_data in pull_datas:
                    add_pull_data(pull_data)
        except Exception:
            pass

        return urls

    async def _extract_stream_url_from_html(self, username: str) -> Optional[str]:
        """Extract live stream URL (FLV/HLS) directly from TikTok live HTML webpage as fallback."""
        try:
            url = f"https://www.tiktok.com/@{username}/live"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            }
            session_id = self.user_settings.get('tiktok_session_id') or self.config.get('tiktok_session_id')
            target_idc = self.user_settings.get('tiktok_target_idc') or self.config.get('tiktok_target_idc')
            cookies = {}
            if session_id:
                cookies['sessionid'] = session_id
                cookies['sessionid_ss'] = session_id
                cookies['sid_tt'] = session_id
                if target_idc:
                    cookies['tt-target-idc'] = target_idc

            async with aiohttp.ClientSession(headers=headers, cookies=cookies) as session:
                async with session.get(url, allow_redirects=True, timeout=10) as response:
                    if response.status == 200:
                        html = await response.text()
                        flv_urls = re.findall(r'https?[^"\'\s\\]+?\.flv[^"\'\s\\]*', html)
                        if flv_urls:
                            target_url = None
                            for qual in ['_hd5.flv', '_hd.flv', '_uhd560.flv', '_hd560.flv', '_sd5.flv', '_ld5.flv', '_ld.flv']:
                                for f in flv_urls:
                                    clean = f.replace('\\u0026', '&').replace('\\/', '/')
                                    if qual in clean:
                                        target_url = clean
                                        break
                                if target_url:
                                    break
                            if not target_url:
                                target_url = flv_urls[0].replace('\\u0026', '&').replace('\\/', '/')
                            logger.info(f"Extracted stream FLV URL from HTML for @{username}")
                            return target_url
                        
                        m3u8_urls = re.findall(r'https?[^"\'\s\\]+?\.m3u8[^"\'\s\\]*', html)
                        if m3u8_urls:
                            clean_m3u8 = m3u8_urls[0].replace('\\u0026', '&').replace('\\/', '/')
                            logger.info(f"Extracted stream HLS URL from HTML for @{username}")
                            return clean_m3u8
        except Exception as e:
            logger.debug(f"HTML stream extraction failed for @{username}: {e}")
        return None

    async def _capture_live_frame(self, username: str, orientation: str = 'auto') -> Optional[str]:
        """Convenience wrapper for capture from username."""
        try:
            session_id = self.user_settings.get('tiktok_session_id') or self.config.get('tiktok_session_id')
            target_idc = self.user_settings.get('tiktok_target_idc') or self.config.get('tiktok_target_idc')
            
            def extract_hls():
                url = f"https://www.tiktok.com/@{username}/live"
                cookie_file = None
                
                if session_id:
                    import tempfile
                    fd, cookie_file = tempfile.mkstemp(suffix='.txt', prefix='tiktok_cookies_retry_')
                    with os.fdopen(fd, 'w') as f:
                        f.write("# Netscape HTTP Cookie File\n")
                        domains = [".tiktok.com", ".www.tiktok.com", ".webcast.tiktok.com"]
                        for domain in domains:
                            flag = "TRUE" if domain.startswith(".") else "FALSE"
                            f.write(f"{domain}\t{flag}\t/\tTRUE\t0\tsessionid\t{session_id}\n")
                            f.write(f"{domain}\t{flag}\t/\tTRUE\t0\tsessionid_ss\t{session_id}\n")
                            f.write(f"{domain}\t{flag}\t/\tTRUE\t0\tsid_tt\t{session_id}\n")
                            if target_idc:
                                f.write(f"{domain}\t{flag}\t/\tTRUE\t0\ttt-target-idc\t{target_idc}\n")
                                
                ydl_opts = {
                    'quiet': True, 
                    'nocheckcertificate': True,
                    'no_warnings': True,
                    **javascript_runtime_options()
                }
                
                if cookie_file:
                    ydl_opts['cookiefile'] = cookie_file
                    
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=False)
                        return select_native_stream_url(info, orientation)
                finally:
                    if cookie_file and os.path.exists(cookie_file):
                        os.remove(cookie_file)

            hls_url = await asyncio.to_thread(extract_hls)
            if hls_url:
                frame = await self._capture_live_frame_from_url(username, hls_url, orientation)
                if frame:
                    return frame
        except Exception as e:
            logger.debug(f"yt-dlp retry extraction failed for @{username}: {e}")

        # Fallback to direct HTML stream extraction for frame capture
        stream_url = await self._extract_stream_url_from_html(username)
        if stream_url:
            return await self._capture_live_frame_from_url(username, stream_url, orientation)

        return None

    async def _capture_live_frame_from_url(self, username: str, hls_url: str, orientation: str = 'auto') -> Optional[str]:
        """Capture a single frame from a known HLS manifest using FFmpeg."""
        try:
            output_dir = get_temp_images_dir()
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, f"live_frame_tiktok_{username}.jpg")

            cmd = ['ffmpeg', '-y']

            # Audience-restricted rooms gate pull-URL segments behind the web
            # session — yt-dlp's discovery URL carries its own auth params, but
            # URLs recovered via HTML fallback do not. Forward the session
            # cookies so ffmpeg is not denied on restricted streams.
            session_id = self.user_settings.get('tiktok_session_id') or self.config.get('tiktok_session_id')
            if session_id:
                target_idc = self.user_settings.get('tiktok_target_idc') or self.config.get('tiktok_target_idc')
                cookie_hdr = f"sessionid={session_id}; sessionid_ss={session_id}; sid_tt={session_id}"
                if target_idc:
                    cookie_hdr += f"; tt-target-idc={target_idc}"
                cmd += [
                    '-headers', f"Cookie: {cookie_hdr}\r\n",
                    '-user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                ]

            cmd += [
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
            _, stderr = await process.communicate()

            if process.returncode != 0:
                detail = (stderr or b'').decode('utf-8', 'replace').strip().splitlines()
                logger.warning(
                    f"TikTok FFmpeg capture failed for @{username} (exit {process.returncode}): "
                    f"{detail[-1][:200] if detail else 'no stderr'}"
                )
                return None
            if os.path.exists(output_path):
                if not captured_frame_matches(output_path, orientation):
                    logger.warning(f"Captured TikTok frame for @{username} does not match requested orientation '{orientation}'")
                    try:
                        os.remove(output_path)
                    except OSError:
                        pass
                    return None
                logger.info(f"Successfully captured TikTok live frame for @{username}")
                return f"file://{os.path.abspath(output_path)}"
        except Exception as e:
            logger.warning(f"TikTok FFmpeg capture failed for @{username}: {e}")
        return None

    async def get_current_stream_info(self, stream_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Gets the current stream info for a TikTok user to check for changes."""
        identifier = stream_info.get('identifier')
        if not identifier:
            return None
        return await self.get_stream_info_with_options(
            identifier,
            use_live_frame=stream_info.get('use_live_frame', False),
            live_frame_orientation=stream_info.get('live_frame_orientation', 'auto')
        )

    async def is_stream_live(self, username: str) -> bool:
        """Checks if a TikTok user is currently live."""
        # The public live-page endpoint reports room status even for
        # audience-restricted rooms — prefer it when reachable.
        public_room = await self._fetch_public_room_info(username)
        if public_room is not None and public_room.get('status') is not None:
            return public_room.get('status') == 2

        # Preference: use yt-dlp check as it's more reliable for public status
        session_id = self.user_settings.get('tiktok_session_id') or self.config.get('tiktok_session_id')
        target_idc = self.user_settings.get('tiktok_target_idc') or self.config.get('tiktok_target_idc')

        try:
            url = f"https://www.tiktok.com/@{username}/live"
            
            def check():
                cookie_file = None
                if session_id:
                    import tempfile
                    fd, cookie_file = tempfile.mkstemp(suffix='.txt', prefix='tiktok_cookies_check_')
                    with os.fdopen(fd, 'w') as f:
                        f.write("# Netscape HTTP Cookie File\n")
                        domains = [".tiktok.com", ".www.tiktok.com", ".webcast.tiktok.com"]
                        for domain in domains:
                            flag = "TRUE" if domain.startswith(".") else "FALSE"
                            f.write(f"{domain}\t{flag}\t/\tTRUE\t0\tsessionid\t{session_id}\n")
                            f.write(f"{domain}\t{flag}\t/\tTRUE\t0\tsessionid_ss\t{session_id}\n")
                            f.write(f"{domain}\t{flag}\t/\tTRUE\t0\tsid_tt\t{session_id}\n")
                            if target_idc:
                                f.write(f"{domain}\t{flag}\t/\tTRUE\t0\ttt-target-idc\t{target_idc}\n")
                
                ydl_opts = {
                    'quiet': True, 
                    'nocheckcertificate': True, 
                    'extract_flat': True,
                    'no_warnings': True,
                    **javascript_runtime_options()
                }
                
                if cookie_file:
                    ydl_opts['cookiefile'] = cookie_file
                
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        return ydl.extract_info(url, download=False).get('is_live')
                finally:
                    if cookie_file and os.path.exists(cookie_file):
                        os.remove(cookie_file)

            return await asyncio.to_thread(check)
        except Exception:
            # Fallback to lightweight TikTokLive check
            client = TikTokLiveClient(unique_id=username)
            try:
                return await client.is_live()
            except Exception:
                return False

    def get_platform_name(self) -> str:
        return "tiktok"

    def parse_identifier(self, identifier: str) -> Optional[str]:
        """Parses a TikTok username from a URL or string."""
        if not identifier:
            return None

        # Handle full URLs
        match = re.search(
            r"(?:https?://)?(?:www\.)?tiktok\.com/@([a-zA-Z0-9_.]+)", identifier
        )
        if match:
            return match.group(1)

        # Handle usernames with or without '@'
        return identifier.lstrip("@")

    async def get_latest_vod(self, identifier: str) -> Optional[str]:
        """TikTok does not provide public archive VODs on the web."""
        return None


