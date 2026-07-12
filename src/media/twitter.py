from PIL import Image
from io import BytesIO
import requests
import os
import logging
from typing import Dict, Any
from .processor import ImageProcessor

logger = logging.getLogger(__name__)

class TwitterImageProcessor(ImageProcessor):
    def __init__(self):
        super().__init__()
        self.width_landscape = 1280
        self.height_landscape = 720
        self.width_vertical = 1080
        self.height_vertical = 1350

    async def process_stream_image(self, stream_info: Dict[str, Any], output_path: str, force_16_9: bool = False, is_discord: bool = False, is_offline: bool = False, is_category_repost: bool = False) -> None:
        """Process stream info into a basic 16:9 or 4:5 image for Twitter/Discord"""
        logger.info("Processing Twitter/Discord post image...")
        
        if stream_info.get('use_keyart') and stream_info.get('key_art_url'):
            image_url = stream_info.get('cached_image') or stream_info['key_art_url']
            mode = "keyart"
        elif stream_info.get('use_boxart') and stream_info.get('box_art_url'):
            image_url = stream_info['box_art_url']
            mode = "boxart"
        else:
            image_url = stream_info['thumbnail_url']
            mode = "thumbnail"
        
        image = self.load_image(image_url)
        image, aspect_ratio = self.autodetect_content_box(image)
        
        source_aspect_ratio = stream_info.get('source_aspect_ratio', 'auto')
        if mode not in ["keyart", "boxart"]:
            image = self.unsquish_image(image, source_aspect_ratio)
        
        if mode == "keyart" or force_16_9:
            target_width = self.width_landscape
            target_height = self.height_landscape
            crop_anchor = "center"
        else:
            target_width = self.width_vertical
            target_height = self.height_vertical
            crop_anchor = stream_info.get('crop_anchor', 'center')
            if mode == "boxart":
                crop_anchor = "center"
        
        image = self.crop_and_resize(image, target_width, target_height, crop_anchor=crop_anchor)
        
        if is_category_repost:
            image = image.convert('L')
            
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        image.save(output_path, quality=95)
        logger.info(f"Twitter/Discord image saved to {output_path}")
