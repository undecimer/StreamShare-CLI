import os
import json
import logging
import asyncio
from typing import Dict, Any, Optional, List, Callable, Tuple
from ..core.config import Config
from ..core.exceptions import PlatformError
from ..platforms.sources.igdb import IGDBClient
from ..media.instagram import InstagramImageProcessor
from ..media.twitter import TwitterImageProcessor
from .sources import AuthorizedStreamSource, get_source
from ..platforms.sources.orientation import normalize_live_frame_orientation, require_native_live_frame
from .images import generate_local_images
from .publisher import share_stream_posts
from .monitor import monitor_stream

logger = logging.getLogger(__name__)


async def share_stream(
    identifier: Optional[str] = None,
    use_boxart: bool = False,
    use_keyart: bool = False,
    use_live_frame: bool = False,
    live_frame_orientation: str = 'auto',
    crop_anchor: str = "center",
    source_aspect_ratio: str = "auto",
    local: bool = False,
    response: bool = False,
    instagram_accounts: Optional[List[str]] = None,
    twitter_accounts: Optional[List[str]] = None,
    discord_accounts: Optional[List[str]] = None,
    platform: Optional[str] = None,
    accounts_config: Optional[Dict[str, Any]] = None,
    preserve_end_posts: bool = False,
    preserve_category_posts: bool = False,
    preserve_end_accounts: Optional[Dict[str, List[str]]] = None,
    preserve_category_accounts: Optional[Dict[str, List[str]]] = None,
    update_end_accounts: Optional[Dict[str, List[str]]] = None,
    update_category_accounts: Optional[Dict[str, List[str]]] = None,
    no_monitor: bool = False,
    stop_event: Optional[asyncio.Event] = None,
    on_event: Optional[Callable] = None,
    user_settings: Optional[Dict[str, Any]] = None,
    twitter_manual: bool = False,
    tiktok_manual: bool = False,
    twitter_reply_accounts: Optional[List[str]] = None,
    monitor_only: bool = False,
    config: Optional[Config] = None,
    igdb_client: Optional[IGDBClient] = None,
    instagram_clients: Optional[Dict[str, Any]] = None,
    twitter_clients: Optional[Dict[str, Any]] = None,
    discord_clients: Optional[Dict[str, Any]] = None,
    instagram_processor: Optional[InstagramImageProcessor] = None,
    twitter_processor: Optional[TwitterImageProcessor] = None,
    source: Optional[Any] = None,
    monitor_fn: Optional[Callable] = None,
    source_authorizer: Optional[Callable] = None,
    destination_authorizer: Optional[Callable] = None
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Main orchestrator for sharing and monitoring a stream."""
    cfg = config or Config()
    live_frame_orientation = normalize_live_frame_orientation(live_frame_orientation)
    actual_monitor = monitor_fn or monitor_stream
    insta_proc = instagram_processor or InstagramImageProcessor()
    twit_proc = twitter_processor or TwitterImageProcessor()
    insta_clients = instagram_clients if instagram_clients is not None else {}
    twit_clients = twitter_clients if twitter_clients is not None else {}
    disc_clients = discord_clients if discord_clients is not None else {}

    try:
        if not identifier:
            raise PlatformError("No stream identifier provided")

        if source is None:
            source = get_source(cfg, platform, user_settings)
        if source_authorizer is not None and not isinstance(source, AuthorizedStreamSource):
            source = AuthorizedStreamSource(source, source_authorizer)
        name = source.parse_identifier(identifier)
        if not name:
            raise PlatformError(f"Could not parse a valid username from identifier: '{identifier}'")

        should_skip_monitor = no_monitor
        is_offline_start = False

        if hasattr(source, 'get_stream_info_with_options'):
            stream_info = await source.get_stream_info_with_options(name, use_live_frame=use_live_frame, live_frame_orientation=live_frame_orientation)
        else:
            stream_info = await source.get_stream_info(name)

        if not stream_info:
            if not local and not should_skip_monitor:
                is_offline_start = True
                fallback_url = ""
                raw_plat = str(platform) if platform and isinstance(platform, str) else 'twitch'
                target_plat = raw_plat.lower()
                if target_plat == 'twitch':
                    fallback_url = f"https://twitch.tv/{name}"
                elif target_plat == 'tiktok':
                    fallback_url = f"https://www.tiktok.com/@{name}/live"
                elif target_plat == 'youtube':
                    fallback_url = f"https://www.youtube.com/@{name}/live" if name.startswith('@') else f"https://www.youtube.com/channel/{name}/live"

                stream_info = {
                    'platform': target_plat,
                    'identifier': name,
                    'title': name,
                    'game_name': 'Offline',

                    'viewer_count': 0,
                    'url': fallback_url,
                    'is_offline_start': True
                }
            else:
                raise PlatformError(f"Stream not found or offline for '{name}' on {platform or 'default platform'}")

        # Inject flags into stream_info (plus requested_* immutable baselines so
        # transient ad-screen fallback on the initial check never overwrites the
        # user's chosen options for subsequent monitoring/category changes).
        stream_info['use_boxart'] = use_boxart
        stream_info['requested_use_boxart'] = use_boxart
        stream_info['requested_use_keyart'] = use_keyart
        stream_info['requested_use_live_frame'] = use_live_frame
        if stream_info.get('ad_fallback'):
            stream_info['use_keyart'] = True
            stream_info['use_live_frame'] = False
        else:
            stream_info['use_keyart'] = use_keyart
            stream_info['use_live_frame'] = use_live_frame
        stream_info['crop_anchor'] = crop_anchor
        stream_info['live_frame_orientation'] = live_frame_orientation

        # Delegated monitor-only work never renders share media — a missing
        # native frame must not kill the watcher before monitoring starts.
        if not is_offline_start and not monitor_only:
            require_native_live_frame(stream_info, platform)
        stream_info['source_aspect_ratio'] = source_aspect_ratio
        stream_info['language'] = user_settings.get('language', 'en') if user_settings else 'en'
        stream_info['no_monitor'] = should_skip_monitor

        # Resolve customizer config
        customizer_config = None
        if user_settings and 'customizer_config' in user_settings:
            customizer_config = user_settings['customizer_config']
        else:
            customizer_config = Config().get('customizer_config')


        if not customizer_config:
            customizer_config = {
                "twitter_text_template": "🔴 {title}\n\n🕹️ {game}\n\n➡️ Ver en {url}",
                "twitter_reply_main_template": "🔴 {title}\n\n🕹️ {game}",
                "twitter_reply_template": "➡️ Ver en {url}",
                "discord_text_template": "{role} 🔴 {title}\n🕹️ {game}\n➡️ Ver en <{url}>",
                "instagram_caption_template": "{title}",
                "instagram_story": {
                    "show_directo": True,
                    "directo_text": "DIRECTO",
                    "directo_font_size": 40,
                    "directo_y_offset": -100,
                    "directo_color": "#ffffff",
                    "show_category": True,
                    "category_font_size": 80,
                    "category_y_offset": 50,
                    "category_color": "#ffffff",
                    "show_top_band": True,
                    "show_link_sticker": True,
                    "link_sticker_y": 1280,
                    "show_logo": True,
                    "logo_y": 1488
                },
                "twitter_post": {
                    "show_directo": True,
                    "directo_text": "DIRECTO",
                    "directo_font_size": 40,
                    "directo_y_offset": -100,
                    "directo_color": "#ffffff",
                    "show_category": True,
                    "category_font_size": 80,
                    "category_y_offset": 20,
                    "category_color": "#ffffff",
                    "show_logo": True
                },
                "discord_post": {
                    "show_directo": True,
                    "directo_text": "DIRECTO",
                    "directo_font_size": 40,
                    "directo_y_offset": -100,
                    "directo_color": "#ffffff",
                    "show_category": True,
                    "category_font_size": 80,
                    "category_y_offset": 20,
                    "category_color": "#ffffff",
                    "show_logo": True
                },
                "discord_embed": {
                    "enabled": True,
                    "color": "#5865f2",
                    "title_template": "{username} is LIVE!",
                    "description_template": "{title}",
                    "offline_title_template": "{username} was live",
                    "offline_description_template": "Thanks for watching! The stream has ended.",
                    "footer_template": "Last online • {time}",
                    "show_fields": ["game", "views", "duration"]
                }
            }
        else:
            if "discord_text_template" not in customizer_config:
                customizer_config["discord_text_template"] = "{role} 🔴 {title}\n🕹️ {game}\n➡️ Ver en <{url}>"
            if "discord_post" not in customizer_config:
                customizer_config["discord_post"] = {
                    "show_directo": True,
                    "directo_text": "DIRECTO",
                    "directo_font_size": 40,
                    "directo_y_offset": -100,
                    "directo_color": "#ffffff",
                    "show_category": True,
                    "category_font_size": 80,
                    "category_y_offset": 20,
                    "category_color": "#ffffff",
                    "show_logo": True
                }
            if "discord_embed" not in customizer_config:
                customizer_config["discord_embed"] = {
                    "enabled": True,
                    "color": "#5865f2",
                    "title_template": "{username} is LIVE!",
                    "description_template": "{title}",
                    "offline_title_template": "{username} was live",
                    "offline_description_template": "Thanks for watching! The stream has ended.",
                    "footer_template": "Last online • {time}",
                    "show_fields": ["game", "views", "duration"]
                }
            else:
                de = customizer_config["discord_embed"]
                if not isinstance(de, dict):
                    de = {}
                de.setdefault("enabled", True)
                de.setdefault("color", "#5865f2")
                de.setdefault("title_template", "{username} is LIVE!")
                de.setdefault("description_template", "{title}")
                de.setdefault("offline_title_template", "{username} was live")
                de.setdefault("offline_description_template", "Thanks for watching! The stream has ended.")
                de.setdefault("footer_template", "Last online • {time}")
                de.setdefault("show_fields", ["game", "views", "duration"])
                customizer_config["discord_embed"] = de

        stream_info['customizer_config'] = customizer_config

        # Generate images locally
        if local:
            await generate_local_images(
                stream_info,
                igdb_client=igdb_client,
                instagram_processor=insta_proc,
                twitter_processor=twit_proc,
                crop_anchor=crop_anchor,
                source_aspect_ratio=source_aspect_ratio
            )
            if on_event:
                await on_event("local_generation", stream_info, {})
            return {}, {}

        # Share posts
        post_ids = {}
        updated_sessions = {}
        if not is_offline_start:
            if not monitor_only:
                post_ids, updated_sessions = await share_stream_posts(
                    stream_info,
                    instagram_accounts=instagram_accounts,
                    twitter_accounts=twitter_accounts,
                    discord_accounts=discord_accounts,
                    accounts_config=accounts_config,
                    response_tweet=response,
                    twitter_manual=twitter_manual,
                    tiktok_manual=tiktok_manual,
                    twitter_reply_accounts=twitter_reply_accounts,
                    config=cfg,
                    instagram_clients=insta_clients,
                    twitter_clients=twit_clients,
                    discord_clients=disc_clients,
                    instagram_processor=insta_proc,
                    twitter_processor=twit_proc,
                    igdb_client=igdb_client,
                    destination_authorizer=destination_authorizer
                )
                if post_ids.get('destination_errors'):
                    logger.warning(
                        "Stream share finished with %d denied/failed destination(s)",
                        len(post_ids['destination_errors']),
                    )
                else:
                    logger.info("Stream shared successfully!")
            else:
                logger.info("Stream is live (surveillance-only mode: social sharing skipped).")

            if on_event:
                await on_event("initial_share", stream_info, post_ids)
        else:
            logger.info("Stream is offline. Skipping initial share posts. Entering wait status...")
            if on_event:
                await on_event("waiting_for_stream", stream_info)

        has_api_posts = False
        if post_ids:
            has_api_posts = any(post_ids.get(plat) for plat in ['instagram', 'twitter', 'discord'])

        has_manual_shares = False
        if post_ids:
            has_manual_shares = bool(post_ids.get('twitter_manual_shares') or post_ids.get('tiktok_manual_shares'))

        # Monitor stream for changes
        if not local and (has_api_posts or has_manual_shares or is_offline_start or monitor_only) and not should_skip_monitor:
            await actual_monitor(
                source=source,
                stream_info=stream_info,
                post_ids=post_ids,
                preserve_end_posts=preserve_end_posts,
                preserve_category_posts=preserve_category_posts,
                preserve_end_accounts=preserve_end_accounts,
                preserve_category_accounts=preserve_category_accounts,
                update_end_accounts=update_end_accounts,
                update_category_accounts=update_category_accounts,
                stop_event=stop_event,
                on_event=on_event,
                instagram_accounts=instagram_accounts,
                twitter_accounts=twitter_accounts,
                discord_accounts=discord_accounts,
                accounts_config=accounts_config,
                response_tweet=response,
                twitter_manual=twitter_manual,
                tiktok_manual=tiktok_manual,
                twitter_reply_accounts=twitter_reply_accounts,
                monitor_only=monitor_only,
                igdb_client=igdb_client,
                instagram_clients=insta_clients,
                twitter_clients=twit_clients,
                discord_clients=disc_clients,
                instagram_processor=insta_proc,
                twitter_processor=twit_proc,
                config=cfg,
                destination_authorizer=destination_authorizer
            )
        elif should_skip_monitor:
            logger.info("Monitoring skipped (--no-monitor enabled)")

        return post_ids, updated_sessions

    except Exception as e:
        logger.error(f"Error sharing stream: {str(e)}")
        raise
