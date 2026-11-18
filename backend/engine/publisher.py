import os
import logging
import asyncio
from datetime import datetime
from urllib.parse import urlparse
from io import BytesIO
from typing import Callable, Dict, Any, Optional, List, Tuple
from ..core.config import Config
from ..platforms.instagram.client import InstagramClient
from ..platforms.twitter.client import TwitterClient
from ..platforms.discord.factory import discord_client
from ..platforms.sources.igdb import IGDBClient
from ..media.instagram import InstagramImageProcessor
from ..media.twitter import TwitterImageProcessor
from .images import cleanup_old_images
from .templates import format_template
from .discord_embeds import build_discord_embeds

logger = logging.getLogger(__name__)


def resolve_instagram_story_link(
    customizer_config: Dict[str, Any],
    stream_info: Dict[str, Any],
    username: str,
) -> Dict[str, Any]:
    stream_url = str(
        stream_info.get('url')
        or stream_info.get('stream_url')
        or stream_info.get('channel_url')
        or ''
    ).strip()
    story_config = (customizer_config or {}).get('instagram_story')

    if not isinstance(story_config, dict):
        parsed = urlparse(stream_url)
        return {'url': stream_url} if parsed.scheme in ('http', 'https') and parsed.netloc else {}

    layers = story_config.get('layers')
    if isinstance(layers, list):
        sticker = next((
            layer for layer in layers
            if isinstance(layer, dict)
            and layer.get('type') == 'sticker'
            and layer.get('visible', True)
        ), None)
        if sticker is None:
            return {}
        width = max(float(story_config.get('width') or 1080), 1.0)
        height = max(float(story_config.get('height') or 1920), 1.0)
        position = sticker.get('position') if isinstance(sticker.get('position'), dict) else {}
        scale = max(0.2, min(float(sticker.get('scale') or 1.0), 3.0))
        x = max(0.0, min(float(position.get('x', width / 2)) / width, 1.0))
        y = max(0.0, min(float(position.get('y', height * 0.7)) / height, 1.0))
        sticker_width = max(0.05, min(0.5 * scale, 1.0))
        sticker_height = max(0.03, min((width * 0.5 * scale * 0.24) / height, 1.0))
        link_template = str(sticker.get('link_url') or '{url}')
    else:
        if story_config.get('show_link_sticker', True) is False:
            return {}
        width = 1080.0
        height = 1920.0
        x = 0.5
        y = max(0.0, min(float(story_config.get('link_sticker_y', 1280)) / height, 1.0))
        sticker_width = 0.5
        sticker_height = (width * 0.5 * 0.24) / height
        link_template = str(story_config.get('link_sticker_url') or '{url}')

    link_url = format_template(link_template, stream_info, username).strip()
    parsed = urlparse(link_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        link_url = stream_url
    parsed = urlparse(link_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return {}

    return {
        'url': link_url,
        'sticker_x': x,
        'sticker_y': y,
        'sticker_width': sticker_width,
        'sticker_height': sticker_height,
    }


def resolve_instagram_story_interactions(
    customizer_config: Dict[str, Any],
    stream_info: Dict[str, Any],
    username: str,
) -> Dict[str, Any]:
    story_config = (customizer_config or {}).get('instagram_story')
    if not isinstance(story_config, dict):
        return {}
    layers = story_config.get('layers')
    if not isinstance(layers, list):
        return {}

    width = max(float(story_config.get('width') or 1080), 1.0)
    height = max(float(story_config.get('height') or 1920), 1.0)

    mention_layer = next((
        layer for layer in layers
        if isinstance(layer, dict)
        and layer.get('type') == 'instagram_mention'
        and layer.get('visible', True)
    ), None)
    hashtag_layer = next((
        layer for layer in layers
        if isinstance(layer, dict)
        and layer.get('type') == 'instagram_hashtag'
        and layer.get('visible', True)
    ), None)

    def _geometry(layer: Dict[str, Any]) -> Dict[str, float]:
        position = layer.get('position') if isinstance(layer.get('position'), dict) else {}
        scale = max(0.2, min(float(layer.get('scale') or 0.8), 3.0))
        tag_width = max(0.05, min(0.42 * scale, 1.0))
        tag_height = max(0.03, min((width * 0.42 * scale * 0.16) / height, 1.0))
        return {
            'x': max(0.0, min(float(position.get('x', width / 2)) / width, 1.0)),
            'y': max(0.0, min(float(position.get('y', height * 0.5)) / height, 1.0)),
            'width': tag_width,
            'height': tag_height,
            'rotation': float(layer.get('rotation') or 0.0),
        }

    result: Dict[str, Any] = {}

    if mention_layer is not None:
        value = format_template(str(mention_layer.get('mention_username') or ''), stream_info, username).strip().lstrip('@')
        if value:
            result['story_mentions'] = [{'username': value, **_geometry(mention_layer)}]

    if hashtag_layer is not None:
        value = format_template(str(hashtag_layer.get('hashtag_name') or ''), stream_info, username).strip().lstrip('#')
        if value:
            result['story_hashtags'] = [{'name': value, **_geometry(hashtag_layer)}]

    return result


async def _authorize_destinations(
    platform_key: str,
    target_ids: List[str],
    accounts_map: Dict[str, Any],
    destination_authorizer: Optional[Callable],
    post_ids: Dict[str, Any],
) -> List[str]:
    """Fail-closed per-destination publish authorization.

    Each selected destination must resolve its exact publish-grant connection
    id from the annotated config entry and obtain a fresh central authorization
    through the injected ``destination_authorizer`` — immediately before the
    platform loop that would post to it. Denied destinations are skipped
    individually: the denial is recorded under ``post_ids['destination_errors']``
    while other destinations keep working. A missing authorizer denies every
    real post rather than silently bypassing authorization.
    """
    allowed: List[str] = []
    for account_id in target_ids:
        entry = accounts_map.get(account_id) or {}
        conn_id = str(entry.get('connection_id') or '').strip()
        error = None
        try:
            if destination_authorizer is None:
                error = "authorization_unavailable: no destination authorizer installed"
            elif not conn_id:
                error = "authorization_denied: destination is not bound to a publish grant"
            else:
                proof = await destination_authorizer(platform_key, account_id, conn_id)
                if not isinstance(proof, dict) or proof.get('allowed') is not True:
                    error = "authorization_denied: destination authorizer returned no verified grant"
        except Exception as exc:
            error = f"authorization_denied: {str(exc)[:160]}"
        if error:
            logger.warning(f"[PublishGrant] {platform_key}/{account_id} publish denied: {error}")
            post_ids.setdefault('destination_errors', []).append({
                'platform': platform_key,
                'account_id': account_id,
                'error': error,
            })
            continue
        allowed.append(account_id)
    return allowed


async def share_stream_posts(
    stream_info: Dict[str, Any],
    instagram_accounts: Optional[List[str]] = None,
    twitter_accounts: Optional[List[str]] = None,
    discord_accounts: Optional[List[str]] = None,
    accounts_config: Optional[Dict[str, Any]] = None,
    response_tweet: bool = False,
    twitter_manual: bool = False,
    tiktok_manual: bool = False,
    twitter_reply_accounts: Optional[List[str]] = None,
    config: Optional[Config] = None,
    instagram_clients: Optional[Dict[str, Any]] = None,
    twitter_clients: Optional[Dict[str, Any]] = None,
    discord_clients: Optional[Dict[str, Any]] = None,
    instagram_processor: Optional[InstagramImageProcessor] = None,
    twitter_processor: Optional[TwitterImageProcessor] = None,
    tiktok_processor: Optional[Any] = None,
    igdb_client: Optional[IGDBClient] = None,
    destination_authorizer: Optional[Callable] = None
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Share stream posts to all enabled accounts.

    Real destination posts are individually authorized through
    ``destination_authorizer`` immediately before their provider call; when
    the authorizer is absent, every real destination fails closed and only
    manual/preview paths (e.g. twitter_manual share entries) still run.
    Denials are recorded under ``post_ids['destination_errors']``.

    Returns:
        Tuple containing:
        - Dictionary containing post IDs for each platform and account
        - Dictionary containing updated session settings for each platform and account
    """
    cfg = config or Config()
    insta_clients = instagram_clients if instagram_clients is not None else {}
    twit_clients = twitter_clients if twitter_clients is not None else {}
    disc_clients = discord_clients if discord_clients is not None else {}
    insta_proc = instagram_processor or InstagramImageProcessor()
    twit_proc = twitter_processor or TwitterImageProcessor()

    post_ids = {'instagram': {}, 'twitter': {}, 'discord': {}}
    updated_sessions = {'instagram': {}, 'twitter': {}, 'discord': {}}
    
    # Use provided config or fall back to Config
    if accounts_config is None:
        accounts_config = {
            'instagram': cfg.get('instagram_accounts', {}),
            'twitter': cfg.get('twitter_accounts', {}),
            'discord': cfg.get('discord_accounts', {})
        }
    
    # Check if this is a gaming category
    is_gaming_category = stream_info.get('game_id') is not None
    logger.info(f"Category type: {'gaming' if is_gaming_category else 'non-gaming'}")
    
    # Cleanup old images first
    cleanup_old_images()
    
    # Create output directory if it doesn't exist
    from ..core.config import get_generated_images_dir
    output_dir = get_generated_images_dir()
    os.makedirs(output_dir, exist_ok=True)
    
    # Generate timestamp for filenames
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # Get current user for filename
    from ..core.context import get_user
    username = get_user() or "unknown"
    
    # Initialize generated images list
    stream_info['generated_images'] = []
    
    # Load IGDB keyart if requested and not already cached
    if stream_info.get('use_keyart') and not stream_info.get('key_art_url') and igdb_client:
        if stream_info.get('game_id'):
            logger.info("Attempting to get key art from IGDB")
            artwork = await igdb_client.get_game_artwork(
                twitch_id=str(stream_info['game_id']),
                game_name=stream_info.get('game_name'),
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
            logger.warning("No game ID available for key art, falling back to thumbnail")
    
    # ---------------------------------------------------------
    # Instagram Processing
    # ---------------------------------------------------------
    instagram_accounts_config = accounts_config.get('instagram', {})
    target_instagram_ids = []
    
    for account_id, account in instagram_accounts_config.items():
        if account.get('enabled', False):
            if instagram_accounts is not None and account_id not in instagram_accounts:
                continue
            target_instagram_ids.append(account_id)

    # ---------------------------------------------------------
    # Identify target Twitter accounts
    # ---------------------------------------------------------
    twitter_accounts_config = accounts_config.get('twitter', {})
    target_twitter_ids = []
    
    for account_id, account in twitter_accounts_config.items():
        if account.get('enabled', True):
            if twitter_accounts is not None and account_id not in twitter_accounts:
                continue
            target_twitter_ids.append(account_id)

    # Per-destination publish authorization — immediately before any provider
    # work. Each destination needs its own fresh central publish-grant proof;
    # denials are recorded and skipped without blocking other destinations.
    if target_instagram_ids:
        target_instagram_ids = await _authorize_destinations(
            'instagram', target_instagram_ids, instagram_accounts_config,
            destination_authorizer, post_ids,
        )
    if target_twitter_ids:
        target_twitter_ids = await _authorize_destinations(
            'twitter', target_twitter_ids, twitter_accounts_config,
            destination_authorizer, post_ids,
        )

    # ---------------------------------------------------------
    # Pre-generate Required Images Concurrently in Worker Threads
    # ---------------------------------------------------------
    need_instagram_img = bool(target_instagram_ids)
    need_twitter_img = bool(target_twitter_ids or twitter_manual)
    need_tiktok_img = bool(tiktok_manual)

    instagram_image = os.path.join(output_dir, f'instagram_story_{username}_{timestamp}.jpg') if need_instagram_img else None
    twitter_image = os.path.join(output_dir, f'twitter_{username}_{timestamp}.jpg') if need_twitter_img else None
    tiktok_image = os.path.join(output_dir, f'tiktok_story_{username}_{timestamp}.jpg') if need_tiktok_img else None

    img_tasks = []
    if need_instagram_img:
        img_tasks.append(insta_proc.process_stream_image(stream_info, instagram_image))
    if need_twitter_img:
        img_tasks.append(twit_proc.process_stream_image(stream_info, twitter_image))
    if need_tiktok_img:
        if tiktok_processor is None:
            from ..media.tiktok import TikTokStoryImageProcessor
            tiktok_processor = TikTokStoryImageProcessor()
        img_tasks.append(tiktok_processor.process_stream_image(stream_info, tiktok_image))

    if img_tasks:
        await asyncio.gather(*img_tasks)
        if need_instagram_img:
            stream_info['generated_images'].append(instagram_image)
            logger.info(f"Generated persistent Instagram image: {instagram_image}")
        if need_twitter_img:
            stream_info['generated_images'].append(twitter_image)
            logger.info(f"Generated persistent Twitter image: {twitter_image}")
        if need_tiktok_img:
            stream_info['generated_images'].append(tiktok_image)
            logger.info(f"Generated persistent TikTok Story image: {tiktok_image}")
    
    if target_instagram_ids:
        logger.info(f"Processing for {len(target_instagram_ids)} Instagram account(s)")
        
        # Caption template
        customizer_config = stream_info.get('customizer_config') or {}
        instagram_caption_template = customizer_config.get("instagram_caption_template", "{title}")
        formatted_caption = format_template(instagram_caption_template, stream_info, username)
        story_link = resolve_instagram_story_link(customizer_config, stream_info, username)
        story_interactions = resolve_instagram_story_interactions(customizer_config, stream_info, username)

        # Initialize clients and post for target accounts
        for account_id in target_instagram_ids:
            cache_key = f"{username}_{account_id}"
            if cache_key not in insta_clients:
                try:
                    account = instagram_accounts_config[account_id]
                    client = InstagramClient(account_id, account)
                    if client.login():
                        insta_clients[cache_key] = client
                        logger.info(f"Initialized Instagram client for account '{account_id}' (user '{username}')")
                except Exception as e:
                    logger.error(f"Failed to initialize Instagram client for account '{account_id}': {str(e)}")
            
            if cache_key in insta_clients:
                client = insta_clients[cache_key]
                try:
                    content = {
                        'image_path': instagram_image,
                        'caption': formatted_caption
                    }
                    content.update(story_link)
                    content.update(story_interactions)
                    story_id = await client.post_content(content)
                    post_ids['instagram'][account_id] = story_id
                    logger.info(f"Successfully posted Instagram story for account '{account_id}'")
                except Exception as e:
                    logger.error(f"Failed to post Instagram story for account '{account_id}': {str(e)}")
    else:
        logger.info("No Instagram accounts selected, skipping Instagram processing")

    # ---------------------------------------------------------
    # Twitter Processing
    # ---------------------------------------------------------
    if target_twitter_ids or twitter_manual:
        logger.info("Processing Twitter sharing...")
        
        # Post / Templates Formatting
        customizer_config = stream_info.get('customizer_config') or {}
        twitter_text_template = customizer_config.get("twitter_text_template", "🔴 {title}\n\n🕹️ {game}\n\n➡️ Ver en {url}")
        twitter_reply_main_template = customizer_config.get("twitter_reply_main_template", "🔴 {title}\n\n🕹️ {game}")
        twitter_reply_template = customizer_config.get("twitter_reply_template", "➡️ Ver en {url}")
        formatted_text = format_template(twitter_text_template, stream_info, username)
        formatted_reply_main = format_template(twitter_reply_main_template, stream_info, username)
        formatted_reply = format_template(twitter_reply_template, stream_info, username)
        
        if twitter_manual:
            logger.info("Manual share requested, skipping API posting")
            if 'twitter_manual_shares' not in post_ids:
                post_ids['twitter_manual_shares'] = []
            
            # Determine if manual mode should use reply mode
            use_reply = False
            if twitter_reply_accounts is not None:
                use_reply = 'manual' in twitter_reply_accounts
            else:
                use_reply = response_tweet
            
            share_entry = {
                'account_id': 'Manual',
                'image_filename': os.path.basename(twitter_image),
                'url': stream_info.get('url', ''),
                'error': 'Manual Share Selected'
            }
            if use_reply:
                share_entry['text'] = formatted_reply_main
                share_entry['reply_text'] = formatted_reply
            else:
                share_entry['text'] = formatted_text
                
            post_ids['twitter_manual_shares'].append(share_entry)
        else:
            logger.info(f"Processing for {len(target_twitter_ids)} Twitter account(s)")
            current_twitter_clients = {}
            for account_id in target_twitter_ids:
                try:
                    cache_key = f"{username}_{account_id}"
                    if cache_key in twit_clients:
                        current_twitter_clients[account_id] = twit_clients[cache_key]
                    else:
                        logger.info(f"Initializing Twitter client for account '{account_id}'...")
                        account = twitter_accounts_config[account_id]
                        client = TwitterClient(account_id, credentials=account)
                        await client.authenticate()
                        twit_clients[cache_key] = client
                        current_twitter_clients[account_id] = client
                        logger.info(f"Successfully initialized Twitter client for account '{account_id}'")
                except Exception as e:
                    logger.error(f"Failed to initialize Twitter client for account '{account_id}': {str(e)}")
                    error_msg = str(e)
                    if 'twitter_manual_shares' not in post_ids:
                        post_ids['twitter_manual_shares'] = []
                    
                    use_reply = False
                    if twitter_reply_accounts is not None:
                        use_reply = account_id in twitter_reply_accounts
                    else:
                        use_reply = response_tweet
                    
                    share_entry = {
                        'account_id': account_id,
                        'image_filename': os.path.basename(twitter_image),
                        'url': stream_info.get('url', ''),
                        'error': error_msg
                    }
                    if use_reply:
                        share_entry['text'] = formatted_reply_main
                        share_entry['reply_text'] = formatted_reply
                    else:
                        share_entry['text'] = formatted_text
                        
                    post_ids['twitter_manual_shares'].append(share_entry)

            for account_id, client in current_twitter_clients.items():
                try:
                    use_reply = False
                    if twitter_reply_accounts is not None:
                        use_reply = account_id in twitter_reply_accounts
                    else:
                        use_reply = response_tweet
                    
                    tweet_content = {
                        'image_path': twitter_image,
                        'text': formatted_text,
                        'reply_main_text': formatted_reply_main,
                        'reply_text': formatted_reply,
                        'url': stream_info.get('url', ''),
                        'use_response': use_reply
                    }
                    
                    tweet_id = await client.post_content(tweet_content)
                    post_ids['twitter'][account_id] = tweet_id
                    logger.info(f"Successfully posted to Twitter for account '{account_id}'")
                except Exception as e:
                    logger.error(f"Failed to post to Twitter account '{account_id}': {str(e)}")
                    error_msg = str(e)
                    if 'twitter_manual_shares' not in post_ids:
                        post_ids['twitter_manual_shares'] = []
                    
                    use_reply = False
                    if twitter_reply_accounts is not None:
                        use_reply = account_id in twitter_reply_accounts
                    else:
                        use_reply = response_tweet
                    
                    share_entry = {
                        'account_id': account_id,
                        'image_filename': os.path.basename(twitter_image),
                        'url': stream_info.get('url', ''),
                        'error': error_msg
                    }
                    if use_reply:
                        share_entry['text'] = formatted_reply_main
                        share_entry['reply_text'] = formatted_reply
                    else:
                        share_entry['text'] = formatted_text
                        
                    post_ids['twitter_manual_shares'].append(share_entry)
    else:
        logger.info("No Twitter accounts selected and manual share not requested, skipping Twitter processing")

    # ---------------------------------------------------------
    # TikTok Story manual share (guided deep-link handoff)
    # ---------------------------------------------------------
    if tiktok_manual:
        logger.info("TikTok Story manual share requested")
        if 'tiktok_manual_shares' not in post_ids:
            post_ids['tiktok_manual_shares'] = []
        post_ids['tiktok_manual_shares'].append({
            'account_id': 'Manual',
            'platform': 'tiktok_story',
            'image_filename': os.path.basename(tiktok_image) if tiktok_image else '',
            'url': stream_info.get('url', ''),
            'text': str(stream_info.get('title') or ''),
        })

    # ---------------------------------------------------------
    # Discord Processing
    # ---------------------------------------------------------
    discord_accounts_config = accounts_config.get('discord', {})
    target_discord_ids = []
    
    for account_id, account in discord_accounts_config.items():
        if account.get('enabled', True):
            if discord_accounts is not None and account_id not in discord_accounts:
                continue
            target_discord_ids.append(account_id)

    # Same fail-closed publish-grant check for Discord webhook destinations —
    # each webhook is its own grant-bound destination.
    if target_discord_ids:
        target_discord_ids = await _authorize_destinations(
            'discord', target_discord_ids, discord_accounts_config,
            destination_authorizer, post_ids,
        )

    if target_discord_ids:
        logger.info(f"Processing for {len(target_discord_ids)} Discord Webhook(s)")
        
        # Generate Image (16:9 like Twitter)
        discord_image = os.path.join(output_dir, f'discord_{username}_{timestamp}.jpg')
        await twit_proc.process_stream_image(stream_info, discord_image, force_16_9=True, is_discord=True)
        stream_info['generated_images'].append(discord_image)
        logger.info(f"Generated persistent Discord image: {discord_image}")
        
        # Format message text
        customizer_config = stream_info.get('customizer_config') or {}
        discord_text_template = customizer_config.get("discord_text_template", "{role} 🔴 {title}\n🕹️ {game}\n➡️ Ver en <{url}>")
        if "{url}" in discord_text_template and "<{url}>" not in discord_text_template:
            discord_text_template = discord_text_template.replace("{url}", "<{url}>")
        
        for account_id in target_discord_ids:
            try:
                account = discord_accounts_config[account_id]
                
                # Resolve role ping for this specific webhook
                ping_role = account.get('ping_role')
                formatted_role = ""
                if ping_role:
                    ping_role_str = str(ping_role).strip()
                    if ping_role_str.lower() in ("everyone", "@everyone"):
                        formatted_role = "@everyone"
                    elif ping_role_str.lower() in ("here", "@here"):
                        formatted_role = "@here"
                    elif ping_role_str.startswith("<") and ping_role_str.endswith(">"):
                        formatted_role = ping_role_str
                    elif ping_role_str.isdigit():
                        formatted_role = f"<@&{ping_role_str}>"
                    else:
                        formatted_role = ping_role_str
                
                # Interpolate {role} first
                webhook_template = discord_text_template.replace("{role}", formatted_role)
                if not formatted_role:
                    if webhook_template.startswith("\n"):
                        webhook_template = webhook_template.lstrip("\n")
                    webhook_template = webhook_template.strip()
                    
                formatted_text = format_template(webhook_template, stream_info, username)

                cache_key = f"{username}_{account_id}"
                if cache_key not in disc_clients:
                    logger.info(f"Initializing Discord client for '{account_id}'...")
                    client = discord_client(account_id, account)
                    await client.authenticate()
                    disc_clients[cache_key] = client
                    logger.info(f"Successfully initialized Discord Webhook client for '{account_id}'")
                    
                client = disc_clients[cache_key]
                embeds = build_discord_embeds(
                    stream_info,
                    username,
                    is_offline=False,
                    image_filename=os.path.basename(discord_image),
                    twitter_processor=twit_proc
                )
                content = {
                    'text': formatted_text,
                    'image_path': discord_image,
                    'stream_url': stream_info.get('url') or stream_info.get('stream_url'),
                    'language': stream_info.get('language', 'en')
                }
                if embeds:
                    content['embeds'] = embeds
                msg_id = await client.post_content(content)
                post_ids['discord'][account_id] = msg_id
                logger.info(f"Successfully posted to Discord Webhook '{account_id}'")
            except Exception as e:
                logger.error(f"Failed to post to Discord Webhook '{account_id}': {str(e)}")
    else:
        logger.info("No Discord Webhooks selected, skipping Discord processing")

    return post_ids, updated_sessions


async def delete_stream_posts(
    post_ids: Dict[str, Dict[str, str]],
    instagram_clients: Optional[Dict[str, Any]] = None,
    twitter_clients: Optional[Dict[str, Any]] = None,
    discord_clients: Optional[Dict[str, Any]] = None
) -> None:
    """Delete stream posts from all platforms and accounts."""
    from ..core.context import get_user
    username = get_user() or "unknown"
    insta_clients = instagram_clients if instagram_clients is not None else {}
    twit_clients = twitter_clients if twitter_clients is not None else {}
    disc_clients = discord_clients if discord_clients is not None else {}
    cfg = Config()
    cfg_accounts = {
        'instagram': cfg.get('instagram_accounts', {}),
        'twitter': cfg.get('twitter_accounts', {}),
        'discord': cfg.get('discord_accounts', {}),
    }

    # Delete Instagram stories
    for account_id, story_id in post_ids.get('instagram', {}).items():
        cache_key = f"{username}_{account_id}"
        if cache_key not in insta_clients:
            try:
                account = (cfg_accounts.get('instagram') or {}).get(account_id)
                if account:
                    client = InstagramClient(account_id, account)
                    await client.authenticate()
                    insta_clients[cache_key] = client
            except Exception as e:
                logger.warning(f"Could not initialize Instagram client for '{account_id}': {e}")

        if cache_key in insta_clients:
            try:
                await insta_clients[cache_key].delete_story(story_id)
                logger.info(f"Deleted Instagram story {story_id} for account '{account_id}'")
            except Exception as e:
                logger.error(f"Failed to delete Instagram story {story_id} for account '{account_id}': {str(e)}")

    # Delete tweets with retry logic
    for account_id, tweet_id in post_ids.get('twitter', {}).items():
        cache_key = f"{username}_{account_id}"
        if cache_key not in twit_clients:
            try:
                account = (cfg_accounts.get('twitter') or {}).get(account_id)
                if account:
                    client = TwitterClient(account_id, credentials=account)
                    await client.authenticate()
                    twit_clients[cache_key] = client
            except Exception as e:
                logger.warning(f"Could not initialize Twitter client for '{account_id}': {e}")

        if cache_key in twit_clients:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await twit_clients[cache_key].delete_tweet(tweet_id)
                    logger.info(f"Deleted tweet {tweet_id} for account '{account_id}'")
                    break
                except Exception as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"Failed to delete tweet {tweet_id} for account '{account_id}' (attempt {attempt+1}/{max_retries}): {str(e)}. Retrying...")
                        await asyncio.sleep(2)
                    else:
                        logger.error(f"Failed to delete tweet {tweet_id} for account '{account_id}' after {max_retries} attempts: {str(e)}")

    # Delete Discord posts
    for account_id, message_id in post_ids.get('discord', {}).items():
        try:
            cache_key = f"{username}_{account_id}"
            if cache_key not in disc_clients:
                try:
                    account = (cfg_accounts.get('discord') or {}).get(account_id)
                    if account:
                        client = discord_client(account_id, account)
                        await client.authenticate()
                        disc_clients[cache_key] = client
                except Exception as e:
                    logger.warning(f"Could not initialize Discord client for '{account_id}': {e}")
            
            if cache_key in disc_clients:
                await disc_clients[cache_key].delete_post(message_id)
                logger.info(f"Deleted Discord message {message_id} for account '{account_id}'")
        except Exception as e:
            logger.error(f"Failed to delete Discord message {message_id} for account '{account_id}': {str(e)}")
