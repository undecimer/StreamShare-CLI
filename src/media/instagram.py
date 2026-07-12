from PIL import Image
from io import BytesIO
import requests
import os
import logging
from typing import Dict, Any
from .processor import ImageProcessor

logger = logging.getLogger(__name__)

class InstagramImageProcessor(ImageProcessor):
    def __init__(self):
        super().__init__()
        self.width = 1080
        self.height = 1920

    async def process_stream_image(self, stream_info: Dict[str, Any], output_path: str) -> None:
        """Process stream info into a basic 9:16 image for Instagram Stories"""
        logger.info("Processing Instagram Story image...")
        
        if stream_info.get('use_keyart') and stream_info.get('key_art_url'):
            image_url = stream_info.get('cached_image') or stream_info['key_art_url']
        elif stream_info.get('use_boxart') and stream_info.get('box_art_url'):
            image_url = stream_info['box_art_url']
        else:
            image_url = stream_info['thumbnail_url']
        
        image = self.load_image(image_url)
        image, aspect_ratio = self.autodetect_content_box(image)
        
        source_aspect_ratio = stream_info.get('source_aspect_ratio', 'auto')
        if not stream_info.get('use_keyart') and not stream_info.get('use_boxart'):
            image = self.unsquish_image(image, source_aspect_ratio)
        
        crop_anchor = stream_info.get('crop_anchor', 'center')
        image = self.crop_and_resize(image, self.width, self.height, crop_anchor=crop_anchor)
        
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        image.save(output_path, quality=95)
        logger.info(f"Instagram Story image saved to {output_path}")
