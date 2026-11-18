from abc import ABC, abstractmethod
from typing import Tuple, Optional, Dict, Any
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops
from io import BytesIO
import requests
import os
import aiohttp
import logging
from ..core.config import Config

# Guard against decompression bomb DoS attacks with a safe pixel ceiling (default 25M pixels)
Image.MAX_IMAGE_PIXELS = int(os.getenv('MAX_IMAGE_PIXELS', 25_000_000))

logger = logging.getLogger(__name__)

class ImageProcessor(ABC):
    """Base class for image processing operations"""

    def __init__(self):
        self.config = Config().get('media')
        self.font_path = self.config['font_path']
        # Default dimensions if not specified
        self.width = 1080  # Default width
        self.height = 1920  # Default height for stories

    def load_image(self, image_url: str) -> Image.Image:
        """Load image from URL
        
        Args:
            image_url: URL to image, BytesIO object, or file path
            
        Returns:
            PIL Image
        """
        try:
            if isinstance(image_url, BytesIO):
                image_url.seek(0)
                image = Image.open(BytesIO(image_url.getvalue()))
                image.load()
            elif isinstance(image_url, str):
                if image_url.startswith(('http://', 'https://')):
                    from ..core import ssrf_guard
                    image = Image.open(BytesIO(ssrf_guard.guarded_get(image_url, timeout=10.0)))
                    image.load()
                else:
                    # Handle local file paths (both absolute and relative)
                    if image_url.startswith('file://'):
                        image_url = image_url[7:]  # Remove 'file://' prefix
                    elif image_url.startswith('/artwork_images/'):
                        from ..core.config import get_artwork_dir
                        image_url = os.path.join(get_artwork_dir(), image_url[len('/artwork_images/'):])
                    elif image_url.startswith('/assets/'):
                        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                        image_url = os.path.join(backend_dir, 'assets', image_url[len('/assets/'):])
                    elif image_url.startswith('artwork_images/'):
                        from ..core.config import get_artwork_dir
                        image_url = os.path.join(get_artwork_dir(), image_url[len('artwork_images/'):])
                    elif image_url.startswith('assets/'):
                        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                        image_url = os.path.join(backend_dir, 'assets', image_url[len('assets/'):])

                    image = Image.open(image_url)
                    image.load()
            else:
                raise ValueError(f"Unsupported image source type: {type(image_url)}")
                
            # Convert to RGB if needed
            if image.mode != 'RGB':
                image = image.convert('RGB')
                
            return image
            
        except Image.DecompressionBombError as e:
            logger.error(f"Decompression bomb detected during image load: {str(e)}")
            raise ValueError(f"Image exceeds maximum allowable pixel threshold ({Image.MAX_IMAGE_PIXELS} px): {str(e)}")
        except Exception as e:
            logger.error(f"Failed to load image: {str(e)}")
            raise

    def calculate_luminance(self, r: int, g: int, b: int) -> float:
        """Calculate luminance from RGB values
        
        Args:
            r: Red value (0-255)
            g: Green value (0-255)
            b: Blue value (0-255)
            
        Returns:
            float: Luminance value
        """
        return (0.299 * r + 0.587 * g + 0.114 * b)

    def get_average_color(self, image: Image.Image) -> Tuple[int, int, int]:
        """Get average color of image
        
        Args:
            image: PIL Image to analyze
            
        Returns:
            Tuple[int, int, int]: Average RGB values
        """
        # Resize for faster processing
        small = image.resize((50, 50))
        pixels = small.getdata()
        
        r_total = g_total = b_total = 0
        for r, g, b in pixels:
            r_total += r
            g_total += g
            b_total += b
            
        num_pixels = len(pixels)
        return (
            r_total // num_pixels,
            g_total // num_pixels,
            b_total // num_pixels
        )

    def get_text_color(self, background_color: Tuple[int, int, int]) -> str:
        """Determine optimal text color based on background
        
        Args:
            background_color: RGB values of background
            
        Returns:
            str: 'white' or 'black'
        """
        luminance = self.calculate_luminance(*background_color)
        return 'black' if luminance > 127 else 'white'

    def add_text_with_shadow(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        position: Tuple[int, int],
        font: ImageFont.FreeTypeFont,
        text_color: str = 'white',
        shadow_color: str = 'black',
        blur_radius: int = 3
    ) -> None:
        """Add text with blurred shadow
        
        Args:
            draw: ImageDraw object
            text: Text to draw
            position: (x, y) position
            font: Font to use
            text_color: Color of text
            shadow_color: Color of shadow
            blur_radius: Blur radius for shadow
        """
        # Create shadow layer
        shadow = Image.new('RGBA', draw.im.size)
        shadow_draw = ImageDraw.Draw(shadow)
        
        # Draw shadow text
        shadow_draw.text(position, text, font=font, fill=shadow_color)
        
        # Blur shadow
        shadow = shadow.filter(ImageFilter.GaussianBlur(blur_radius))
        
        # Composite shadow onto original
        draw.im.paste(shadow, (0, 0), shadow)
        
        # Draw main text
        draw.text(position, text, font=font, fill=text_color)

    def autodetect_content_box(self, image: Image.Image, tolerance: int = 15) -> Tuple[Image.Image, float]:
        """Detect the true content by removing black letterbox/pillarbox bars.
        Returns the cropped image and its aspect ratio.
        """
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
            # Image is too wide
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
            # Image is too tall
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
        
        # Resize to final dimensions
        image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
        logger.debug(f"After final resize: {image.size}")
        
        return image

    def process_image(self, image_url: str, category: str, crop_anchor: str = "center") -> Image.Image:
        """Process image for social media
        
        Args:
            image_url: URL or BytesIO of the image to process
            category: Category/game name to add to the image
            crop_anchor: Crop anchor side (e.g. left, right, center, top, bottom)
            
        Returns:
            Processed PIL Image
        """
        # Open image from URL, BytesIO, or local file
        if isinstance(image_url, BytesIO):
            image = Image.open(image_url)
            image_url.seek(0)  # Reset position for potential reuse
        elif isinstance(image_url, str) and image_url.startswith('file://'):
            image = Image.open(image_url[7:])  # Remove 'file://' prefix
        else:
            from ..core import ssrf_guard
            image = Image.open(BytesIO(ssrf_guard.guarded_get(image_url)))

        # Get original dimensions
        original_width, original_height = image.size
        logger.info(f"Original image dimensions: {original_width}x{original_height}")

        # Convert to RGB if needed
        if image.mode != 'RGB':
            image = image.convert('RGB')

        # Auto-detect and strip borders first
        image, aspect_ratio = self.autodetect_content_box(image)

        # Apply standard crop and resize
        target_width = self.width
        target_height = self.height
        image = self.crop_and_resize(image, target_width, target_height, crop_anchor=crop_anchor)
        logger.info(f"Processed image dimensions: {target_width}x{target_height}")

        return image

    async def process_stream_image(self, stream_info: Dict[str, Any], output_path: str) -> None:
        """Process stream image and save to output path
        
        Args:
            stream_info: Dictionary containing stream information
            output_path: Path to save processed image
        """
        # Get image data
        if 'cached_image' in stream_info:
            logger.info("Using cached image")
            stream_info['cached_image'].seek(0)
            image = Image.open(stream_info['cached_image'])
        elif 'preprocessed_image' in stream_info:
            logger.info("Using preprocessed ultrawide image")
            stream_info['preprocessed_image'].seek(0)
            image = Image.open(stream_info['preprocessed_image'])
        else:
            # Use image URL
            image_url = stream_info.get('image_url')
            if not image_url:
                raise ValueError("No image URL or cached image provided")
                
            # Handle file URLs
            if image_url.startswith('file://'):
                image = Image.open(image_url[7:])
            else:
                # Download image
                try:
                    from ..core import ssrf_guard
                    image = Image.open(BytesIO(ssrf_guard.guarded_get(image_url, timeout=10)))
                except Exception as e:
                    logger.error(f"Error downloading image: {str(e)}")
                    raise
        
        # Process image
        # Add your image processing logic here
        image.save(output_path)

    def save(self, image: Image.Image, output_path: str, format: Optional[str] = None) -> None:
        """Save image to file
        
        Args:
            image: PIL Image to save
            output_path: Path to save image to
            format: Optional format override
        """
        image.save(output_path, format=format if format else image.format)
