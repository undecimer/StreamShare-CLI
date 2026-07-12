from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from ...core.exceptions import PlatformError

class StreamSource(ABC):
    """Base class for stream sources (Twitch, TikTok, etc.)"""
    
    @abstractmethod
    async def authenticate(self) -> None:
        """Authenticate with the platform API"""
        pass
    
    @abstractmethod
    async def validate_credentials(self) -> bool:
        """Check if current credentials are valid"""
        pass
    
    @abstractmethod
    async def get_stream_info(self, identifier: str, ultrawide: bool = False, resolution: str = None) -> Dict[str, Any]:
        """Get stream information for a given identifier
        
        Args:
            identifier: Platform-specific identifier (username, stream ID, etc.)
            ultrawide: Whether to use ultrawide mode
            resolution: Custom resolution string (e.g., "2580x1080")
            
        Returns:
            Dict containing standardized stream information:
            {
                'url': str,           # Stream URL
                'title': str,         # Stream title
                'game_name': str,     # Game/category name
                'thumbnail_url': str, # URL to stream thumbnail
                'box_art_url': str,   # URL to game/category image (optional)
                'platform': str,      # Platform identifier (e.g., 'twitch', 'tiktok')
            }
        """
        pass
    
    @abstractmethod
    def get_platform_name(self) -> str:
        """Get the name of this platform"""
        pass
    
    @abstractmethod
    def parse_identifier(self, identifier: str) -> Optional[str]:
        """Parse and validate a platform-specific identifier
        
        Args:
            identifier: Raw identifier string (e.g., URL, username, etc.)
            
        Returns:
            Parsed identifier or None if this source can't handle the identifier
        """
        pass
