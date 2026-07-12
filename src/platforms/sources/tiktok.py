import os
import re
import asyncio
import logging
import subprocess
import aiohttp
import yt_dlp
from typing import Optional, Dict, Any

from TikTokLive import TikTokLiveClient
from TikTokLive.client.errors import UserOfflineError, UserNotFoundError

from ...core.config import Config
from .base import StreamSource

logger = logging.getLogger(__name__)

class TikTokSource(StreamSource):
    """Source for TikTok streams"""

    def __init__(self, config: Config, user_settings: Dict[str, Any] = None):
        self.config = config
        self.user_settings = user_settings or {}
        self.client = None

    def authenticate(self) -> None:
        """Initializes the TikTok client."""
        logger.info("TikTok source does not require authentication.")

    def validate_credentials(self) -> bool:
        """TikTok source does not require credentials."""
        logger.info("TikTok source does not require credential validation.")
        return True

    async def get_stream_info(self, identifier: str, ultrawide: bool = False, resolution: str = None) -> Optional[Dict[str, Any]]:
        """Standard interface for getting stream info (Satisfies ABC)"""
        return await self.get_stream_info_with_options(identifier)

    async def get_stream_info_with_options(self, username: str, use_boxart: bool = False, use_keyart: bool = False, use_live_frame: bool = False) -> Optional[Dict[str, Any]]:
        """Gets the stream info for a given TikTok user with options like live frame capture."""
        logger.info(f"Checking TikTok live status for @{username} via yt-dlp...")
        
        discovery_hls_url = None
        session_id = self.user_settings.get('tiktok_session_id') or os.getenv('TIKTOK_SESSION_ID')
        target_idc = self.user_settings.get('tiktok_target_idc') or os.getenv('TIKTOK_TARGET_IDC')
        
        try:
            url = f"https://www.tiktok.com/@{username}/live"
            cookie_file = None
            
            if session_id:
                import tempfile
                fd, cookie_file = tempfile.mkstemp(suffix='.txt', prefix='tiktok_cookies_')
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
            
            def extract_info():
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'nocheckcertificate': True,
                    'extract_flat': False,
                    'skip_download': True,
                }
                if cookie_file:
                    ydl_opts['cookiefile'] = cookie_file
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)

            try:
                info = await asyncio.to_thread(extract_info)
                if info and info.get('is_live'):
                    logger.info(f"TikTok user @{username} is LIVE (detected via yt-dlp)")
                    discovery_hls_url = info.get('url')
            except Exception as e:
                logger.debug(f"yt-dlp primary discovery failed for @{username}: {e}")
            finally:
                if cookie_file and os.path.exists(cookie_file):
                    os.remove(cookie_file)

        except Exception as e:
            logger.warning(f"yt-dlp setup failed for TikTok user @{username}: {e}")

        return await self._get_stream_info_tiktok_live_fallback(username, use_live_frame, discovery_hls_url)

    async def _get_stream_info_tiktok_live_fallback(self, username: str, use_live_frame: bool = False, override_hls_url: str = None) -> Optional[Dict[str, Any]]:
        """Fallback to TikTokLive library / direct web API if yt-dlp discovery fails."""
        web_kwargs = {}
        session_id = self.user_settings.get('tiktok_session_id') or os.getenv('TIKTOK_SESSION_ID')
        target_idc = self.user_settings.get('tiktok_target_idc') or os.getenv('TIKTOK_TARGET_IDC')
        
        if session_id:
            whitelist_host = (
                self.user_settings.get('tiktok_sign_host')
                or os.getenv('TIKTOK_SIGN_HOST')
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

        if session_id and target_idc:
            try:
                client.web.set_session(session_id, target_idc)
            except Exception as e:
                logger.warning(f"Failed to set TikTokLive session: {e}")

        try:
            room_info = None
            try:
                room_id = await client.web.fetch_room_id_from_api(username)
                if room_id:
                    room_info = await client.web.fetch_room_info(room_id=str(room_id))
            except Exception as e:
                logger.debug(f"Direct TikTokLive room_info fetch failed for @{username}: {e}")

            if not room_info:
                try:
                    if override_hls_url or await client.is_live():
                        await asyncio.wait_for(client.connect(fetch_room_info=True), timeout=10.0)
                        room_info = client.room_info
                except Exception as e:
                    logger.debug(f"TikTokLive room connection failed: {e}")

            if room_info:
                thumbnail_url = self._extract_thumbnail(room_info)
                game_name = self._get_room_game_name(room_info)

                info = {
                    'title': room_info.get('title', "Live"),
                    'thumbnail_url': thumbnail_url,
                    'viewer_count': room_info.get('user_count', 0),
                    'url': f"https://www.tiktok.com/@{username}/live",
                    'game_name': game_name,
                    'platform': 'tiktok',
                    'identifier': username,
                    'use_live_frame': use_live_frame
                }

                if use_live_frame:
                    hls_url = override_hls_url or self._extract_hls_url(room_info)
                    if hls_url:
                        live_image_url = await self._capture_live_frame_from_url(username, hls_url)
                        if live_image_url:
                            info['thumbnail_url'] = live_image_url
                            logger.info(f"Using dynamic frame (via stream URL) for @{username}")
                    
                    if not info.get('thumbnail_url') or 'live_frame' not in info['thumbnail_url']:
                        live_image_url = await self._capture_live_frame(username)
                        if live_image_url:
                            info['thumbnail_url'] = live_image_url

                return info
            
            if override_hls_url:
                info = {
                    'title': "Live Stream",
                    'thumbnail_url': None,
                    'viewer_count': 0,
                    'url': f"https://www.tiktok.com/@{username}/live",
                    'game_name': "Live",
                    'platform': 'tiktok',
                    'identifier': username,
                    'use_live_frame': use_live_frame
                }
                if use_live_frame:
                    live_image_url = await self._capture_live_frame_from_url(username, override_hls_url)
                    if live_image_url:
                        info['thumbnail_url'] = live_image_url
                        logger.info(f"Using minimal stream info (discovery only) for @{username}")
                return info

        except Exception as e:
            logger.error(f"TikTokLive fallback failed for @{username}: {e}")
        finally:
            if client.connected:
                await client.disconnect()

        return None

    def _extract_thumbnail(self, room_info: Dict[str, Any]) -> Optional[str]:
        square_cover_img = room_info.get('square_cover_img')
        if square_cover_img and isinstance(square_cover_img, dict) and square_cover_img.get('url_list'):
            return square_cover_img['url_list'][0]
        
        cover_img = room_info.get('cover')
        if cover_img and isinstance(cover_img, dict) and cover_img.get('url_list'):
            return cover_img['url_list'][0]
        return None

    def _get_room_game_name(self, room_info: Dict[str, Any]) -> str:
        game_tag = room_info.get('game_tag')
        if game_tag and isinstance(game_tag, list) and len(game_tag) > 0:
            return game_tag[0].get('show_name') or "Live"
        elif game_tag and isinstance(game_tag, dict):
            return game_tag.get('show_name') or "Live"
        return "Live"

    def _extract_hls_url(self, room_info: Dict[str, Any]) -> Optional[str]:
        try:
            stream_url = room_info.get('stream_url')
            if not stream_url:
                return None
            
            flv_url = stream_url.get('flv_pull_url')
            if isinstance(flv_url, dict):
                for key in ['FULL_HD1', 'HD1', 'ORIGIN', 'SD1', 'LD1']:
                    if flv_url.get(key):
                        return flv_url[key]
                val = next(iter(flv_url.values()), None)
                if val:
                    return val
            elif isinstance(flv_url, str) and flv_url:
                return flv_url

            rtmp_url = stream_url.get('rtmp_pull_url')
            if isinstance(rtmp_url, str) and rtmp_url:
                return rtmp_url

            hls_map = stream_url.get('hls_pull_url_map')
            if isinstance(hls_map, dict) and hls_map:
                for key in ['ORIGIN', 'FULL_HD1', 'HD1', 'SD1']:
                    if hls_map.get(key):
                        return hls_map[key]
                val = next(iter(hls_map.values()), None)
                if val:
                    return val

            return stream_url.get('hls_pull_url')
        except Exception:
            return None

    async def _extract_stream_url_from_html(self, username: str) -> Optional[str]:
        try:
            url = f"https://www.tiktok.com/@{username}/live"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            }
            session_id = self.user_settings.get('tiktok_session_id') or os.getenv('TIKTOK_SESSION_ID')
            cookies = {}
            if session_id:
                cookies['sessionid'] = session_id
                cookies['sessionid_ss'] = session_id

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
                            return target_url
                        
                        m3u8_urls = re.findall(r'https?[^"\'\s\\]+?\.m3u8[^"\'\s\\]*', html)
                        if m3u8_urls:
                            clean_m3u8 = m3u8_urls[0].replace('\\u0026', '&').replace('\\/', '/')
                            return clean_m3u8
        except Exception as e:
            logger.debug(f"HTML stream extraction failed for @{username}: {e}")
        return None

    async def _capture_live_frame(self, username: str) -> Optional[str]:
        try:
            session_id = self.user_settings.get('tiktok_session_id') or os.getenv('TIKTOK_SESSION_ID')
            target_idc = self.user_settings.get('tiktok_target_idc') or os.getenv('TIKTOK_TARGET_IDC')
            
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
                    'no_warnings': True
                }
                if cookie_file:
                    ydl_opts['cookiefile'] = cookie_file
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=False)
                        return info.get('url')
                finally:
                    if cookie_file and os.path.exists(cookie_file):
                        os.remove(cookie_file)
    
            hls_url = await asyncio.to_thread(extract_hls)
            if hls_url:
                frame = await self._capture_live_frame_from_url(username, hls_url)
                if frame:
                    return frame
        except Exception:
            pass
            
        stream_url = await self._extract_stream_url_from_html(username)
        if stream_url:
            return await self._capture_live_frame_from_url(username, stream_url)
        return None

    async def _capture_live_frame_from_url(self, username: str, hls_url: str) -> Optional[str]:
        try:
            output_dir = os.path.join(os.getcwd(), 'generated_images', 'temp')
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, f"live_frame_tiktok_{username}.jpg")
            
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
                logger.info(f"Successfully captured TikTok live frame for @{username}")
                return f"file://{os.path.abspath(output_path)}"
        except Exception as e:
            logger.warning(f"TikTok FFmpeg capture failed for @{username}: {e}")
        return None

    async def get_current_stream_info(self, stream_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        identifier = stream_info.get('identifier')
        if not identifier:
            return None
        return await self.get_stream_info_with_options(
            identifier, 
            use_live_frame=stream_info.get('use_live_frame', False)
        )

    async def is_stream_live(self, username: str) -> bool:
        session_id = self.user_settings.get('tiktok_session_id') or os.getenv('TIKTOK_SESSION_ID')
        target_idc = self.user_settings.get('tiktok_target_idc') or os.getenv('TIKTOK_TARGET_IDC')

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
                    'no_warnings': True
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
            client = TikTokLiveClient(unique_id=username)
            try:
                return await client.is_live()
            except Exception:
                return False

    def get_platform_name(self) -> str:
        return "tiktok"

    def parse_identifier(self, identifier: str) -> Optional[str]:
        if not identifier:
            return None
        match = re.search(
            r"(?:https?://)?(?:www\.)?tiktok\.com/@([a-zA-Z0-9_.]+)", identifier
        )
        if match:
            return match.group(1)
        return identifier.lstrip("@")
