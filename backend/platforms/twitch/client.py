from twitchAPI.twitch import Twitch
from ...core.config import Config
from ...core.exceptions import AuthenticationError, PlatformError
from ..base import BasePlatform
from typing import Dict, Any, Optional
import aiohttp

class TwitchClient(BasePlatform):
    def __init__(self):
        self.config = Config().get('twitch')
        self.client: Optional[Twitch] = None
        self._access_token = None

    async def authenticate(self) -> None:
        """Authenticate with Twitch API"""
        try:
            self.client = await Twitch(
                self.config['client_id'],
                self.config['client_secret']
            )
            self._access_token = await self.client.authenticate_app([])
        except Exception as e:
            raise AuthenticationError(f"Failed to authenticate with Twitch: {str(e)}")

    async def validate_credentials(self) -> bool:
        """Check if current credentials are valid"""
        try:
            if not self.client:
                await self.authenticate()
            # Probe Twitch's own platform account — 'test' went defunct.
            async for user in self.client.get_users(logins=['twitch']):
                return True
            return True
        except:
            return False

    async def post_content(self, content: Dict[str, Any]) -> str:
        """Not applicable for Twitch - this platform is used for fetching only"""
        raise NotImplementedError("Twitch client is for fetching stream data only")

    async def get_stream_info(self, username: str) -> Dict[str, Any]:
        """Get stream information for a user
        
        Args:
            username: Twitch username to get stream info for
            
        Returns:
            Dict containing stream information
        """
        if not self.client:
            await self.authenticate()

        try:
            # Get user ID first
            user_id = None
            async for user in self.client.get_users(logins=[username]):
                user_id = user.id
                break

            if not user_id:
                raise ValueError(f"User {username} not found")

            # Get stream info
            stream_data = None
            async for stream in self.client.get_streams(user_id=user_id):
                stream_data = stream
                break

            if not stream_data:
                raise ValueError(f"No active stream for {username}")

            # Get game info
            game_data = None
            if stream_data.game_id:
                async for game in self.client.get_games(game_ids=[stream_data.game_id]):
                    game_data = game
                    break

            # Construct response
            thumbnail_url = stream_data.thumbnail_url.replace(
                '{width}x{height}',
                self.config.get('thumbnail_resolution', '1920x1080')
            )

            game_name = game_data.name if game_data else stream_data.game_name
            game_boxart = None
            if game_data and game_data.box_art_url:
                game_boxart = game_data.box_art_url.replace(
                    '{width}x{height}',
                    self.config.get('boxart_resolution', '600x800')
                )

            return {
                'url': f"https://twitch.tv/{username}",
                'title': stream_data.title,
                'game_name': game_name,
                'thumbnail_url': thumbnail_url,
                'box_art_url': game_boxart
            }
        except Exception as e:
            raise PlatformError(f"Failed to get stream info: {str(e)}")
