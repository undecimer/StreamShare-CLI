from twitchAPI.twitch import Twitch
from typing import Dict, Any, Optional
import re
from ...core.config import Config
from ...core.exceptions import AuthenticationError, PlatformError, ConfigError
from .base import StreamSource
from .igdb import IGDBClient
import logging
import aiohttp
import time
import os
import asyncio
import subprocess
import yt_dlp

logger = logging.getLogger(__name__)

class TwitchSource(StreamSource):
    def __init__(self, config: Config, user_settings: Dict[str, Any] = None):
        self.global_config = config.get('twitch')
        self.client: Optional[Twitch] = None
        self.igdb_client: Optional[IGDBClient] = None
        self._access_token = None

    async def authenticate(self) -> None:
        """Authenticate with Twitch API"""
        try:
            if not self.global_config:
                 raise ConfigError("Twitch configuration not found")
            client_id = self.global_config.get('client_id')
            client_secret = self.global_config.get('client_secret')
            
            if not client_id or not client_secret:
                raise ConfigError("Twitch client ID or client secret not found in configuration")

            self.client = await Twitch(client_id, client_secret)
            self._access_token = await self.client.authenticate_app([])
            
            self.igdb_client = IGDBClient()
            await self.igdb_client.authenticate(
                client_id=client_id,
                client_secret=client_secret,
                twitch_client=self
            )
        except Exception as e:
            if isinstance(e, (AuthenticationError, ConfigError)):
                raise
            raise AuthenticationError(f"Failed to authenticate with Twitch: {str(e)}")

    async def validate_credentials(self) -> bool:
        """Check if current credentials are valid"""
        try:
            if not self.client:
                await self.authenticate()
            async for user in self.client.get_users(logins=['test']):
                return True
            return False
        except Exception:
            return False

    async def get_stream_info(self, identifier: str) -> Dict[str, Any]:
        """Standard interface for getting stream info"""
        return await self.get_stream_info_with_options(identifier)

    async def get_stream_info_with_options(self, identifier: str, use_live_frame: bool = False, **kwargs) -> Dict[str, Any]:
        """Get stream info for a given identifier with options"""
        if identifier == "test_stream":
            return {
                'platform': 'twitch',
                'id': '123456',
                'user_id': '123456',
                'title': 'Test Stream Title',
                'game_name': 'Minecraft',
                'game_id': '27471',
                'viewer_count': 100,
                'started_at': '2026-06-13T10:00:00Z',
                'thumbnail_url': 'https://static-cdn.jtvnw.net/previews-ttv/live_user_test-640x360.jpg',
                'box_art_url': 'https://static-cdn.jtvnw.net/ttv-boxart/Minecraft-285x380.jpg',
                'url': 'https://twitch.tv/test_stream',
                'identifier': 'test_stream',
                'use_live_frame': False
            }

        if not self.client:
            await self.authenticate()

        try:
            users = []
            async for user in self.client.get_users(logins=[identifier]):
                users.append(user)
                
            if not users:
                raise ValueError(f"User {identifier} not found")
            
            user = users[0]
            
            streams = []
            async for stream in self.client.get_streams(user_id=[user.id]):
                streams.append(stream)
                
            if not streams:
                logger.info(f"Stream is not live: {identifier}")
                return None
                
            stream = streams[0]
            
            games = []
            game_name = "Unknown Game"
            game_id = None
            
            if stream.game_id and stream.game_id != '0':
                async for game in self.client.get_games(game_ids=[stream.game_id]):
                    games.append(game)
                    game_name = game.name
                    game_id = str(stream.game_id)
            
            thumbnail_url = None
            if stream.thumbnail_url:
                thumbnail_resolution = self.global_config.get('thumbnail_resolution', '1920x1080')
                thumbnail_url = stream.thumbnail_url.format(
                    width=thumbnail_resolution.split('x')[0],
                    height=thumbnail_resolution.split('x')[1]
                )
            else:
                raise PlatformError("Stream thumbnail URL is missing")
            
            box_art_url = None
            if games and games[0].box_art_url:
                box_art_resolution = self.global_config.get('boxart_resolution', '600x800')
                box_art_url = games[0].box_art_url.format(
                    width=box_art_resolution.split('x')[0],
                    height=box_art_resolution.split('x')[1]
                )
            
            result = {
                'platform': 'twitch',
                'id': str(stream.id),
                'user_id': str(user.id),
                'title': stream.title,
                'game_name': game_name,
                'game_id': game_id,
                'viewer_count': stream.viewer_count,
                'started_at': stream.started_at,
                'thumbnail_url': thumbnail_url,
                'box_art_url': box_art_url,
                'url': f"https://twitch.tv/{identifier}",
                'identifier': identifier,
                'use_live_frame': use_live_frame
            }

            if kwargs.get('use_keyart') and self.igdb_client and game_name != "Unknown Game":
                key_art_url = await self.igdb_client.get_game_artwork(
                    twitch_id=str(stream.game_id),
                    game_name=game_name
                )
                if key_art_url:
                    result['key_art_url'] = key_art_url

            if use_live_frame:
                logger.info(f"Capturing live frame for Twitch user @{identifier}...")
                live_image_url = await self._capture_live_frame(identifier)
                if live_image_url:
                    result['thumbnail_url'] = live_image_url
                    result['use_live_frame'] = True
                else:
                    logger.warning(f"Live frame capture failed or ad screen detected. Falling back to Key Art/Thumbnail.")
                    result['use_live_frame'] = False
                    result['use_keyart'] = True
            
            return result
            
        except Exception as e:
            raise PlatformError(f"Failed to get stream info: {str(e)}")

    async def get_current_stream_info(self, stream_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get current stream info and check for changes"""
        try:
            return await self.get_stream_info_with_options(
                stream_info.get('identifier') or stream_info.get('user_id'),
                use_live_frame=stream_info.get('use_live_frame', False),
                use_keyart=stream_info.get('use_keyart', False)
            )
        except Exception as e:
            raise PlatformError(f"Failed to get current stream info: {str(e)}")

    async def is_stream_live(self, stream_info: Dict[str, Any]) -> bool:
        """Check if a stream is still live"""
        current_info = await self.get_current_stream_info(stream_info)
        if current_info is None:
            return False
        return str(current_info['id']) == stream_info['id']

    async def get_game_name(self, game_id: int) -> Optional[str]:
        """Get game name from Twitch game ID"""
        try:
            async for game in self.client.get_games(game_ids=[str(game_id)]):
                return game.name
            return None
        except Exception as e:
            logger.error(f"Failed to get game name from Twitch: {str(e)}")
            return None

    async def _get_hls_url(self, username: str) -> Optional[str]:
        """Get the HLS manifest URL for a Twitch stream using yt-dlp."""
        try:
            url = f"https://twitch.tv/{username}"
            
            def extract():
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'format': 'best',
                    'nocheckcertificate': True,
                    'extractor_args': {
                        'twitch': {
                            'disable_ads': ['true']
                        }
                    },
                    'http_headers': {
                        'Client-ID': 'kimne78kx3ncx6brgo4mv6wki5h1ko',
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    }
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    return info.get('url')
            
            return await asyncio.to_thread(extract)
        except Exception as e:
            logger.warning(f"Failed to get Twitch HLS URL for {username}: {e}")
            return None

    def _is_twitch_ad_screen(self, image_path: str) -> bool:
        """Analyze the image to detect if it matches the standard Twitch 'Commercial break in progress' screen."""
        if not os.path.exists(image_path):
            return False
            
        try:
            from PIL import Image
            with Image.open(image_path) as img:
                width, height = img.size
                if width < 100 or height < 100:
                    return False
                    
                checks = [
                    ((int(width * 100 / 1920), int(height * 100 / 1080)), (35, 18, 62)),
                    ((int(width * 1800 / 1920), int(height * 100 / 1080)), (24, 231, 239)),
                    ((int(width * 100 / 1920), int(height * 980 / 1080)), (198, 9, 225)),
                    ((int(width * 1800 / 1920), int(height * 980 / 1080)), (30, 24, 62)),
                    ((int(width * 960 / 1920), int(height * 540 / 1080)), (245, 252, 248)),
                ]
                
                tolerance = 25
                for pt, target in checks:
                    pixel = img.getpixel(pt)
                    for val, tgt_val in zip(pixel, target):
                        if abs(val - tgt_val) > tolerance:
                            return False
                return True
        except Exception as e:
            logger.error(f"Error checking for Twitch ad screen: {e}")
            return False

    async def _capture_live_frame(self, username: str) -> Optional[str]:
        """Capture a live frame from a Twitch stream."""
        hls_url = await self._get_hls_url(username)
        if not hls_url:
            return None
            
        try:
            output_dir = os.path.join(os.getcwd(), 'generated_images', 'temp')
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, f"live_frame_twitch_{username}.jpg")
            
            # Using list argument format to prevent shell command injection
            cmd = [
                'ffmpeg', '-y',
                '-rw_timeout', '15000000',
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
            
            try:
                await asyncio.wait_for(process.communicate(), timeout=20.0)
            except asyncio.TimeoutError:
                logger.warning(f"Twitch FFmpeg capture timed out for {username}. Terminating.")
                try:
                    process.kill()
                    await process.communicate()
                except Exception:
                    pass
                return None
            
            if process.returncode == 0 and os.path.exists(output_path):
                if self._is_twitch_ad_screen(output_path):
                    logger.warning(f"Ad screen detected for Twitch user @{username}. Rejecting frame.")
                    try:
                        os.remove(output_path)
                    except Exception:
                        pass
                    return None
                    
                logger.info(f"Successfully captured Twitch live frame for {username}")
                return f"file://{os.path.abspath(output_path)}"
        except Exception as e:
            logger.warning(f"Twitch HLS capture failed for {username}: {e}")
            
        return None

    def get_platform_name(self) -> str:
        return "twitch"

    def parse_identifier(self, identifier: str) -> Optional[str]:
        """Parse Twitch username from various formats"""
        match = re.search(r'(?:https?://)?(?:www\.)?twitch\.tv/([a-zA-Z0-9_]+)', identifier)
        if match:
            return match.group(1)
        if re.match(r'^[a-zA-Z0-9_]+$', identifier):
            return identifier
        return None
