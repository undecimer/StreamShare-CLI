import os
import time
import logging
import asyncio
from datetime import datetime
from io import BytesIO
from typing import Dict, Any, Optional
from ..platforms.sources.igdb import IGDBClient
from ..media.instagram import InstagramImageProcessor
from ..media.twitter import TwitterImageProcessor

logger = logging.getLogger(__name__)


def cleanup_old_images(days: int = 7) -> None:
    """Delete generated images older than specified days.
    
    Args:
        days: Number of days to keep images
    """
    try:
        from ..core.config import get_generated_images_dir
        output_dir = get_generated_images_dir()
        if not os.path.exists(output_dir):
            return
            
        cutoff = time.time() - (days * 86400)
        count = 0
        
        for filename in os.listdir(output_dir):
            file_path = os.path.join(output_dir, filename)
            if os.path.isfile(file_path):
                if os.path.getmtime(file_path) < cutoff:
                    os.remove(file_path)
                    count += 1
                    
        if count > 0:
            logger.info(f"Cleaned up {count} old images from generated_images/")
            
    except Exception as e:
        logger.error(f"Error cleaning up old images: {e}")


async def generate_local_images(
    stream_info: Dict[str, Any],
    igdb_client: Optional[IGDBClient] = None,
    instagram_processor: Optional[InstagramImageProcessor] = None,
    twitter_processor: Optional[TwitterImageProcessor] = None,
    crop_anchor: str = "center",
    source_aspect_ratio: str = "auto"
) -> None:
    """Generate images locally without posting.
    
    Args:
        stream_info: Dictionary containing stream information
        igdb_client: IGDB client instance for artwork lookups
        instagram_processor: Instagram image processor instance
        twitter_processor: Twitter image processor instance
        crop_anchor: Crop anchor side (top, center, bottom, left, right)
        source_aspect_ratio: Force source aspect ratio ("auto", "16:9", etc.)
    """
    insta_proc = instagram_processor or InstagramImageProcessor()
    twit_proc = twitter_processor or TwitterImageProcessor()

    # Create output directory if it doesn't exist
    from ..core.config import get_generated_images_dir
    output_dir = get_generated_images_dir()
    os.makedirs(output_dir, exist_ok=True)
    
    # Generate timestamp for filenames
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # Get current user for filename
    from ..core.context import get_user
    username = get_user() or "unknown"
    
    # Pass crop_anchor to processors through stream_info
    stream_info['crop_anchor'] = crop_anchor
    stream_info['source_aspect_ratio'] = source_aspect_ratio
    
    # Initialize generated images list
    stream_info['generated_images'] = []
    
    # Load IGDB keyart if requested
    if stream_info.get('use_keyart') and igdb_client:
        game_name = stream_info.get('game_name')
        if game_name and game_name not in ("Offline", "Live", "Live Stream"):
            logger.info(f"Attempting to get key art from IGDB for '{game_name}'")
            artwork = await igdb_client.get_game_artwork(
                twitch_id=str(stream_info.get('game_id') or "None"),
                game_name=game_name,
                platform=stream_info.get('platform')
            )
            if artwork:
                if isinstance(artwork, BytesIO):
                    stream_info['cached_image'] = artwork
                    stream_info['key_art_url'] = 'file://cached'
                    logger.info("Using cached key art from IGDB")
                else:
                    stream_info['key_art_url'] = artwork
                    logger.info(f"Using key art URL from IGDB: {artwork}")
            else:
                logger.warning("No key art found in IGDB, falling back to thumbnail")
        else:
            logger.warning("No valid game name available for key art, falling back to thumbnail")
    elif stream_info.get('use_boxart'):
        if stream_info.get('box_art_url'):
            logger.info("Processors will use stream provided game box art")
        elif igdb_client:
            game_name = stream_info.get('game_name')
            if game_name and game_name not in ("Offline", "Live", "Live Stream"):
                logger.info(f"Attempting to get game box art/cover from IGDB for '{game_name}'")
                cover = await igdb_client.get_game_cover(
                    twitch_id=str(stream_info.get('game_id') or "None"),
                    game_name=game_name,
                    platform=stream_info.get('platform')
                )
                if cover:
                    if isinstance(cover, BytesIO):
                        stream_info['cached_image'] = cover
                        stream_info['box_art_url'] = 'file://cached'
                        logger.info("Using cached box art from IGDB")
                    else:
                        stream_info['box_art_url'] = cover
                        logger.info(f"Using box art URL from IGDB: {cover}")
                else:
                    logger.warning("No box art found in IGDB, falling back to thumbnail")
            else:
                logger.warning("No valid game name available for box art, falling back to thumbnail")
    else:
        logger.info("Processors will use stream thumbnail")
        
    # Generate Instagram Story and Twitter Image concurrently in worker threads
    insta_path = os.path.join(output_dir, f'instagram_story_{username}_{timestamp}.jpg')
    twitter_path = os.path.join(output_dir, f'twitter_{username}_{timestamp}.jpg')
    
    await asyncio.gather(
        insta_proc.process_stream_image(stream_info, insta_path),
        twit_proc.process_stream_image(stream_info, twitter_path)
    )
    stream_info['generated_images'].extend([insta_path, twitter_path])
    logger.info(f"Generated local preview images in: {output_dir}")
