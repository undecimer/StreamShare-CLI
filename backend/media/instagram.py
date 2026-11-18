import os
import copy
import logging
import asyncio
from typing import Dict, Any
from .processor import ImageProcessor
from .scene_renderer import SceneRenderer
from ..core.config import Config
from ..core.exceptions import ConfigError

logger = logging.getLogger(__name__)


class InstagramImageProcessor(ImageProcessor):
    """Format adapter for Instagram Story media generation."""

    def __init__(self):
        super().__init__()
        config = Config()
        media_config = config.get('media')
        if not media_config:
            raise ConfigError("Media configuration not found")
        
        # Instagram Story dimensions (9:16)
        self.width = 1080
        self.height = 1920
        self.font_path = media_config.get('font_path', os.path.join("fonts", "Roboto-Regular.ttf"))
        self.logo_path = media_config.get('logo_path', os.path.join("images"))

    def process_stream_image_sync(self, stream_info: Dict[str, Any], output_path: str) -> None:
        """Synchronous CPU/disk image processing for Instagram Stories."""
        logger.info(f"Generating Instagram Story ({self.width}x{self.height})...")
        customizer_config = copy.deepcopy(stream_info.get('customizer_config') or {})
        
        renderer = SceneRenderer()
        scene_graph = renderer.migrate_v1_to_v2(customizer_config, "instagram_story")
        renderer.render_scene(scene_graph, stream_info, output_path)
        logger.info(f"Saved Instagram Story: {os.path.basename(output_path)}")

    async def process_stream_image(self, stream_info: Dict[str, Any], output_path: str) -> None:
        """Asynchronous wrapper running image processing in a background worker thread."""
        await asyncio.to_thread(self.process_stream_image_sync, stream_info, output_path)

    def format_text(self, text: str, stream_info: Dict[str, Any]) -> str:
        """Format text using unified template resolution."""
        from ..engine.templates import format_template
        return format_template(text, stream_info)
