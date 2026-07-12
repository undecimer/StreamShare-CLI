from abc import ABC, abstractmethod
from typing import Tuple, Optional, Dict, Any
from PIL import Image, ImageChops
from io import BytesIO
import requests
import os
import logging

logger = logging.getLogger(__name__)

class ImageProcessor(ABC):
    """Base class for image processing operations"""

    def __init__(self):
        self.width = 1080
        self.height = 1920

    def load_image(self, image_url: str) -> Image.Image:
        """Load image from URL, BytesIO, or local file path"""
        try:
            if isinstance(image_url, BytesIO):
                image = Image.open(image_url)
                image_url.seek(0)
            elif isinstance(image_url, str):
                if image_url.startswith(('http://', 'https://')):
                    response = requests.get(image_url)
                    response.raise_for_status()
                    image = Image.open(BytesIO(response.content))
                else:
                    if image_url.startswith('file://'):
                        image_url = image_url[7:]
                    image = Image.open(image_url)
            else:
                raise ValueError(f"Unsupported image source type: {type(image_url)}")
                
            if image.mode != 'RGB':
                image = image.convert('RGB')
            return image
        except Exception as e:
            logger.error(f"Failed to load image: {str(e)}")
            raise

    def autodetect_content_box(self, image: Image.Image, tolerance: int = 15) -> Tuple[Image.Image, float]:
        """Detect the true content by removing black letterbox/pillarbox bars."""
        bg = Image.new(image.mode, image.size, (0, 0, 0))
        diff = ImageChops.difference(image, bg)
        diff = ImageChops.add(diff, diff, 2.0, -tolerance)
        bbox = diff.getbbox()
        if bbox:
            image = image.crop(bbox)
        return image, image.width / image.height

    def unsquish_image(self, image: Image.Image, source_aspect_ratio: str) -> Image.Image:
        """Force stretch the image to a given aspect ratio to reverse squishing"""
        if source_aspect_ratio == "21:9":
            target_ratio = 21 / 9
            new_width = int(image.height * target_ratio)
            image = image.resize((new_width, image.height), Image.Resampling.LANCZOS)
            logger.info(f"Force resized width to {new_width} (21:9 unsquish)")
        elif source_aspect_ratio == "32:9":
            target_ratio = 32 / 9
            new_width = int(image.height * target_ratio)
            image = image.resize((new_width, image.height), Image.Resampling.LANCZOS)
            logger.info(f"Force resized width to {new_width} (32:9 unsquish)")
        return image

    def crop_and_resize(self, image: Image.Image, target_width: int, target_height: int, crop_anchor: str = "center") -> Image.Image:
        """Crop and resize image to target dimensions using anchor"""
        target_ratio = target_width / target_height
        current_ratio = image.width / image.height
        
        logger.debug(f"Target aspect ratio: {target_ratio:.2f}, Current aspect ratio: {current_ratio:.2f}")
        
        if current_ratio > target_ratio:
            logger.debug(f"Image is too wide, cropping width with anchor '{crop_anchor}'")
            new_width = int(image.height * target_ratio)
            if crop_anchor == "left":
                left = 0
            elif crop_anchor == "right":
                left = image.width - new_width
            else:
                left = (image.width - new_width) // 2
            top = 0
            right = left + new_width
            bottom = image.height
        else:
            logger.debug(f"Image is too tall, cropping height with anchor '{crop_anchor}'")
            new_height = int(image.width / target_ratio)
            if crop_anchor == "top":
                top = 0
            elif crop_anchor == "bottom":
                top = image.height - new_height
            else:
                top = (image.height - new_height) // 2
            left = 0
            right = image.width
            bottom = top + new_height
            
        logger.debug(f"Crop box: left={left}, top={top}, right={right}, bottom={bottom}")
        image = image.crop((left, top, right, bottom))
        image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
        return image

    def process_image(self, image_url: str, crop_anchor: str = "center") -> Image.Image:
        image = self.load_image(image_url)
        image, aspect_ratio = self.autodetect_content_box(image)
        image = self.crop_and_resize(image, self.width, self.height, crop_anchor=crop_anchor)
        return image

    @abstractmethod
    async def process_stream_image(self, stream_info: Dict[str, Any], output_path: str) -> None:
        pass
