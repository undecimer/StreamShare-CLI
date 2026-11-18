from twitchAPI.twitch import Twitch
from twitchAPI.eventsub.websocket import EventSubWebsocket
from twitchAPI.object.eventsub import StreamOnlineEvent, StreamOfflineEvent, ChannelUpdateEvent
from typing import Dict, Any, Optional, Callable, Tuple
import re
from ...core.config import Config
from ...core.config import get_temp_images_dir
from ...core.exceptions import AuthenticationError, PlatformError, ConfigError
from .base import StreamSource
from .igdb import IGDBClient
from .orientation import normalize_live_frame_orientation, select_native_stream_url, captured_frame_matches
from .ytdlp_runtime import javascript_runtime_options
import logging
import aiohttp
import time
import os
import asyncio
import subprocess
import yt_dlp

logger = logging.getLogger(__name__)

# Hard bound on the synchronous EventSubWebsocket.start() (see
# TwitchEventSubManager.start); kept as a module constant so tests can
# shrink it.
_EVENTSUB_START_TIMEOUT = 10.0

class TwitchEventSubManager:
    """Manages Twitch EventSub over WebSocket for real-time push events"""
    def __init__(self, twitch_client: Twitch):
        self.twitch = twitch_client
        self.eventsub: Optional[EventSubWebsocket] = None
        self._running = False

    async def start(self) -> bool:
        """Start the EventSub WebSocket listener"""
        try:
            self.eventsub = EventSubWebsocket(self.twitch)
            # EventSubWebsocket.start() is synchronous: it spins
            # ``time.sleep`` on the calling thread until its socket thread
            # reports startup. A stalled WSS connect would block the whole
            # event loop forever, so run it in a thread with a hard bound —
            # a timeout falls back to polling like any other failure.
            await asyncio.wait_for(
                asyncio.to_thread(self.eventsub.start),
                timeout=_EVENTSUB_START_TIMEOUT,
            )
            self._running = True
            logger.info("Twitch EventSub WebSocket connected successfully.")
            return True
        except Exception as e:
            logger.warning(f"Twitch EventSub WebSocket failed to start: {e}. Will fallback to polling.")
            self._running = False
            self.eventsub = None
            return False

    async def subscribe_channel(
        self,
        broadcaster_user_id: str,
        on_online: Optional[Callable] = None,
        on_offline: Optional[Callable] = None,
        on_update: Optional[Callable] = None
    ) -> bool:
        """Subscribe to stream events for a specific channel"""
        if not self._running or not self.eventsub:
            return False
        try:
            if on_online:
                await self.eventsub.listen_stream_online(broadcaster_user_id, on_online)
            if on_offline:
                await self.eventsub.listen_stream_offline(broadcaster_user_id, on_offline)
            if on_update:
                await self.eventsub.listen_channel_update(broadcaster_user_id, on_update)
            logger.info(f"Subscribed to Twitch EventSub WebSocket for broadcaster ID {broadcaster_user_id}")
            return True
        except Exception as e:
            logger.warning(f"Failed to subscribe to Twitch EventSub for {broadcaster_user_id}: {e}")
            return False

    async def stop(self):
        """Stop the EventSub WebSocket listener"""
        if self.eventsub and self._running:
            try:
                await self.eventsub.stop()
            except Exception as e:
                logger.debug(f"Error stopping EventSub: {e}")
            finally:
                self._running = False

