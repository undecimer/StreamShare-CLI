import os
import sys
import logging
import asyncio
import argparse
from .core.config import Config
from .engine import StreamShare

logger = logging.getLogger(__name__)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='Share stream details to social media')
    
    # Get available accounts from config
    config = Config()
    instagram_accounts = list(config.get('instagram_accounts', {}).keys())
    twitter_accounts = list(config.get('twitter_accounts', {}).keys())
    
    # Get enabled accounts as defaults
    default_instagram = [acc for acc, cfg in config.get('instagram_accounts', {}).items() if cfg.get('enabled', False)]
    default_twitter = [acc for acc, cfg in config.get('twitter_accounts', {}).items() if cfg.get('enabled', False)]
    
    # Stream source arguments
    parser.add_argument('identifier', nargs='?', help='Stream identifier (username or URL)')
    parser.add_argument('--platform', choices=['twitch', 'tiktok', 'youtube', 'kick'], help='Platform to get stream from')
    
    # Image source arguments
    parser.add_argument('--boxart', action='store_true', help='Use game box art instead of stream thumbnail')
    parser.add_argument('--keyart', action='store_true', help='Use game key art (promotional art) instead of thumbnail or box art')
    parser.add_argument('--anchor', choices=['center', 'left', 'right', 'top', 'bottom'], default='center', help='Crop anchor for images (default: center)')
    parser.add_argument('--aspect-ratio', choices=['auto', '21:9', '32:9'], default='auto', help='Force source aspect ratio to unstretch')
    
    # Mode arguments
    parser.add_argument('--local', action='store_true', help='Only generate images locally without posting')
    parser.add_argument('--preserve-end-posts', action='store_true', help='Keep the last posts when stream ends (default: cleanup)')
    parser.add_argument('--preserve-category-posts', action='store_true', help='Keep old posts when game category changes (default: cleanup)')
    parser.add_argument('--no-monitor', action='store_true', help='Do not monitor stream after posting (fire and forget)')
    parser.add_argument('--response', action='store_true', help='For Twitter: post URL in a response tweet instead of the main tweet (improves visibility)')
    parser.add_argument('--live-frame', action='store_true', help='For YouTube: Capture a live frame from the stream instead of using the static thumbnail')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    
    # Account selection arguments
    parser.add_argument(
        '--instagram-accounts',
        nargs='+',
        choices=instagram_accounts,
        default=default_instagram,
        metavar='ACCOUNT',
        help=f'Specific Instagram accounts to post to. Available: {", ".join(instagram_accounts)}'
    )
    parser.add_argument(
        '--twitter-accounts',
        nargs='+',
        choices=twitter_accounts,
        default=default_twitter,
        metavar='ACCOUNT',
        help=f'Specific Twitter accounts to post to. Available: {", ".join(twitter_accounts)}'
    )
    
    return parser.parse_args()


def main():
    """Main CLI entry point."""
    args = parse_args()

    # Set up logging
    from .core.logging import setup_logging
    setup_logging(args.debug)
    
    try:
        streamshare = StreamShare(args)
        
        asyncio.run(streamshare.share_stream(
            identifier=args.identifier,
            use_boxart=args.boxart,
            use_keyart=args.keyart,
            crop_anchor=args.anchor,
            source_aspect_ratio=args.aspect_ratio,
            local=args.local,
            response=args.response,
            instagram_accounts=args.instagram_accounts,
            twitter_accounts=args.twitter_accounts,
            no_monitor=args.no_monitor,
            use_live_frame=args.live_frame
        ))
    except KeyboardInterrupt:
        logger.info("Server stopped cleanly.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Application error: {str(e)}")
        raise
