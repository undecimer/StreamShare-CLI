from typing import Dict, List, Type, Optional, Any
from .base import StreamSource
from .twitch import TwitchSource
from .tiktok import TikTokSource
from .youtube import YoutubeSource
from ...core.config import Config

class SourceManager:
    def __init__(self, config: Config):
        # Register all available sources
        self.sources: Dict[str, StreamSource] = {
            'twitch': TwitchSource(config),
            'tiktok': TikTokSource(config),
            'youtube': YoutubeSource(config),
        }
    
    def get_source(self, platform: str) -> Optional[StreamSource]:
        """Get a source by platform name"""
        return self.sources.get(platform.lower())
    
    def get_all_sources(self) -> List[StreamSource]:
        """Get all registered sources"""
        return list(self.sources.values())
    
    async def get_stream_info(self, identifier: str) -> Dict[str, Any]:
        """Get stream info from the appropriate source based on the identifier"""
        for source in self.sources.values():
            parsed = source.parse_identifier(identifier)
            if parsed:
                return await source.get_stream_info(parsed)
        
        raise ValueError(
            f"Could not parse identifier: {identifier}\n"
            "Please use one of these formats:\n"
            "- Twitch: username or https://twitch.tv/username\n"
            "- TikTok: @username or https://tiktok.com/@username/live\n"
            "- YouTube: @username or channel URL or video URL"
        )