class TwitchSource(StreamSource):
    def __init__(self, config: Config, user_settings: Dict[str, Any] = None):
        self.global_config = config.get('twitch')
        self.user_settings = user_settings
        self.client: Optional[Twitch] = None
        self.igdb_client: Optional[IGDBClient] = None
        self.eventsub_manager: Optional[TwitchEventSubManager] = None
        self._access_token = None

    async def authenticate(self) -> None:
        """Authenticate with Twitch API using local Client ID/Secret credentials."""
        try:
            client_id = None
            client_secret = None

            if self.user_settings is not None:
                from ...core.security import decrypt_scalar
                client_id = self.user_settings.get('twitch_client_id')
                client_secret = decrypt_scalar(self.user_settings.get('twitch_client_secret'))

            if not (client_id and client_secret) and self.global_config:
                client_id = self.global_config.get('client_id')
                client_secret = self.global_config.get('client_secret')

            if not client_id or not client_secret:
                raise ConfigError("Twitch client ID or client secret not found in configuration")

            self.client = await Twitch(client_id, client_secret)
            self._access_token = await self.client.authenticate_app([])

            # Initialize IGDB client with same Twitch credentials
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
            # Probe Twitch's own platform account — the old 'test' login went
            # defunct and made every healthy credential chain report failure.
            # Any 200 (even empty data) already proves the auth chain works;
            # only request/auth exceptions mean the credentials are bad.
            async for user in self.client.get_users(logins=['twitch']):
                return True
            return True
        except:
            return False

    async def get_stream_info(self, identifier: str) -> Dict[str, Any]:
        """Standard interface for getting stream info (Satisfies ABC)"""
        return await self.get_stream_info_with_options(identifier)

    async def get_stream_info_with_options(self, identifier: str, use_live_frame: bool = False, live_frame_orientation: str = 'auto', **kwargs) -> Dict[str, Any]:
        """Get stream info for a given identifier with options"""
        live_frame_orientation = normalize_live_frame_orientation(live_frame_orientation)
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
                'embed_url': 'https://player.twitch.tv/?channel=test_stream&parent=localhost',
                'vod_url': 'https://twitch.tv/test_stream/videos',
                'is_live': True,
                'identifier': 'test_stream',
                'use_live_frame': False,
                'live_frame_captured': False,
                'live_frame_orientation': live_frame_orientation
            }

        if not self.client:
            await self.authenticate()

        try:
            # Get user info first
            users = []
            async for user in self.client.get_users(logins=[identifier]):
                users.append(user)
                
            if not users:
                raise ValueError(f"User {identifier} not found")
            
            user = users[0]
            
            # Get stream info
            streams = []
            async for stream in self.client.get_streams(user_id=[user.id]):
                streams.append(stream)
                
            if not streams:
                logger.info(f"Stream is not live: {identifier}")
                return None
                
            stream = streams[0]
            logger.debug(f"Stream object: {vars(stream)}")
            
            # Get game info and box art
            games = []
            game_name = "Unknown Game"
            game_id = None
            
            # Only set game_id if we have a valid game
            if stream.game_id and stream.game_id != '0':
                async for game in self.client.get_games(game_ids=[stream.game_id]):
                    games.append(game)
                    game_name = game.name
                    game_id = str(stream.game_id)
                    logger.debug(f"Found Twitch game: {game_name} (id: {stream.game_id})")
            else:
                logger.info("Stream is in a non-gaming category")
            
            # Format image URLs
            thumbnail_url = None
            if stream.thumbnail_url:
                # Use standard 16:9 resolution
                thumbnail_resolution = self.global_config.get('thumbnail_resolution', '1920x1080')
                thumbnail_url = stream.thumbnail_url.format(
                    width=thumbnail_resolution.split('x')[0],
                    height=thumbnail_resolution.split('x')[1]
                )
            else:
                logger.error("Stream thumbnail URL is missing")
                raise PlatformError("Stream thumbnail URL is missing")
            
            # Get box art URL
            box_art_url = None
            if games and games[0].box_art_url:
                box_art_resolution = self.global_config.get('boxart_resolution', '600x800')
                box_art_url = games[0].box_art_url.format(
                    width=box_art_resolution.split('x')[0],
                    height=box_art_resolution.split('x')[1]
                )
            
            # Prepare result dictionary
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
                'embed_url': f"https://player.twitch.tv/?channel={identifier}&parent=localhost",
                'vod_url': f"https://twitch.tv/{identifier}/videos",
                'is_live': True,
                'identifier': identifier,
                'use_live_frame': use_live_frame,
                'live_frame_captured': False,
                'live_frame_orientation': live_frame_orientation
            }

            # Handle key art if requested (preserving logic from get_current_stream_info)
            if kwargs.get('use_keyart') and self.igdb_client and game_name != "Unknown Game":
                key_art_url = await self.igdb_client.get_game_artwork(
                    twitch_id=str(stream.game_id),
                    game_name=game_name,
                    platform='twitch'
                )
                if key_art_url:
                    result['key_art_url'] = key_art_url

            # Capture live frame if requested
            if use_live_frame:
                logger.info(f"Capturing live frame for Twitch user @{identifier}...")
                live_image_url, ad_detected = await self._capture_live_frame(identifier, live_frame_orientation)
                if live_image_url:
                    result['thumbnail_url'] = live_image_url
                    result['use_live_frame'] = True
                    result['live_frame_captured'] = True
                else:
                    # Ad screen detected or capture failed!
                    # Fall back to key art mode!
                    logger.warning(f"Live frame capture failed or detected ad screen for Twitch user @{identifier}. Falling back to Key Art mode.")
                    result['use_live_frame'] = False
                    if live_frame_orientation in ('auto', 'horizontal') or ad_detected:
                        result['use_keyart'] = True
                        result['ad_fallback'] = True
                        if not result.get('key_art_url') and self.igdb_client and game_name != "Unknown Game":
                            key_art_url = await self.igdb_client.get_game_artwork(
                                twitch_id=str(stream.game_id),
                                game_name=game_name,
                                platform='twitch'
                            )
                            if key_art_url:
                                result['key_art_url'] = key_art_url
            
            return result
            
        except Exception as e:
            raise PlatformError(f"Failed to get stream info: {str(e)}")

    async def get_current_stream_info(self, stream_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get current stream info and check for changes
        
        Args:
            stream_info: Previous stream info dictionary
            
        Returns:
            Optional[Dict[str, Any]]: New stream info if stream is live, None if offline
        """
        try:
            return await self.get_stream_info_with_options(
                stream_info.get('identifier') or stream_info.get('user_id'),
                use_live_frame=stream_info.get('use_live_frame', False),
                live_frame_orientation=stream_info.get('live_frame_orientation', 'auto'),
                use_keyart=stream_info.get('use_keyart', False)
            )
        except Exception as e:
            raise PlatformError(f"Failed to get current stream info: {str(e)}")

    async def is_stream_live(self, stream_info: Dict[str, Any]) -> bool:
        """Check if a stream is still live
        
        Args:
            stream_info: Stream info dictionary from get_stream_info
            
        Returns:
            bool: True if stream is still live, False otherwise
        """
        logger.debug(f"Checking if stream {stream_info.get('id')} is still live")
        current_info = await self.get_current_stream_info(stream_info)
        
        if current_info is None:
            logger.info(f"Stream {stream_info.get('id')} has ended (no current info returned)")
            return False
            
        if str(current_info['id']) != stream_info['id']:
            logger.warning(f"Stream ID mismatch - Old: {stream_info['id']}, New: {current_info['id']}")
            return False
            
        logger.debug(f"Stream {stream_info.get('id')} is still live")
        return True

    async def get_game_name(self, game_id: int) -> Optional[str]:
        """Get game name from Twitch game ID
        
        Args:
            game_id: Twitch game ID
            
        Returns:
            Game name if found, None otherwise
        """
        try:
            # Get game info from Twitch
            async for game in self.client.get_games(game_ids=[str(game_id)]):
                game_name = game.name
                logger.debug(f"Found Twitch game: {game_name} (id: {game_id})")
                return game_name
            return None
        except Exception as e:
            logger.error(f"Failed to get game name from Twitch: {str(e)}")
            return None
    async def _get_hls_url(self, username: str, orientation: str = 'auto') -> Optional[str]:
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
                    },
                    **javascript_runtime_options()
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    return select_native_stream_url(info, orientation)
            
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
                img_rgb = img.convert('RGB')
                width, height = img_rgb.size
                if width < 100 or height < 100:
                    return False
                    
                # Sample across multiple points on the background
                sample_points = [
                    (int(width * 0.1), int(height * 0.1)),
                    (int(width * 0.2), int(height * 0.2)),
                    (int(width * 0.8), int(height * 0.2)),
                    (int(width * 0.9), int(height * 0.1)),
                    (int(width * 0.1), int(height * 0.8)),
                    (int(width * 0.2), int(height * 0.7)),
                ]
                
                purple_or_cyan_count = 0
                for pt in sample_points:
                    r, g, b = img_rgb.getpixel(pt)[:3]
                    # Purple/Magenta check: R > 50, B > 100, B > G + 30
                    is_purple = (b > 100 and r > 50 and b > g + 30)
                    # Cyan check: G > 90, B > 130, B > R + 30
                    is_cyan = (b > 130 and g > 90 and b > r + 30)
                    
                    if is_purple or is_cyan:
                        purple_or_cyan_count += 1
                        
                return purple_or_cyan_count >= 4
        except Exception as e:
            logger.error(f"Error checking for Twitch ad screen: {e}")
            return False

    async def _capture_live_frame(self, username: str, orientation: str = 'auto') -> Tuple[Optional[str], bool]:
        """Capture a live frame from a Twitch stream, prioritizing direct ad-free ingest preview and falling back to HLS."""
        orientation = normalize_live_frame_orientation(orientation)
        output_dir = get_temp_images_dir()
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"live_frame_twitch_{username}.jpg")
        ad_detected = False

        # 1. Primary Strategy: Direct 1080p Ingest Frame from Twitch CDN with cache buster
        # This frame is generated server-side by Twitch directly from the broadcaster's ingest feed
        # before any SSAI commercial breaks or pre-roll ads are inserted!
        # Note: Twitch CDN previews are always horizontal (1920x1080). If vertical is requested,
        # Twitch redirects to the 404 camera placeholder, so we only use CDN preview for non-vertical.
        if orientation != 'vertical':
            try:
                cdn_url = f"https://static-cdn.jtvnw.net/previews-ttv/live_user_{username}-1920x1080.jpg?v={int(time.time())}"
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
                }
                async with aiohttp.ClientSession(headers=headers) as session:
                    async with session.get(cdn_url, timeout=aiohttp.ClientTimeout(total=8.0)) as resp:
                        if resp.status == 200 and '404_preview' not in str(resp.url) and resp.headers.get('x-404-redirect') != 'true':
                            content = await resp.read()
                            if len(content) > 10000:
                                with open(output_path, 'wb') as f:
                                    f.write(content)
                                if self._is_twitch_ad_screen(output_path):
                                    ad_detected = True
                                    try:
                                        os.remove(output_path)
                                    except OSError:
                                        pass
                                elif not captured_frame_matches(output_path, orientation):
                                    try:
                                        os.remove(output_path)
                                    except OSError:
                                        pass
                                else:
                                    logger.info(f"Successfully captured Twitch live frame for {username} via direct ingest preview (Ad-Free)")
                                    return f"file://{os.path.abspath(output_path)}", False
            except Exception as e:
                logger.debug(f"Direct CDN preview fetch failed for {username}: {e}")

        # 2. Secondary Strategy: HLS Stream Capture with FFmpeg and Seek
        hls_url = await self._get_hls_url(username, orientation)
        if not hls_url:
            return None, ad_detected
            
        attempts = [0, 5, 15, 30]
        for idx, seek_sec in enumerate(attempts):
            try:
                cmd = [
                    'ffmpeg', '-y',
                    '-rw_timeout', '15000000', # 15s socket timeout
                ]
                if seek_sec > 0:
                    cmd.extend(['-ss', f"00:00:{seek_sec:02d}"])
                cmd.extend([
                    '-i', hls_url,
                    '-frames:v', '1',
                    '-q:v', '2',
                    output_path
                ])
                
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                try:
                    await asyncio.wait_for(process.communicate(), timeout=20.0)
                except asyncio.TimeoutError:
                    logger.warning(f"Twitch FFmpeg capture timed out for {username} (attempt {idx+1}).")
                    try:
                        process.kill()
                        await process.communicate()
                    except Exception:
                        pass
                    continue
                
                if process.returncode == 0 and os.path.exists(output_path):
                    # Verify if this is the Twitch ad screen
                    if self._is_twitch_ad_screen(output_path):
                        ad_detected = True
                        logger.info(f"Twitch ad screen detected for {username} at attempt {idx+1}. Retrying past ad...")
                        try:
                            os.remove(output_path)
                        except Exception:
                            pass
                        await asyncio.sleep(1.0)
                        continue

                    if not captured_frame_matches(output_path, orientation):
                        logger.warning(f"Captured Twitch frame for {username} does not match requested orientation '{orientation}'")
                        try:
                            os.remove(output_path)
                        except Exception:
                            pass
                        continue

                    logger.info(f"Successfully captured Twitch live frame for {username}")
                    return f"file://{os.path.abspath(output_path)}", False
            except Exception as e:
                logger.warning(f"Twitch FFmpeg capture attempt {idx+1} failed for {username}: {e}")
                
        logger.warning(f"Twitch live frame capture failed or detected persistent ad screen for @{username}. Falling back to Key Art/Thumbnail.")
        return None, ad_detected

    async def get_latest_vod(self, user_id: str) -> Optional[str]:
        """Fetch the latest recorded archive VOD for a user."""
        try:
            if not self.client:
                await self.authenticate()
            async for video in self.client.get_videos(user_id=user_id, first=1, video_type='archive'):
                return video.url
        except Exception as e:
            logger.debug(f"Could not fetch latest VOD for Twitch user {user_id}: {e}")
        return None

    async def get_schedule(self, broadcaster_id: str) -> list:
        """Fetch upcoming schedule events for a Twitch channel."""
        try:
            if not self.client:
                await self.authenticate()
            schedule = await self.client.get_channel_stream_schedule(broadcaster_id=broadcaster_id)
            if schedule and schedule.segments:
                return [
                    {
                        "id": str(seg.id),
                        "title": seg.title,
                        "start_time": seg.start_time.isoformat() if seg.start_time else None,
                        "end_time": seg.end_time.isoformat() if seg.end_time else None,
                        "category_name": seg.category.name if seg.category else None,
                        "is_recurring": seg.is_recurring,
                        "is_canceled": seg.canceled_until is not None,
                    }
                    for seg in schedule.segments
                ]
        except Exception as e:
            logger.debug(f"Could not fetch Twitch schedule: {e}")
        return []

    async def start_eventsub(
        self,
        broadcaster_username: str,
        on_online: Optional[Callable] = None,
        on_offline: Optional[Callable] = None,
        on_update: Optional[Callable] = None
    ) -> bool:
        """Initialize and subscribe to Twitch EventSub WebSocket for a channel"""
        if not self.client:
            await self.authenticate()
        
        # Get broadcaster user ID
        user_id = None
        async for user in self.client.get_users(logins=[broadcaster_username]):
            user_id = user.id
            break

        if not user_id:
            logger.warning(f"Could not find Twitch user ID for {broadcaster_username}")
            return False

        self.eventsub_manager = TwitchEventSubManager(self.client)
        started = await self.eventsub_manager.start()
        if not started:
            return False

        return await self.eventsub_manager.subscribe_channel(
            user_id,
            on_online=on_online,
            on_offline=on_offline,
            on_update=on_update
        )

    async def stop_eventsub(self):
        """Stop EventSub WebSocket listener"""
        if self.eventsub_manager:
            await self.eventsub_manager.stop()
            self.eventsub_manager = None

    def get_platform_name(self) -> str:
        return "twitch"

    def parse_identifier(self, identifier: str) -> Optional[str]:
        """Parse Twitch username from various formats
        
        Supports:
        - Plain username
        - twitch.tv/username
        - www.twitch.tv/username
        - https://twitch.tv/username
        - https://www.twitch.tv/username
        """
        # If it's a URL, extract username
        match = re.search(r'(?:https?://)?(?:www\.)?twitch\.tv/([a-zA-Z0-9_]+)', identifier)
        if match:
            return match.group(1)
        
        # If it's just a username
        if re.match(r'^[a-zA-Z0-9_]+$', identifier):
            return identifier
        
        return None
