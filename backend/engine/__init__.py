import argparse
from typing import Optional, Dict, Any, List, Tuple, Callable
import asyncio
from ..core.config import Config
from ..platforms.sources.igdb import IGDBClient
from ..platforms.twitter.client import TwitterClient
from ..platforms.instagram.client import InstagramClient
from ..platforms.discord.client import DiscordWebhookClient
DiscordClient = DiscordWebhookClient
from ..media.instagram import InstagramImageProcessor
from ..media.twitter import TwitterImageProcessor
from ..platforms.sources.base import StreamSource
from .templates import format_template
from .sources import get_source
from .images import cleanup_old_images, generate_local_images
from .discord_embeds import build_discord_embeds
from .publisher import share_stream_posts, delete_stream_posts
from .monitor import StreamMonitor, monitor_stream
from .share import share_stream
from .local_auth import local_destination_authorizer


class StreamShare:
    """Facade for StreamShare engine maintaining full backward compatibility."""

    def __init__(self, args: Optional[argparse.Namespace] = None):
        self.config = Config()
        default_source = self.config.get('default_source') or {}
        self.default_platform = default_source.get('platform', 'twitch')
        self.instagram_clients: Dict[str, Any] = {}
        self.twitter_clients: Dict[str, Any] = {}
        self.discord_clients: Dict[str, Any] = {}
        self.instagram_processor = InstagramImageProcessor()
        self.twitter_processor = TwitterImageProcessor()
        from ..media.tiktok import TikTokStoryImageProcessor
        self.tiktok_processor = TikTokStoryImageProcessor()
        twitch_config = self.config.get('twitch') or {}
        self.igdb_client = IGDBClient(
            twitch_config.get('client_id', ''),
            twitch_config.get('client_secret', '')
        )
        self.args = args

    def format_template(self, template: str, stream_info: Dict[str, Any], username: str) -> str:
        return format_template(template, stream_info, username)

    def get_source(self, platform: Optional[str] = None, user_settings: Optional[Dict[str, Any]] = None) -> StreamSource:
        return get_source(self.config, platform, user_settings, default_platform=self.default_platform)

    def cleanup_old_images(self, days: int = 7) -> None:
        cleanup_old_images(days)

    async def generate_local_images(self, stream_info: Dict[str, Any], crop_anchor: str = "center", source_aspect_ratio: str = "auto") -> None:
        await generate_local_images(
            stream_info,
            igdb_client=self.igdb_client,
            instagram_processor=self.instagram_processor,
            twitter_processor=self.twitter_processor,
            crop_anchor=crop_anchor,
            source_aspect_ratio=source_aspect_ratio
        )

    def build_discord_embeds(
        self,
        stream_info: Dict[str, Any],
        username: str,
        is_offline: bool = False,
        is_category_change: bool = False,
        next_game: Optional[str] = None,
        image_filename: Optional[str] = None
    ) -> Optional[List[Dict[str, Any]]]:
        return build_discord_embeds(
            stream_info=stream_info,
            username=username,
            is_offline=is_offline,
            is_category_change=is_category_change,
            next_game=next_game,
            image_filename=image_filename,
            twitter_processor=self.twitter_processor
        )

    async def share_stream_posts(
        self,
        stream_info: Dict[str, Any],
        instagram_accounts: Optional[List[str]] = None,
        twitter_accounts: Optional[List[str]] = None,
        discord_accounts: Optional[List[str]] = None,
        accounts_config: Optional[Dict[str, Any]] = None,
        response_tweet: bool = False,
        twitter_manual: bool = False,
        tiktok_manual: bool = False,
        twitter_reply_accounts: Optional[List[str]] = None,
        **kwargs
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        cfg = kwargs.get('config') or self.config
        insta_clients = kwargs.get('instagram_clients') if kwargs.get('instagram_clients') is not None else self.instagram_clients
        twit_clients = kwargs.get('twitter_clients') if kwargs.get('twitter_clients') is not None else self.twitter_clients
        disc_clients = kwargs.get('discord_clients') if kwargs.get('discord_clients') is not None else self.discord_clients
        insta_proc = kwargs.get('instagram_processor') or self.instagram_processor
        twit_proc = kwargs.get('twitter_processor') or self.twitter_processor
        igdb = kwargs.get('igdb_client') or self.igdb_client

        return await share_stream_posts(
            stream_info=stream_info,
            instagram_accounts=instagram_accounts,
            twitter_accounts=twitter_accounts,
            discord_accounts=discord_accounts,
            accounts_config=accounts_config,
            response_tweet=response_tweet,
            twitter_manual=twitter_manual,
            tiktok_manual=tiktok_manual,
            twitter_reply_accounts=twitter_reply_accounts,
            config=cfg,
            instagram_clients=insta_clients,
            twitter_clients=twit_clients,
            discord_clients=disc_clients,
            instagram_processor=insta_proc,
            twitter_processor=twit_proc,
            tiktok_processor=kwargs.get('tiktok_processor') or self.tiktok_processor,
            igdb_client=igdb,
            destination_authorizer=kwargs.get('destination_authorizer') or local_destination_authorizer
        )

    async def delete_stream_posts(self, post_ids: Dict[str, Dict[str, str]], **kwargs) -> None:
        insta_clients = kwargs.get('instagram_clients') if kwargs.get('instagram_clients') is not None else self.instagram_clients
        twit_clients = kwargs.get('twitter_clients') if kwargs.get('twitter_clients') is not None else self.twitter_clients
        disc_clients = kwargs.get('discord_clients') if kwargs.get('discord_clients') is not None else self.discord_clients

        await delete_stream_posts(
            post_ids=post_ids,
            instagram_clients=insta_clients,
            twitter_clients=twit_clients,
            discord_clients=disc_clients
        )


    async def monitor_stream(
        self,
        source: Any,
        stream_info: Dict[str, Any],
        post_ids: Dict[str, Dict[str, str]],
        preserve_end_posts: bool = False,
        preserve_category_posts: bool = False,
        preserve_end_accounts: Optional[Dict[str, List[str]]] = None,
        preserve_category_accounts: Optional[Dict[str, List[str]]] = None,
        update_end_accounts: Optional[Dict[str, List[str]]] = None,
        update_category_accounts: Optional[Dict[str, List[str]]] = None,
        stop_event: Optional[asyncio.Event] = None,
        on_event: Optional[Callable] = None,
        instagram_accounts: Optional[List[str]] = None,
        twitter_accounts: Optional[List[str]] = None,
        discord_accounts: Optional[List[str]] = None,
        accounts_config: Optional[Dict[str, Any]] = None,
        response_tweet: bool = False,
        twitter_manual: bool = False,
        tiktok_manual: bool = False,
        twitter_reply_accounts: Optional[List[str]] = None,
        monitor_only: bool = False,
        **kwargs
    ) -> None:
        should_preserve_end = preserve_end_posts or (self.args and self.args.preserve_end_posts)
        should_preserve_cat = preserve_category_posts or (self.args and self.args.preserve_category_posts)
        igdb = kwargs.get('igdb_client') or self.igdb_client
        insta_clients = kwargs.get('instagram_clients') if kwargs.get('instagram_clients') is not None else self.instagram_clients
        twit_clients = kwargs.get('twitter_clients') if kwargs.get('twitter_clients') is not None else self.twitter_clients
        disc_clients = kwargs.get('discord_clients') if kwargs.get('discord_clients') is not None else self.discord_clients
        insta_proc = kwargs.get('instagram_processor') or self.instagram_processor
        twit_proc = kwargs.get('twitter_processor') or self.twitter_processor
        cfg = kwargs.get('config') or self.config

        await monitor_stream(
            source=source,
            stream_info=stream_info,
            post_ids=post_ids,
            preserve_end_posts=should_preserve_end,
            preserve_category_posts=should_preserve_cat,
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
            response_tweet=response_tweet,
            twitter_manual=twitter_manual,
            tiktok_manual=tiktok_manual,
            twitter_reply_accounts=twitter_reply_accounts,
            monitor_only=monitor_only,
            igdb_client=igdb,
            instagram_clients=insta_clients,
            twitter_clients=twit_clients,
            discord_clients=disc_clients,
            instagram_processor=insta_proc,
            twitter_processor=twit_proc,
            config=cfg,
            share_posts_fn=self.share_stream_posts,
            delete_posts_fn=self.delete_stream_posts,
            destination_authorizer=kwargs.get('destination_authorizer') or local_destination_authorizer
        )


    async def share_stream(
        self,
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
        source_authorizer: Optional[Callable] = None,
        destination_authorizer: Optional[Callable] = None
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        target_ident = identifier or (self.args.identifier if self.args else None)
        target_plat = platform or (self.args.platform if self.args else None)
        should_no_monitor = no_monitor or (self.args.no_monitor if self.args else False)
        return await share_stream(
            identifier=target_ident,
            use_boxart=use_boxart,
            use_keyart=use_keyart,
            use_live_frame=use_live_frame,
            live_frame_orientation=live_frame_orientation,
            crop_anchor=crop_anchor,
            source_aspect_ratio=source_aspect_ratio,
            local=local,
            response=response,
            instagram_accounts=instagram_accounts,
            twitter_accounts=twitter_accounts,
            discord_accounts=discord_accounts,
            platform=target_plat,
            accounts_config=accounts_config,
            preserve_end_posts=preserve_end_posts,
            preserve_category_posts=preserve_category_posts,
            preserve_end_accounts=preserve_end_accounts,
            preserve_category_accounts=preserve_category_accounts,
            update_end_accounts=update_end_accounts,
            update_category_accounts=update_category_accounts,
            no_monitor=should_no_monitor,
            stop_event=stop_event,
            on_event=on_event,
            user_settings=user_settings,
            twitter_manual=twitter_manual,
            tiktok_manual=tiktok_manual,
            twitter_reply_accounts=twitter_reply_accounts,
            monitor_only=monitor_only,
            config=self.config,
            igdb_client=self.igdb_client,
            instagram_clients=self.instagram_clients,
            twitter_clients=self.twitter_clients,
            discord_clients=self.discord_clients,
            instagram_processor=self.instagram_processor,
            twitter_processor=self.twitter_processor,
            source=self.get_source(target_plat, user_settings) if hasattr(self, 'get_source') else None,
            monitor_fn=self.monitor_stream,
            source_authorizer=source_authorizer,
            destination_authorizer=destination_authorizer or local_destination_authorizer
        )




__all__ = [
    "StreamShare",
    "StreamMonitor",
    "format_template",
    "get_source",
    "cleanup_old_images",
    "generate_local_images",
    "build_discord_embeds",
    "share_stream_posts",
    "delete_stream_posts",
    "monitor_stream",
    "share_stream",
]
