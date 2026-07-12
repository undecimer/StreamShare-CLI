import os
import sys
import logging
import asyncio
import argparse
import tempfile
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple
from io import BytesIO

from .core.config import Config
from .core.exceptions import PlatformError, AuthenticationError
from .platforms.sources.manager import SourceManager
from .platforms.instagram.client import InstagramClient
from .platforms.twitter.client import TwitterClient
from .platforms.discord.client import DiscordWebhookClient
from .media.instagram import InstagramImageProcessor
from .media.twitter import TwitterImageProcessor
from .platforms.sources.base import StreamSource

logger = logging.getLogger(__name__)

class StreamShare:
    def __init__(self, args: Optional[argparse.Namespace] = None):
        self.config = Config()
        default_source = self.config.get('default_source')
        self.default_platform = default_source.get('platform', 'twitch')
        
        self.instagram_clients = {}
        self.twitter_clients = {}
        self.discord_clients = {}
        
        self.instagram_processor = InstagramImageProcessor()
        self.twitter_processor = TwitterImageProcessor()
        
        twitch_config = self.config.get('twitch')
        if twitch_config and twitch_config.get('client_id') and twitch_config.get('client_secret'):
            from .platforms.sources.igdb import IGDBClient
            self.igdb_client = IGDBClient(
                twitch_config['client_id'],
                twitch_config['client_secret']
            )
        else:
            self.igdb_client = None
            
        self.args = args

    def format_template(self, template: str, stream_info: Dict[str, Any], username: str) -> str:
        """Format a template string with placeholders"""
        if not template:
            return ""
        title = stream_info.get('title') or stream_info.get('stream_title') or ""
        game = stream_info.get('game_name') or stream_info.get('category') or ""
        url = stream_info.get('url') or stream_info.get('stream_url') or ""
        platform = stream_info.get('platform') or ""
        u_name = username or stream_info.get('user_name') or stream_info.get('username') or ""
        
        res = template
        res = res.replace('{title}', str(title))
        res = res.replace('{game}', str(game))
        res = res.replace('{category}', str(game))
        res = res.replace('{url}', str(url))
        res = res.replace('{platform}', str(platform))
        res = res.replace('{username}', str(u_name))
        return res

    def get_source(self, platform: str = None) -> StreamSource:
        """Get the appropriate stream source"""
        if not platform:
            platform = self.default_platform

        manager = SourceManager(self.config)
        source = manager.get_source(platform)
        if not source:
            raise ValueError(f"Unsupported platform: {platform}")
        return source

    def cleanup_old_images(self, days: int = 7) -> None:
        """Delete generated images older than specified days"""
        try:
            output_dir = os.path.join(os.getcwd(), 'generated_images')
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

    async def generate_local_images(self, stream_info: Dict[str, Any], crop_anchor: str = "center", source_aspect_ratio: str = "auto") -> None:
        """Generate images locally without posting"""
        output_dir = os.path.join(os.getcwd(), 'generated_images')
        os.makedirs(output_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        from .core.context import get_user
        username = get_user() or "unknown"
        
        stream_info['crop_anchor'] = crop_anchor
        stream_info['source_aspect_ratio'] = source_aspect_ratio
        stream_info['generated_images'] = []
        
        # Load IGDB keyart if requested
        if stream_info.get('use_keyart') and self.igdb_client:
            game_name = stream_info.get('game_name')
            if game_name and game_name not in ("Offline", "Live", "Live Stream"):
                logger.info("Attempting to get key art from IGDB")
                artwork = await self.igdb_client.get_game_artwork(
                    twitch_id=str(stream_info.get('game_id') or "None"),
                    game_name=game_name
                )
                if artwork:
                    if isinstance(artwork, BytesIO):
                        stream_info['cached_image'] = artwork
                        stream_info['key_art_url'] = 'file://cached'
                    else:
                        stream_info['key_art_url'] = artwork
        
        # Generate Instagram Story
        insta_path = os.path.join(output_dir, f'instagram_story_{username}_{timestamp}.jpg')
        await self.instagram_processor.process_stream_image(stream_info, insta_path)
        stream_info['generated_images'].append(insta_path)
        logger.info(f"Generated Instagram Story: {insta_path}")
        
        # Generate Twitter Image
        twitter_path = os.path.join(output_dir, f'twitter_{username}_{timestamp}.jpg')
        await self.twitter_processor.process_stream_image(stream_info, twitter_path)
        stream_info['generated_images'].append(twitter_path)
        logger.info(f"Generated Twitter Image: {twitter_path}")
        
        image_type = "key art" if stream_info.get('use_keyart') else "box art" if stream_info.get('use_boxart') else "thumbnail"
        logger.info(f"\nImages generated in: {output_dir}")
        logger.info(f"Using {image_type}")
        logger.info("Use these images to verify the cropping before posting")

    def build_discord_embeds(self, stream_info: Dict[str, Any], username: str, is_offline: bool = False, image_filename: str = None) -> Optional[List[Dict[str, Any]]]:
        """Build Discord embeds list based on basic styling and stream info"""
        embed_config = {
            "enabled": True,
            "color": "#5865f2",
            "title_template": "{username} is LIVE!",
            "description_template": "{title}",
            "offline_title_template": "{username} was live",
            "offline_description_template": "Thanks for watching! The stream has ended.",
            "footer_template": "Last online • {time}",
            "show_fields": ["game", "views", "duration"]
        }
        
        if not embed_config.get('enabled', True):
            return None
            
        formatted_duration = "0s"
        started_at = stream_info.get('started_at')
        if started_at:
            if isinstance(started_at, str):
                try:
                    started_at = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                except Exception:
                    started_at = None
            if started_at:
                now_utc = datetime.now(timezone.utc)
                diff = now_utc - started_at.replace(tzinfo=timezone.utc)
                total_seconds = int(diff.total_seconds())
                if total_seconds > 0:
                    hours = total_seconds // 3600
                    minutes = (total_seconds % 3600) // 60
                    seconds = total_seconds % 60
                    if hours > 0:
                        formatted_duration = f"{hours}h {minutes}m {seconds}s"
                    elif minutes > 0:
                        formatted_duration = f"{minutes}m {seconds}s"
                    else:
                        formatted_duration = f"{seconds}s"
                        
        color_hex = embed_config.get('color', '#5865f2').replace('#', '')
        try:
            color_int = int(color_hex, 16)
        except ValueError:
            color_int = 5814783
            
        if is_offline:
            title_tpl = embed_config.get('offline_title_template', '{username} was live')
            desc_tpl = embed_config.get('offline_description_template', 'Thanks for watching! The stream has ended.')
        else:
            title_tpl = embed_config.get('title_template', '{username} is LIVE!')
            desc_tpl = embed_config.get('description_template', '{title}')
            
        title_text = self.format_template(title_tpl, stream_info, username)
        desc_text = self.format_template(desc_tpl, stream_info, username)
        
        if stream_info.get('no_monitor', False):
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            footer_text = f"Shared at {current_time}"
        elif not is_offline:
            footer_text = "🔴 LIVE"
        else:
            footer_tpl = embed_config.get('footer_template', 'Last online • {time}')
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            footer_text = footer_tpl.replace('{time}', current_time)
        
        embed = {
            "title": title_text,
            "description": desc_text,
            "color": color_int,
            "fields": [],
            "footer": {
                "text": footer_text
            }
        }
        
        show_fields = embed_config.get('show_fields', ['game', 'views', 'duration'])
        if 'game' in show_fields:
            embed['fields'].append({
                "name": "Game",
                "value": stream_info.get('game_name', 'Unknown Game') or 'Unknown Game',
                "inline": True
            })
        if 'views' in show_fields and not is_offline:
            embed['fields'].append({
                "name": "Views",
                "value": str(stream_info.get('viewer_count', 0)),
                "inline": True
            })
        if 'duration' in show_fields:
            embed['fields'].append({
                "name": "Duration",
                "value": formatted_duration,
                "inline": True
            })
            
        return [embed]

    async def share_stream_posts(self, stream_info: Dict[str, Any], instagram_accounts: List[str] = None, twitter_accounts: List[str] = None, discord_accounts: List[str] = None, accounts_config: Dict[str, Any] = None, response_tweet: bool = False, twitter_manual: bool = False, twitter_reply_accounts: List[str] = None) -> Tuple[Dict[str, Dict[str, str]], Dict[str, Any]]:
        post_ids = {'instagram': {}, 'twitter': {}, 'discord': {}}
        updated_sessions = {'instagram': {}, 'twitter': {}, 'discord': {}}
        
        if accounts_config is None:
            accounts_config = {
                'instagram': self.config.get('instagram_accounts', {}),
                'twitter': self.config.get('twitter_accounts', {}),
                'discord': self.config.get('discord_accounts', {})
            }
        
        self.cleanup_old_images()
        output_dir = os.path.join(os.getcwd(), 'generated_images')
        os.makedirs(output_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        from .core.context import get_user
        username = get_user() or "unknown"
        
        stream_info['generated_images'] = []
        
        # Get key art if requested
        if stream_info.get('use_keyart') and not stream_info.get('key_art_url') and self.igdb_client:
            game_name = stream_info.get('game_name')
            if game_name and game_name not in ("Offline", "Live", "Live Stream") and stream_info.get('game_id'):
                artwork = await self.igdb_client.get_game_artwork(
                    twitch_id=str(stream_info['game_id']),
                    game_name=game_name
                )
                if artwork:
                    if isinstance(artwork, BytesIO):
                        stream_info['cached_image'] = artwork
                        stream_info['key_art_url'] = 'file://cached'
                    else:
                        stream_info['key_art_url'] = artwork

        # 1. Instagram Posting
        instagram_accounts_config = accounts_config.get('instagram', {})
        target_instagram_ids = [acc for acc, config in instagram_accounts_config.items() if config.get('enabled', True)]
        if instagram_accounts is not None:
            target_instagram_ids = [acc for acc in target_instagram_ids if acc in instagram_accounts]
            
        if target_instagram_ids:
            instagram_image = os.path.join(output_dir, f'instagram_story_{username}_{timestamp}.jpg')
            await self.instagram_processor.process_stream_image(stream_info, instagram_image)
            stream_info['generated_images'].append(instagram_image)
            
            caption_template = "🎥 Live Now: {title}\n\nCategory: {game}\n\nWatch here: {url}"
            formatted_caption = self.format_template(caption_template, stream_info, username)
            
            for account_id in target_instagram_ids:
                cache_key = f"{username}_{account_id}"
                if cache_key not in self.instagram_clients:
                    try:
                        client = InstagramClient(account_id, instagram_accounts_config[account_id])
                        await client.authenticate()
                        self.instagram_clients[cache_key] = client
                    except Exception as e:
                        logger.error(f"Failed to initialize Instagram for account '{account_id}': {e}")
                        
                if cache_key in self.instagram_clients:
                    try:
                        content = {
                            'image_path': instagram_image,
                            'url': stream_info.get('url', ''),
                            'caption': formatted_caption
                        }
                        story_id = await self.instagram_clients[cache_key].post_content(content)
                        post_ids['instagram'][account_id] = story_id
                    except Exception as e:
                        logger.error(f"Instagram posting failed for account '{account_id}': {e}")

        # 2. Twitter Posting
        twitter_accounts_config = accounts_config.get('twitter', {})
        target_twitter_ids = [acc for acc, config in twitter_accounts_config.items() if config.get('enabled', True)]
        if twitter_accounts is not None:
            target_twitter_ids = [acc for acc in target_twitter_ids if acc in twitter_accounts]
            
        if target_twitter_ids:
            twitter_image = os.path.join(output_dir, f'twitter_{username}_{timestamp}.jpg')
            await self.twitter_processor.process_stream_image(stream_info, twitter_image)
            stream_info['generated_images'].append(twitter_image)
            
            twitter_template = "🔴 {title}\n\n🕹️ {game}\n\n➡️ Ver en {url}"
            twitter_reply_main_template = "🔴 {title}\n\n🕹️ {game}"
            twitter_reply_template = "➡️ Ver en {url}"
            
            formatted_text = self.format_template(twitter_template, stream_info, username)
            formatted_reply_main = self.format_template(twitter_reply_main_template, stream_info, username)
            formatted_reply = self.format_template(twitter_reply_template, stream_info, username)
            
            for account_id in target_twitter_ids:
                cache_key = f"{username}_{account_id}"
                if cache_key not in self.twitter_clients:
                    try:
                        client = TwitterClient(account_id, twitter_accounts_config[account_id])
                        await client.authenticate()
                        self.twitter_clients[cache_key] = client
                    except Exception as e:
                        logger.error(f"Failed to initialize Twitter for '{account_id}': {e}")
                        
                if cache_key in self.twitter_clients:
                    try:
                        content = {
                            'image_path': twitter_image,
                            'text': formatted_text,
                            'reply_main_text': formatted_reply_main,
                            'reply_text': formatted_reply,
                            'url': stream_info.get('url', ''),
                            'game': stream_info.get('game_name', ''),
                            'use_response': response_tweet
                        }
                        tweet_id = await self.twitter_clients[cache_key].post_content(content)
                        post_ids['twitter'][account_id] = tweet_id
                    except Exception as e:
                        logger.error(f"Twitter posting failed for account '{account_id}': {e}")

        # 3. Discord Webhook Posting
        discord_accounts_config = accounts_config.get('discord', {})
        target_discord_ids = [acc for acc, config in discord_accounts_config.items() if config.get('enabled', True)]
        if discord_accounts is not None:
            target_discord_ids = [acc for acc in target_discord_ids if acc in discord_accounts]
            
        if target_discord_ids:
            discord_image = os.path.join(output_dir, f'discord_{username}_{timestamp}.jpg')
            await self.twitter_processor.process_stream_image(stream_info, discord_image, force_16_9=True, is_discord=True)
            stream_info['generated_images'].append(discord_image)
            
            discord_text_template = "🔴 {title}\n🕹️ {game}\n➡️ Ver en <{url}>"
            
            for account_id in target_discord_ids:
                cache_key = f"{username}_{account_id}"
                if cache_key not in self.discord_clients:
                    try:
                        client = DiscordWebhookClient(account_id, discord_accounts_config[account_id])
                        await client.authenticate()
                        self.discord_clients[cache_key] = client
                    except Exception as e:
                        logger.error(f"Failed to initialize Discord for Webhook '{account_id}': {e}")
                        
                if cache_key in self.discord_clients:
                    try:
                        formatted_text = self.format_template(discord_text_template, stream_info, username)
                        embeds = self.build_discord_embeds(stream_info, username, is_offline=False, image_filename=os.path.basename(discord_image))
                        content = {
                            'text': formatted_text,
                            'image_path': discord_image,
                            'stream_url': stream_info.get('url', ''),
                            'embeds': embeds
                        }
                        msg_id = await self.discord_clients[cache_key].post_content(content)
                        post_ids['discord'][account_id] = msg_id
                    except Exception as e:
                        logger.error(f"Discord posting failed for Webhook '{account_id}': {e}")
                        
        return post_ids, updated_sessions

    async def delete_stream_posts(self, post_ids: Dict[str, Dict[str, str]]) -> None:
        """Delete stream posts from all platforms and accounts"""
        from .core.context import get_user
        username = get_user() or "unknown"

        # Instagram stories
        for account_id, story_id in post_ids.get('instagram', {}).items():
            cache_key = f"{username}_{account_id}"
            if cache_key in self.instagram_clients:
                try:
                    await self.instagram_clients[cache_key].delete_story(story_id)
                    logger.info(f"Deleted Instagram story {story_id}")
                except Exception as e:
                    logger.error(f"Failed to delete Instagram story {story_id}: {e}")

        # Twitter tweets
        for account_id, tweet_id in post_ids.get('twitter', {}).items():
            cache_key = f"{username}_{account_id}"
            if cache_key in self.twitter_clients:
                try:
                    await self.twitter_clients[cache_key].delete_tweet(tweet_id)
                    logger.info(f"Deleted tweet {tweet_id}")
                except Exception as e:
                    logger.error(f"Failed to delete tweet {tweet_id}: {e}")

        # Discord Webhook messages
        for account_id, message_id in post_ids.get('discord', {}).items():
            cache_key = f"{username}_{account_id}"
            if cache_key not in self.discord_clients:
                discord_accounts_config = self.config.get('discord_accounts', {})
                if account_id in discord_accounts_config:
                    try:
                        client = DiscordWebhookClient(account_id, discord_accounts_config[account_id])
                        await client.authenticate()
                        self.discord_clients[cache_key] = client
                    except Exception as e:
                        logger.error(f"Failed to initialize Discord to delete message: {e}")
            if cache_key in self.discord_clients:
                try:
                    await self.discord_clients[cache_key].delete_post(message_id)
                    logger.info(f"Deleted Discord message {message_id}")
                except Exception as e:
                    logger.error(f"Failed to delete Discord message {message_id}: {e}")

    async def monitor_stream(self, source, stream_info: Dict[str, Any], post_ids: Dict[str, Dict[str, str]], preserve_end_posts: bool = False, preserve_category_posts: bool = False, instagram_accounts: List[str] = None, twitter_accounts: List[str] = None, discord_accounts: List[str] = None, accounts_config: Dict[str, Any] = None, response_tweet: bool = False, twitter_manual: bool = False, twitter_reply_accounts: List[str] = None) -> None:
        """Monitor a stream and delete posts when it ends or category changes"""
        try:
            logger.info(f"Starting stream monitoring for {stream_info.get('url')}")
            logger.info("Will check stream status every 5 minutes")
            
            should_preserve_end = preserve_end_posts or (self.args and self.args.preserve_end_posts)
            should_preserve_cat = preserve_category_posts or (self.args and self.args.preserve_category_posts)

            # If offline at start, wait for live
            is_offline_start = stream_info.get('is_offline_start', False)
            if is_offline_start:
                logger.info(f"Stream '{stream_info.get('identifier')}' is offline at start. Entering waiting loop...")
                offline_check_interval = 120
                current_info = None
                while True:
                    logger.info("Checking if stream has gone live...")
                    try:
                        current_info = await source.get_stream_info(stream_info.get('identifier'))
                    except Exception as poll_err:
                        logger.error(f"Error checking if stream is live: {poll_err}")
                        current_info = None
                        
                    if current_info:
                        logger.info("Stream has gone live! Sharing posts...")
                        current_info['use_keyart'] = stream_info.get('use_keyart', False)
                        current_info['use_boxart'] = stream_info.get('use_boxart', False)
                        current_info['crop_anchor'] = stream_info.get('crop_anchor', 'center')
                        current_info['use_live_frame'] = stream_info.get('use_live_frame', False)
                        current_info['source_aspect_ratio'] = stream_info.get('source_aspect_ratio', 'auto')
                        
                        new_post_ids, _ = await self.share_stream_posts(
                            current_info, 
                            instagram_accounts, 
                            twitter_accounts, 
                            discord_accounts, 
                            accounts_config, 
                            response_tweet=response_tweet, 
                            twitter_manual=twitter_manual,
                            twitter_reply_accounts=twitter_reply_accounts
                        )
                        post_ids.clear()
                        post_ids.update(new_post_ids)
                        stream_info = current_info
                        is_offline_start = False
                        break
                        
                    await asyncio.sleep(offline_check_interval)
            
            check_interval = 300
            while True:
                logger.info("Check: Checking stream status...")
                try:
                    current_info = await source.get_current_stream_info(stream_info)
                except Exception as e:
                    logger.error(f"Failed to check stream status: {e}")
                    current_info = stream_info  # Keep going in case of intermittent failure
                    
                if not current_info:
                    logger.info("Stream has ended.")
                    if not should_preserve_end:
                        logger.info("Deleting social media posts...")
                        await self.delete_stream_posts(post_ids)
                    else:
                        logger.info("Preserving social media posts per configuration.")
                    break
                
                # Check for category change
                if current_info.get('game_name') != stream_info.get('game_name'):
                    logger.info(f"Game changed from {stream_info.get('game_name')} to {current_info.get('game_name')}")
                    
                    if not should_preserve_cat:
                        logger.info("Deleting old posts on category change...")
                        await self.delete_stream_posts(post_ids)
                    
                    logger.info("Creating new posts with updated category info...")
                    current_info['use_keyart'] = stream_info.get('use_keyart', False)
                    current_info['use_boxart'] = stream_info.get('use_boxart', False)
                    current_info['crop_anchor'] = stream_info.get('crop_anchor', 'center')
                    current_info['use_live_frame'] = stream_info.get('use_live_frame', False)
                    current_info['source_aspect_ratio'] = stream_info.get('source_aspect_ratio', 'auto')
                    
                    new_post_ids, _ = await self.share_stream_posts(
                        current_info,
                        instagram_accounts,
                        twitter_accounts,
                        discord_accounts,
                        accounts_config,
                        response_tweet=response_tweet,
                        twitter_manual=twitter_manual,
                        twitter_reply_accounts=twitter_reply_accounts
                    )
                    post_ids.clear()
                    post_ids.update(new_post_ids)
                    stream_info = current_info
                    
                await asyncio.sleep(check_interval)
                
        except Exception as e:
            logger.error(f"Error in stream monitoring: {e}")

    async def share_stream(self, identifier: str = None, use_boxart: bool = False, use_keyart: bool = False, use_live_frame: bool = False, crop_anchor: str = "center", source_aspect_ratio: str = "auto", local: bool = False, response: bool = False, instagram_accounts: List[str] = None, twitter_accounts: List[str] = None, discord_accounts: List[str] = None, platform: str = None, accounts_config: Dict[str, Any] = None, preserve_end_posts: bool = False, preserve_category_posts: bool = False, no_monitor: bool = False):
        try:
            if not identifier and self.args:
                identifier = self.args.identifier
            if not identifier:
                raise PlatformError("No stream identifier provided")
                
            target_platform = platform
            if not target_platform and self.args:
                target_platform = self.args.platform
            if not target_platform:
                target_platform = self.default_platform

            source = self.get_source(target_platform)
            name = source.parse_identifier(identifier)
            if not name:
                raise PlatformError(f"Could not parse username from identifier: '{identifier}'")

            should_skip_monitor = no_monitor or (self.args and self.args.no_monitor)
            is_offline_start = False

            stream_info = await source.get_stream_info_with_options(name, use_live_frame=use_live_frame)
            if not stream_info:
                if not local and not should_skip_monitor:
                    is_offline_start = True
                    fallback_url = ""
                    if target_platform == 'twitch':
                        fallback_url = f"https://twitch.tv/{name}"
                    elif target_platform == 'tiktok':
                        fallback_url = f"https://www.tiktok.com/@{name}/live"
                    elif target_platform == 'youtube':
                        fallback_url = f"https://www.youtube.com/@{name}/live" if name.startswith('@') else f"https://www.youtube.com/channel/{name}/live"
                    
                    stream_info = {
                        'platform': target_platform,
                        'identifier': name,
                        'title': name,
                        'game_name': 'Offline',
                        'viewer_count': 0,
                        'url': fallback_url,
                        'is_offline_start': True
                    }
                else:
                    raise PlatformError(f"Stream not found or offline for '{name}' on {target_platform}")

            stream_info['use_boxart'] = use_boxart
            stream_info['use_keyart'] = use_keyart
            stream_info['crop_anchor'] = crop_anchor
            stream_info['use_live_frame'] = use_live_frame
            stream_info['source_aspect_ratio'] = source_aspect_ratio
            stream_info['no_monitor'] = should_skip_monitor

            if local:
                await self.generate_local_images(stream_info, crop_anchor, source_aspect_ratio)
                return {}, {}

            post_ids = {}
            if not is_offline_start:
                post_ids, _ = await self.share_stream_posts(
                    stream_info,
                    instagram_accounts,
                    twitter_accounts,
                    discord_accounts,
                    accounts_config,
                    response_tweet=response
                )
                logger.info("Stream shared successfully!")
            else:
                logger.info("Stream is offline. Entering waiting loop...")

            if not should_skip_monitor:
                await self.monitor_stream(
                    source,
                    stream_info,
                    post_ids,
                    preserve_end_posts=preserve_end_posts,
                    preserve_category_posts=preserve_category_posts,
                    instagram_accounts=instagram_accounts,
                    twitter_accounts=twitter_accounts,
                    discord_accounts=discord_accounts,
                    accounts_config=accounts_config,
                    response_tweet=response
                )

            return post_ids
        except Exception as e:
            logger.error(f"Error sharing stream: {e}")
            raise

def parse_args():
    parser = argparse.ArgumentParser(description='Share stream details to social media')
    
    parser.add_argument('identifier', nargs='?', help='Stream identifier (username or URL)')
    parser.add_argument('--platform', choices=['twitch', 'tiktok', 'youtube'], help='Platform to get stream from')
    
    parser.add_argument('--boxart', action='store_true', help='Use game box art instead of stream thumbnail')
    parser.add_argument('--keyart', action='store_true', help='Use game key art instead of thumbnail or box art')
    parser.add_argument('--anchor', choices=['center', 'left', 'right', 'top', 'bottom'], default='center', help='Crop anchor for images (default: center)')
    parser.add_argument('--aspect-ratio', choices=['auto', '21:9', '32:9'], default='auto', help='Force source aspect ratio to unstretch')
    
    parser.add_argument('--local', action='store_true', help='Only generate images locally without posting')
    parser.add_argument('--preserve-end-posts', action='store_true', help='Keep the last posts when stream ends')
    parser.add_argument('--preserve-category-posts', action='store_true', help='Keep old posts when category changes')
    parser.add_argument('--no-monitor', action='store_true', help='Do not monitor stream after posting')
    parser.add_argument('--response', action='store_true', help='Post URL in a response tweet instead of main tweet')
    parser.add_argument('--live-frame', action='store_true', help='Capture a live frame from the stream using FFmpeg')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    
    return parser.parse_args()

def main():
    args = parse_args()
    from .core.logging import setup_logging
    setup_logging(args.debug)
    
    try:
        streamshare = StreamShare(args)
        asyncio.run(streamshare.share_stream(
            identifier=args.identifier,
            use_boxart=args.boxart,
            use_keyart=args.keyart,
            use_live_frame=args.live_frame,
            crop_anchor=args.anchor,
            source_aspect_ratio=args.aspect_ratio,
            local=args.local,
            response=args.response,
            preserve_end_posts=args.preserve_end_posts,
            preserve_category_posts=args.preserve_category_posts,
            no_monitor=args.no_monitor
        ))
    except Exception as e:
        logger.error(f"Application error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
