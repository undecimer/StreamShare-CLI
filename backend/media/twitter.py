import os
import copy
import logging
import asyncio
from typing import Dict, Any
from .processor import ImageProcessor
from .scene_renderer import SceneRenderer
from ..core.config import Config
from ..core.exceptions import ConfigError
from ..engine.templates import format_template

logger = logging.getLogger(__name__)


class TwitterImageProcessor(ImageProcessor):
    """Format adapter for Twitter / Discord post image generation."""

    def __init__(self):
        super().__init__()
        config = Config()
        media_config = config.get('media')
        if not media_config:
            raise ConfigError("Media configuration not found")
        
        # Twitter image dimensions (16:9)
        self.width_landscape = 1280
        self.height_landscape = 720
        
        # Twitter vertical dimensions (4:5)
        self.width_vertical = 1080
        self.height_vertical = 1350
        
        self.font_path = media_config.get('font_path', os.path.join("fonts", "Roboto-Regular.ttf"))
        self.logo_path = media_config.get('logo_path', os.path.join("images"))

    def process_stream_image_sync(
        self,
        stream_info: Dict[str, Any],
        output_path: str,
        force_16_9: bool = False,
        is_discord: bool = False,
        is_offline: bool = False,
        is_category_repost: bool = False
    ) -> None:
        """Synchronous CPU/disk image processing for Twitter or Discord."""
        is_keyart = bool(stream_info.get('use_keyart') and stream_info.get('key_art_url'))
        format_type = "discord_post" if is_discord else ("twitter_landscape" if (is_keyart or force_16_9) else "twitter_post")
        logger.info(f"Generating {'Discord' if is_discord else 'Twitter'} post image ({format_type})...")
        customizer_config = copy.deepcopy(stream_info.get('customizer_config') or {})

        renderer = SceneRenderer()
        scene_graph = renderer.migrate_v1_to_v2(customizer_config, format_type)
        renderer.render_scene(scene_graph, stream_info, output_path)
        logger.info(f"Saved {'Discord' if is_discord else 'Twitter'} image: {os.path.basename(output_path)}")

    async def process_stream_image(
        self,
        stream_info: Dict[str, Any],
        output_path: str,
        force_16_9: bool = False,
        is_discord: bool = False,
        is_offline: bool = False,
        is_category_repost: bool = False
    ) -> None:
        """Asynchronous wrapper running image processing in a background worker thread."""
        await asyncio.to_thread(
            self.process_stream_image_sync,
            stream_info,
            output_path,
            force_16_9,
            is_discord,
            is_offline,
            is_category_repost
        )

    def format_text(self, text: str, stream_info: Dict[str, Any]) -> str:
        """Format text using unified template resolution."""
        return format_template(text, stream_info)
