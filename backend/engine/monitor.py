import os
import logging
import asyncio
from datetime import datetime
from io import BytesIO
from typing import Dict, Any, Optional, List, Callable
from ..core.config import Config
from ..platforms.discord.client import DiscordWebhookClient
from ..platforms.discord.factory import discord_client
from ..platforms.instagram.client import InstagramClient
from ..platforms.sources.igdb import IGDBClient
from ..media.instagram import InstagramImageProcessor
from ..media.twitter import TwitterImageProcessor
from .templates import format_template
from .discord_embeds import build_discord_embeds
from .publisher import share_stream_posts, delete_stream_posts, _authorize_destinations
from ..platforms.sources.orientation import require_native_live_frame
from .observations import (
    PROVIDER_POLL_FAILED_KEY,
    StreamObservationBacklogFull,
)

logger = logging.getLogger(__name__)


class StreamMonitor:
    """Stateful stream monitor that tracks category changes, stream end, and post updates."""

    def __init__(
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
        igdb_client: Optional[IGDBClient] = None,
        instagram_clients: Optional[Dict[str, Any]] = None,
        twitter_clients: Optional[Dict[str, Any]] = None,
        discord_clients: Optional[Dict[str, Any]] = None,
        instagram_processor: Optional[InstagramImageProcessor] = None,
        twitter_processor: Optional[TwitterImageProcessor] = None,
        config: Optional[Config] = None,
        share_posts_fn: Optional[Callable] = None,
        delete_posts_fn: Optional[Callable] = None,
        destination_authorizer: Optional[Callable] = None
    ):
        self.source = source
        self.stream_info = stream_info
        self.post_ids = post_ids
        self.preserve_end_posts = preserve_end_posts
        self.preserve_category_posts = preserve_category_posts
        self.preserve_end_accounts = preserve_end_accounts
        self.preserve_category_accounts = preserve_category_accounts
        self.update_end_accounts = update_end_accounts
        self.update_category_accounts = update_category_accounts
        self.stop_event = stop_event
        self.on_event = on_event
        self.instagram_accounts = instagram_accounts
        self.twitter_accounts = twitter_accounts
        self.discord_accounts = discord_accounts
        self.accounts_config = accounts_config
        self.response_tweet = response_tweet
        self.twitter_manual = twitter_manual
        self.tiktok_manual = tiktok_manual
        self.twitter_reply_accounts = twitter_reply_accounts
        self.monitor_only = monitor_only

        self.igdb_client = igdb_client
        self.instagram_clients = instagram_clients if instagram_clients is not None else {}
        self.twitter_clients = twitter_clients if twitter_clients is not None else {}
        self.discord_clients = discord_clients if discord_clients is not None else {}
        self.instagram_processor = instagram_processor or InstagramImageProcessor()
        self.twitter_processor = twitter_processor or TwitterImageProcessor()
        self.config = config or Config()
        self.share_posts_fn = share_posts_fn or share_stream_posts
        self.delete_posts_fn = delete_posts_fn or delete_stream_posts
        # Per-destination publish-grant authorizer — forwarded into every
        # share_posts_fn call so reposts/updates created by monitoring stay
        # under fresh per-destination authorization (fail closed when None).
        self.destination_authorizer = destination_authorizer

        # Dynamic interval controls
        self.check_interval = 300
        self.max_interval = 600
        self.min_interval = 300
        self.backoff_factor = 1.5

        # Snapshot the user's configured launcher options at monitor start so
        # periodic checks (e.g. when no Discord webhooks are configured) or
        # transient ad-screen fallbacks can never overwrite or lose them before
        # a later category change.
        self._base_options = {
            'use_keyart': bool(stream_info.get('requested_use_keyart', stream_info.get('use_keyart', False))),
            'use_boxart': bool(stream_info.get('requested_use_boxart', stream_info.get('use_boxart', False))),
            'use_live_frame': bool(stream_info.get('requested_use_live_frame', stream_info.get('use_live_frame', False))),
            'crop_anchor': stream_info.get('crop_anchor', 'center'),
            'live_frame_orientation': stream_info.get('live_frame_orientation', 'auto'),
            'source_aspect_ratio': stream_info.get('source_aspect_ratio', 'auto'),
            'language': stream_info.get('language', 'en'),
            'customizer_config': stream_info.get('customizer_config'),
        }

    def _source_query_info(self) -> Dict[str, Any]:
        """Build the stream_info dict passed to source.get_current_stream_info,
        always using the user's originally requested use_live_frame / use_keyart
        options rather than a transient ad_fallback override from the last tick."""
        info = dict(self.stream_info)
        info['use_keyart'] = self._base_options['use_keyart']
        info['use_boxart'] = self._base_options['use_boxart']
        info['use_live_frame'] = self._base_options['use_live_frame']
        info['crop_anchor'] = self._base_options['crop_anchor']
        info['live_frame_orientation'] = self._base_options['live_frame_orientation']
        info['source_aspect_ratio'] = self._base_options['source_aspect_ratio']
        info['language'] = self._base_options['language']
        info['customizer_config'] = self._base_options['customizer_config']
        return info

    def _apply_configured_options(self, current_info: Dict[str, Any]) -> None:
        """Apply the user's configured launcher options onto a newly polled
        current_info dict, honoring ad_fallback only for the current tick."""
        if current_info.get('ad_fallback'):
            current_info['use_keyart'] = True
            current_info['use_live_frame'] = False
        else:
            current_info['use_keyart'] = self._base_options['use_keyart']
            current_info['use_live_frame'] = self._base_options['use_live_frame']
        current_info['use_boxart'] = self._base_options['use_boxart']
        current_info['crop_anchor'] = self._base_options['crop_anchor']
        current_info['live_frame_orientation'] = self._base_options['live_frame_orientation']
        current_info['source_aspect_ratio'] = self._base_options['source_aspect_ratio']
        current_info['language'] = self._base_options['language']
        current_info['customizer_config'] = self._base_options['customizer_config']
        current_info['requested_use_keyart'] = self._base_options['use_keyart']
        current_info['requested_use_boxart'] = self._base_options['use_boxart']
        current_info['requested_use_live_frame'] = self._base_options['use_live_frame']

    async def run(self) -> None:
        """Run the main monitor lifecycle loop."""
        try:
            platform_name = str(self.stream_info.get('platform') or getattr(self.source, 'platform', 'stream')).upper()
            logger.info(f"Starting [{platform_name}] stream monitoring for {self.stream_info.get('url')}")
            logger.info("Will check stream status every 5 minutes")
            logger.info("Posts being monitored:")
            for platform, accounts in self.post_ids.items():
                if isinstance(accounts, dict):
                    for account_id, post_id in accounts.items():
                        logger.info(f"  - {platform.capitalize()}: Account '{account_id}' -> Post ID {post_id}")

            if self.preserve_end_posts:
                logger.info("Stream end cleanup is disabled")
            if self.preserve_category_posts:
                logger.info("Category change cleanup is disabled")

            while True:
                if self.stop_event and self.stop_event.is_set():
                    logger.info("Stop signal received. Stopping monitoring...")
                    break

                # 1. Waiting for stream if offline at start
                if self.stream_info.get('is_offline_start', False):
                    live = await self._wait_for_stream_online(platform_name)
                    if not live:
                        break

                # 2. Main polling loop while stream is live
                stream_ended = await self._poll_live_loop()

                if self.stop_event and self.stop_event.is_set():
                    break

                # 3. Stream end handling & transition back to waiting
                if stream_ended:
                    await self._handle_stream_end()

            # Final cleanup after loop exit (if manually stopped)
            await self._cleanup_on_stop()

        except asyncio.CancelledError:
            logger.info("Monitoring cancelled")
            raise
        except Exception as e:
            logger.error(f"Error monitoring stream: {str(e)}")
            raise

    async def _wait_for_stream_online(self, platform_name: str) -> bool:
        """Offline waiting loop before stream goes live."""
        logger.info(f"[{platform_name}] Stream '{self.stream_info.get('identifier')}' is offline at start. Entering waiting loop...")
        offline_check_interval = 120
        current_info = None

        while True:
            if self.stop_event and self.stop_event.is_set():
                logger.info("Stop signal received during waiting state. Stopping monitoring...")
                return False

            logger.info(f"Checking if [{platform_name}] stream '{self.stream_info.get('identifier')}' has gone live...")
            poll_failed = False
            try:
                query_info = self._source_query_info()
                if hasattr(self.source, 'get_current_stream_info'):
                    current_info = await self.source.get_current_stream_info(query_info)
                else:
                    is_live = await self.source.is_stream_live(query_info)
                    current_info = query_info if is_live else None
            except StreamObservationBacklogFull:
                # Backpressure stop: the producer must halt, never be
                # swallowed into a fabricated 'waiting_for_stream' state.
                raise
            except Exception as poll_err:
                logger.error(f"Error checking if stream is live: {poll_err}")
                current_info = None
                poll_failed = True

            if not current_info:
                logger.info(f"Stream is not live: {self.stream_info.get('identifier')}")
                if self.on_event:
                    try:
                        # A failed provider poll produced no state: mark the
                        # emitted copy so the observation layer never derives
                        # an offline fact from it. Local history keeps the
                        # waiting row exactly as before.
                        waiting_info = (
                            dict(self.stream_info, **{PROVIDER_POLL_FAILED_KEY: True})
                            if poll_failed else self.stream_info
                        )
                        await self.on_event("waiting_for_stream", waiting_info)
                    except Exception as ev_err:
                        logger.debug(f"Error calling on_event in waiting state: {ev_err}")

            if current_info:
                self._apply_configured_options(current_info)
                if not self.monitor_only:
                    require_native_live_frame(current_info, platform_name)

                    new_post_ids, _ = await self.share_posts_fn(
                        current_info,
                        self.instagram_accounts,
                        self.twitter_accounts,
                        self.discord_accounts,
                        self.accounts_config,
                        response_tweet=self.response_tweet,
                        twitter_manual=self.twitter_manual,
                        tiktok_manual=self.tiktok_manual,
                        twitter_reply_accounts=self.twitter_reply_accounts,
                        config=self.config,
                        instagram_clients=self.instagram_clients,
                        twitter_clients=self.twitter_clients,
                        discord_clients=self.discord_clients,
                        instagram_processor=self.instagram_processor,
                        twitter_processor=self.twitter_processor,
                        igdb_client=self.igdb_client,
                        destination_authorizer=self.destination_authorizer
                    )
                    self.post_ids.clear()
                    self.post_ids.update(new_post_ids)
                else:
                    logger.info(f"[{platform_name}] Stream '{self.stream_info.get('identifier')}' has gone live (surveillance-only mode: social sharing skipped).")
                    self.post_ids.clear()
                    new_post_ids = {}

                self.stream_info = current_info
                if self.on_event:
                    await self.on_event("initial_share", current_info, new_post_ids)
                return True

            if self.stop_event:
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=offline_check_interval)
                    logger.info("Stop signal received during waiting state. Stopping monitoring...")
                    return False
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(offline_check_interval)

        return False

    async def _poll_live_loop(self) -> bool:
        """Main live polling loop. Returns True if stream ended, False if stopped."""
        self.check_interval = 300

        while True:
            if self.stop_event and self.stop_event.is_set():
                logger.info("Stop signal received. Stopping monitoring...")
                return False

            logger.info("Check: Checking stream status...")

            if hasattr(self.source, 'get_current_stream_info'):
                current_info = await self.source.get_current_stream_info(self._source_query_info())
                if not current_info:
                    logger.info("Stream has ended")
                    if self.on_event:
                        await self.on_event("stream_end", self.stream_info, None)
                    return True

                # Check for category change
                if current_info['game_name'] != self.stream_info['game_name']:
                    await self._handle_category_change(current_info)
                else:
                    await self._handle_periodic_discord_updates(current_info)

                logger.info(f"Stream is live playing {current_info['game_name']} with {current_info.get('viewer_count', 'unknown')} viewers")
            else:
                is_live = await self.source.is_stream_live(self.stream_info)
                if not is_live:
                    logger.info("Stream has ended (simple check)")
                    if self.on_event:
                        await self.on_event("stream_end", self.stream_info, None)
                    return True

            # Heartbeat: re-announce the live state every poll so OneSite-ID's
            # live_stream_channel.updated_at stays fresh. Writers that die
            # silently stop heartbeating and the identity-core sweep reaps
            # their stale is_live rows; transitions alone must not carry that
            # burden (a crash between initial_share and stream_end used to
            # leave the OneSite banner permanently on).
            if self.on_event:
                await self.on_event("live", self.stream_info)

            self._adapt_check_interval()

            if self.stop_event:
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=self.check_interval)
                    logger.info("Stop signal received during wait. Stopping monitoring...")
                    return False
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(self.check_interval)

    async def _handle_category_change(self, current_info: Dict[str, Any]) -> None:
        """Handle game/category change during live stream."""
        logger.info(f"Game changed from {self.stream_info['game_name']} to {current_info['game_name']}")
        posts_to_delete = {}
        discord_repost_offline_updates = []

        for platform, accounts in self.post_ids.items():
            if isinstance(accounts, dict):
                for account_id, post_id in accounts.items():
                    is_preserved = False
                    if self.preserve_category_accounts and platform in self.preserve_category_accounts:
                        if account_id in self.preserve_category_accounts[platform]:
                            is_preserved = True
                    if not self.preserve_category_accounts:
                        is_preserved = self.preserve_category_posts
                    is_updated = False
                    if platform == 'discord' and self.update_category_accounts and platform in self.update_category_accounts:
                        if account_id in self.update_category_accounts[platform]:
                            is_updated = True
                    if not is_preserved and not is_updated:
                        if platform == 'discord':
                            discord_repost_offline_updates.append(account_id)
                        else:
                            if platform not in posts_to_delete:
                                posts_to_delete[platform] = {}
                            posts_to_delete[platform][account_id] = post_id

        # Update Discord posts if requested
        discord_update_cats = []
        if self.update_category_accounts and 'discord' in self.update_category_accounts:
            for account_id in self.post_ids.get('discord', {}):
                if account_id in self.update_category_accounts['discord']:
                    discord_update_cats.append(account_id)

        if discord_update_cats:
            logger.info(f"Updating {len(discord_update_cats)} Discord Webhooks on category change")
            self._apply_configured_options(current_info)

            require_native_live_frame(current_info, current_info.get('platform'))

            if current_info.get('use_keyart'):
                if isinstance(current_info.get('key_art_url'), BytesIO):
                    current_info['cached_image'] = current_info['key_art_url']
                    current_info['key_art_url'] = 'file://cached'
                elif not current_info.get('key_art_url') and current_info.get('game_id') and self.igdb_client:
                    logger.info("Attempting to get key art from IGDB on category change update")
                    artwork = await self.igdb_client.get_game_artwork(
                        twitch_id=str(current_info['game_id']),
                        game_name=current_info['game_name'],
                        platform=current_info.get('platform')
                    )
                    if artwork:
                        if isinstance(artwork, BytesIO):
                            current_info['cached_image'] = artwork
                            current_info['key_art_url'] = 'file://cached'
                        else:
                            current_info['key_art_url'] = artwork

            from ..core.config import get_generated_images_dir
            output_dir = get_generated_images_dir()
            os.makedirs(output_dir, exist_ok=True)
            username = current_info.get('username') or current_info.get('user_name') or current_info.get('identifier')
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            discord_image = os.path.join(output_dir, f'discord_{username}_{timestamp}.jpg')
            await self.twitter_processor.process_stream_image(current_info, discord_image, force_16_9=True, is_discord=True)
            current_info.setdefault('generated_images', []).append(discord_image)

            customizer_config = current_info.get('customizer_config') or {}
            discord_text_template = customizer_config.get("discord_text_template", "{role} 🔴 {title}\n🕹️ {game}\n➡️ Ver en <{url}>")
            if "{url}" in discord_text_template and "<{url}>" not in discord_text_template:
                discord_text_template = discord_text_template.replace("{url}", "<{url}>")

            for account_id in discord_update_cats:
                try:
                    account = (self.accounts_config or {}).get('discord', {}).get(account_id, {})
                    ping_role = account.get('ping_role')
                    formatted_role = self._format_discord_role(ping_role)
                    webhook_template = discord_text_template.replace("{role}", formatted_role)
                    if not formatted_role and webhook_template.startswith("\n"):
                        webhook_template = webhook_template.lstrip("\n")
                    formatted_text = format_template(webhook_template.strip(), current_info, username)

                    embeds = build_discord_embeds(
                        current_info,
                        username,
                        is_offline=False,
                        image_filename=os.path.basename(discord_image),
                        twitter_processor=self.twitter_processor
                    )

                    client = await self._get_discord_client(account_id, account, username)
                    content = {
                        'text': formatted_text,
                        'image_path': discord_image,
                        'stream_url': current_info.get('url') or current_info.get('stream_url'),
                        'language': current_info.get('language', 'en'),
                        'embeds': embeds
                    }
                    msg_id = self.post_ids['discord'][account_id]
                    await client.update_content(msg_id, content)
                    logger.info(f"Successfully updated Discord Webhook '{account_id}' on category change")
                except Exception as update_err:
                    logger.error(f"Failed to update Discord Webhook '{account_id}' on category change: {update_err}")

        # Category transition updates for reposted Discord webhooks
        if discord_repost_offline_updates:
            await self._update_discord_reposts_offline(discord_repost_offline_updates, current_info)

        if posts_to_delete:
            logger.info("Deleting old posts...")
            await self.delete_posts_fn(
                posts_to_delete,
                instagram_clients=self.instagram_clients,
                twitter_clients=self.twitter_clients,
                discord_clients=self.discord_clients
            )
        else:
            logger.info("Keeping old posts due to preserve settings")

        logger.info("Creating new posts with updated game information...")
        self._apply_configured_options(current_info)

        require_native_live_frame(current_info, current_info.get('platform'))

        discord_repost_accounts = None
        if self.discord_accounts is not None:
            discord_repost_accounts = [
                acc_id for acc_id in self.discord_accounts
                if acc_id not in discord_update_cats
            ]

        new_post_ids, _ = await self.share_posts_fn(
            current_info,
            self.instagram_accounts,
            self.twitter_accounts,
            discord_repost_accounts,
            self.accounts_config,
            response_tweet=self.response_tweet,
            twitter_manual=self.twitter_manual,
            tiktok_manual=self.tiktok_manual,
            twitter_reply_accounts=self.twitter_reply_accounts,
            config=self.config,
            instagram_clients=self.instagram_clients,
            twitter_clients=self.twitter_clients,
            discord_clients=self.discord_clients,
            instagram_processor=self.instagram_processor,
            twitter_processor=self.twitter_processor,
            igdb_client=self.igdb_client,
            destination_authorizer=self.destination_authorizer
        )

        for platform, accounts in new_post_ids.items():
            if isinstance(accounts, dict):
                for account_id, post_id in accounts.items():
                    if platform not in self.post_ids:
                        self.post_ids[platform] = {}
                    self.post_ids[platform][account_id] = post_id

        self.stream_info = current_info
        logger.info("New posts created with updated game information")

        if self.on_event:
            await self.on_event("category_change", current_info, new_post_ids)

    async def _handle_periodic_discord_updates(self, current_info: Dict[str, Any]) -> None:
        """Update Discord posts dynamically on periodic status checks (without category change)."""
        self._apply_configured_options(current_info)
        discord_posts = self.post_ids.get('discord', {})
        if not discord_posts:
            self.stream_info = current_info
            return

        logger.info(f"Dynamically updating {len(discord_posts)} Discord Webhook messages with latest status")

        require_native_live_frame(current_info, current_info.get('platform'))

        is_thumbnail_mode = not current_info.get('use_keyart') and not current_info.get('use_boxart')
        discord_image = None
        if is_thumbnail_mode:
            try:
                from ..core.config import get_generated_images_dir
                output_dir = get_generated_images_dir()
                os.makedirs(output_dir, exist_ok=True)
                username = current_info.get('username') or current_info.get('user_name') or current_info.get('identifier')
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                discord_image = os.path.join(output_dir, f'discord_{username}_{timestamp}.jpg')
                await self.twitter_processor.process_stream_image(current_info, discord_image, force_16_9=True, is_discord=True)
                current_info.setdefault('generated_images', []).append(discord_image)
            except Exception as img_err:
                logger.error(f"Failed to generate dynamic thumbnail image: {img_err}")

        customizer_config = current_info.get('customizer_config') or {}
        discord_text_template = customizer_config.get("discord_text_template", "{role} 🔴 {title}\n🕹️ {game}\n➡️ Ver en <{url}>")
        if "{url}" in discord_text_template and "<{url}>" not in discord_text_template:
            discord_text_template = discord_text_template.replace("{url}", "<{url}>")

        username = current_info.get('username') or current_info.get('user_name') or current_info.get('identifier')

        for account_id, msg_id in discord_posts.items():
            try:
                account = (self.accounts_config or {}).get('discord', {}).get(account_id, {})
                ping_role = account.get('ping_role')
                formatted_role = self._format_discord_role(ping_role)
                webhook_template = discord_text_template.replace("{role}", formatted_role)
                if not formatted_role and webhook_template.startswith("\n"):
                    webhook_template = webhook_template.lstrip("\n")
                formatted_text = format_template(webhook_template.strip(), current_info, username)

                image_filename = os.path.basename(discord_image) if discord_image else None
                embeds = build_discord_embeds(
                    current_info,
                    username,
                    is_offline=False,
                    image_filename=image_filename,
                    twitter_processor=self.twitter_processor
                )

                client = await self._get_discord_client(account_id, account, username)
                content = {
                    'text': formatted_text,
                    'image_path': discord_image,
                    'stream_url': current_info.get('url') or current_info.get('stream_url'),
                    'language': current_info.get('language', 'en'),
                    'embeds': embeds
                }
                await client.update_content(msg_id, content)
                logger.info(f"Successfully updated Discord Webhook '{account_id}' with dynamic status")
            except Exception as update_err:
                logger.error(f"Failed to update Discord Webhook '{account_id}' with dynamic status: {update_err}")

        self.stream_info = current_info

    def _adapt_check_interval(self) -> None:
        """Adapt check interval based on rate-limit headers."""
        remaining = int(self.stream_info.get('ratelimit_remaining', 800))
        if remaining < 100:
            self.check_interval = min(self.check_interval * self.backoff_factor, self.max_interval)
            logger.warning(f"Rate limit getting low, increasing check interval to {self.check_interval}s")
        elif remaining > 500 and self.check_interval > self.min_interval:
            self.check_interval = max(self.check_interval / self.backoff_factor, self.min_interval)
            logger.info(f"Rate limit healthy, decreasing check interval to {self.check_interval}s")

    async def _handle_stream_end(self) -> None:
        """Handle stream end: update offline Discord embeds, delete posts, and reset to waiting."""
        logger.info("Stream ended. Performing session cleanup and transition back to waiting...")

        # Update Discord posts on stream end if requested
        discord_update_ends = []
        if self.update_end_accounts and 'discord' in self.update_end_accounts:
            for account_id in self.post_ids.get('discord', {}):
                if account_id in self.update_end_accounts['discord']:
                    discord_update_ends.append(account_id)

        instagram_update_ends = []
        if self.update_end_accounts and 'instagram' in self.update_end_accounts:
            instagram_update_ends = list(self.update_end_accounts.get('instagram') or [])

        # Resolve the recorded VOD once — both end-card paths consume it.
        vod_url = None
        if discord_update_ends or instagram_update_ends:
            user_id_or_ident = self.stream_info.get('user_id') or self.stream_info.get('identifier') or self.stream_info.get('username')
            if hasattr(self.source, 'get_latest_vod') and user_id_or_ident:
                try:
                    vod_url = await self.source.get_latest_vod(user_id_or_ident)
                    if vod_url:
                        logger.info(f"Retrieved recorded VOD URL: {vod_url}")
                except Exception as vod_err:
                    logger.debug(f"Could not retrieve VOD URL on stream end: {vod_err}")

        if discord_update_ends:
            logger.info(f"Updating {len(discord_update_ends)} Discord Webhooks on stream end")
            try:

                from ..core.config import get_generated_images_dir
                output_dir = get_generated_images_dir()
                os.makedirs(output_dir, exist_ok=True)
                username = self.stream_info.get('username') or self.stream_info.get('user_name') or self.stream_info.get('identifier')
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

                discord_image = os.path.join(output_dir, f'discord_offline_{username}_{timestamp}.jpg')
                await self.twitter_processor.process_stream_image(self.stream_info, discord_image, force_16_9=True, is_discord=True, is_offline=True)
                self.stream_info.setdefault('generated_images', []).append(discord_image)

                customizer_config = self.stream_info.get('customizer_config') or {}
                discord_text_template = customizer_config.get("discord_text_template", "{role} 🔴 {title}\n🕹️ {game}\n➡️ Ver en <{url}>")
                if "{url}" in discord_text_template and "<{url}>" not in discord_text_template:
                    discord_text_template = discord_text_template.replace("{url}", "<{url}>")

                for account_id in discord_update_ends:
                    try:
                        account = (self.accounts_config or {}).get('discord', {}).get(account_id, {})
                        ping_role = account.get('ping_role')
                        formatted_role = self._format_discord_role(ping_role)
                        webhook_template = discord_text_template.replace("{role}", formatted_role)
                        if not formatted_role and webhook_template.startswith("\n"):
                            webhook_template = webhook_template.lstrip("\n")
                        formatted_text = format_template(webhook_template.strip(), self.stream_info, username)

                        embeds = build_discord_embeds(
                            self.stream_info,
                            username,
                            is_offline=True,
                            image_filename=os.path.basename(discord_image),
                            twitter_processor=self.twitter_processor
                        )

                        client = await self._get_discord_client(account_id, account, username)
                        content = {
                            'text': formatted_text,
                            'image_path': discord_image,
                            'stream_url': self.stream_info.get('url') or self.stream_info.get('stream_url'),
                            'vod_url': vod_url,
                            'language': self.stream_info.get('language', 'en'),
                            'embeds': embeds,
                            'is_offline': True
                        }
                        msg_id = self.post_ids['discord'][account_id]
                        await client.update_content(msg_id, content)
                        logger.info(f"Successfully updated Discord Webhook '{account_id}' to offline on stream end")
                    except Exception as update_err:
                        logger.error(f"Failed to update Discord Webhook '{account_id}' to offline on stream end: {update_err}")
            except Exception as img_err:
                logger.error(f"Failed to generate offline image for Discord updates: {img_err}")

        # Instagram Stories end cards — stories can't be edited, so the end
        # card is a fresh story carrying the offline card + a VOD link sticker.
        if instagram_update_ends:
            logger.info(f"Posting end-card stories to {len(instagram_update_ends)} Instagram account(s)")
            try:
                instagram_accounts_map = (self.accounts_config or {}).get('instagram', {})
                allowed = await _authorize_destinations(
                    'instagram',
                    instagram_update_ends,
                    instagram_accounts_map,
                    self.destination_authorizer,
                    self.post_ids,
                )
                if allowed:
                    from ..core.config import get_generated_images_dir
                    output_dir = get_generated_images_dir()
                    os.makedirs(output_dir, exist_ok=True)
                    username = self.stream_info.get('username') or self.stream_info.get('user_name') or self.stream_info.get('identifier')
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    ig_image = os.path.join(output_dir, f'instagram_endcard_{username}_{timestamp}.jpg')
                    await self.instagram_processor.process_stream_image(self.stream_info, ig_image)
                    self.stream_info.setdefault('generated_images', []).append(ig_image)

                    customizer_config = self.stream_info.get('customizer_config') or {}
                    end_template = customizer_config.get(
                        "instagram_end_caption_template",
                        "⏹ {title}\n\nThanks for watching!",
                    )
                    caption = format_template(end_template, self.stream_info, username)
                    link_url = vod_url or self.stream_info.get('url') or self.stream_info.get('stream_url') or ''

                    for account_id in allowed:
                        try:
                            account = instagram_accounts_map.get(account_id, {})
                            cache_key = f"{username}_{account_id}"
                            if cache_key not in self.instagram_clients:
                                client = InstagramClient(account_id, account)
                                if client.login():
                                    self.instagram_clients[cache_key] = client
                            client = self.instagram_clients.get(cache_key)
                            if not client:
                                continue
                            # End-card stories persist — they expire naturally
                            # in 24h and are never queued for deletion.
                            await client.post_content({
                                'image_path': ig_image,
                                'caption': caption,
                                'url': link_url,
                            })
                            logger.info(f"Posted end-card story for Instagram account '{account_id}'")
                        except Exception as ig_err:
                            logger.error(f"Failed to post end-card story for '{account_id}': {ig_err}")
            except Exception as end_err:
                logger.error(f"Failed Instagram end-card flow: {end_err}")

        # Cleanup posts that should be deleted
        posts_to_delete = self._collect_posts_to_delete(is_stream_end=True)
        if posts_to_delete:
            logger.info("Performing cleanup for ended stream...")
            await self.delete_posts_fn(
                posts_to_delete,
                instagram_clients=self.instagram_clients,
                twitter_clients=self.twitter_clients,
                discord_clients=self.discord_clients
            )
            logger.info("Cleanup completed")
        else:
            logger.info("Keeping posts due to preserve settings")

        # Clear post IDs and reset stream_info to offline skeleton
        self.post_ids.clear()
        self.stream_info = {
            'platform': self.stream_info.get('platform'),
            'identifier': self.stream_info.get('identifier'),
            'title': self.stream_info.get('identifier'),
            'game_name': 'Offline',
            'viewer_count': 0,
            'url': self.stream_info.get('url'),
            'is_offline_start': True,
            'use_keyart': self._base_options['use_keyart'],
            'use_boxart': self._base_options['use_boxart'],
            'crop_anchor': self._base_options['crop_anchor'],
            'use_live_frame': self._base_options['use_live_frame'],
            'live_frame_orientation': self._base_options['live_frame_orientation'],
            'source_aspect_ratio': self._base_options['source_aspect_ratio'],
            'language': self._base_options['language'],
            'customizer_config': self._base_options['customizer_config'],
            'requested_use_keyart': self._base_options['use_keyart'],
            'requested_use_boxart': self._base_options['use_boxart'],
            'requested_use_live_frame': self._base_options['use_live_frame'],
        }
        logger.info("Stream session reset. Transitioning back to waiting state.")

    async def _cleanup_on_stop(self) -> None:
        """Final cleanup when monitoring is explicitly stopped."""
        # Update Discord posts on stop if requested
        discord_update_ends = []
        if self.update_end_accounts and 'discord' in self.update_end_accounts:
            for account_id in self.post_ids.get('discord', {}):
                if account_id in self.update_end_accounts['discord']:
                    discord_update_ends.append(account_id)

        if discord_update_ends:
            logger.info(f"Updating {len(discord_update_ends)} Discord Webhooks on stream end")
            try:
                from ..core.config import get_generated_images_dir
                output_dir = (
                    get_generated_images_dir()
                    if os.environ.get("STREAMSHARE_DATA_DIR")
                    else os.path.join(os.getcwd(), 'generated_images')
                )
                os.makedirs(output_dir, exist_ok=True)
                username = self.stream_info.get('username') or self.stream_info.get('user_name') or self.stream_info.get('identifier')
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

                discord_image = os.path.join(output_dir, f'discord_offline_{username}_{timestamp}.jpg')
                await self.twitter_processor.process_stream_image(self.stream_info, discord_image, force_16_9=True, is_discord=True, is_offline=True)
                self.stream_info.setdefault('generated_images', []).append(discord_image)

                customizer_config = self.stream_info.get('customizer_config') or {}
                discord_text_template = customizer_config.get("discord_text_template", "{role} 🔴 {title}\n🕹️ {game}\n➡️ Ver en <{url}>")
                if "{url}" in discord_text_template and "<{url}>" not in discord_text_template:
                    discord_text_template = discord_text_template.replace("{url}", "<{url}>")

                for account_id in discord_update_ends:
                    try:
                        account = (self.accounts_config or {}).get('discord', {}).get(account_id, {})
                        ping_role = account.get('ping_role')
                        formatted_role = self._format_discord_role(ping_role)
                        webhook_template = discord_text_template.replace("{role}", formatted_role)
                        if not formatted_role and webhook_template.startswith("\n"):
                            webhook_template = webhook_template.lstrip("\n")
                        formatted_text = format_template(webhook_template.strip(), self.stream_info, username)

                        embeds = build_discord_embeds(
                            self.stream_info,
                            username,
                            is_offline=True,
                            image_filename=os.path.basename(discord_image),
                            twitter_processor=self.twitter_processor
                        )

                        client = await self._get_discord_client(account_id, account, username)
                        content = {
                            'text': formatted_text,
                            'image_path': discord_image,
                            'stream_url': self.stream_info.get('url') or self.stream_info.get('stream_url'),
                            'language': self.stream_info.get('language', 'en'),
                            'embeds': embeds,
                            'is_offline': True
                        }
                        msg_id = self.post_ids['discord'][account_id]
                        await client.update_content(msg_id, content)
                        logger.info(f"Successfully updated Discord Webhook '{account_id}' to offline on stream end")
                    except Exception as update_err:
                        logger.error(f"Failed to update Discord Webhook '{account_id}' to offline on stream end: {update_err}")
            except Exception as img_err:
                logger.error(f"Failed to generate offline image for Discord updates: {img_err}")

        posts_to_delete = self._collect_posts_to_delete(is_stream_end=True)
        if posts_to_delete:
            logger.info("Monitoring stopped, performing cleanup...")
            await self.delete_posts_fn(
                posts_to_delete,
                instagram_clients=self.instagram_clients,
                twitter_clients=self.twitter_clients,
                discord_clients=self.discord_clients
            )
            logger.info("Cleanup completed")
        else:
            logger.info("Monitoring stopped, keeping posts due to preserve settings")

    def _collect_posts_to_delete(self, is_stream_end: bool = True) -> Dict[str, Dict[str, str]]:
        """Determine which posts must be deleted based on preservation / update settings."""
        posts_to_delete = {}
        for platform, accounts in self.post_ids.items():
            if isinstance(accounts, dict):
                for account_id, post_id in accounts.items():
                    is_preserved = False
                    if is_stream_end:
                        if self.preserve_end_accounts and platform in self.preserve_end_accounts:
                            if account_id in self.preserve_end_accounts[platform]:
                                is_preserved = True
                        if not self.preserve_end_accounts:
                            is_preserved = self.preserve_end_posts

                        is_updated = False
                        if platform == 'discord' and self.update_end_accounts and platform in self.update_end_accounts:
                            if account_id in self.update_end_accounts[platform]:
                                is_updated = True
                    else:
                        if self.preserve_category_accounts and platform in self.preserve_category_accounts:
                            if account_id in self.preserve_category_accounts[platform]:
                                is_preserved = True
                        if not self.preserve_category_accounts:
                            is_preserved = self.preserve_category_posts

                        is_updated = False
                        if platform == 'discord' and self.update_category_accounts and platform in self.update_category_accounts:
                            if account_id in self.update_category_accounts[platform]:
                                is_updated = True

                    if not is_preserved and not is_updated:
                        if platform not in posts_to_delete:
                            posts_to_delete[platform] = {}
                        posts_to_delete[platform][account_id] = post_id
        return posts_to_delete

    async def _update_discord_reposts_offline(self, account_ids: List[str], current_info: Dict[str, Any]) -> None:
        """Update old Discord messages to transition embed state when category changed."""
        logger.info(f"Updating {len(account_ids)} old Discord Webhooks to category transition on repost")
        try:
            from ..core.config import get_generated_images_dir
            output_dir = (
                get_generated_images_dir()
                if os.environ.get("STREAMSHARE_DATA_DIR")
                else os.path.join(os.getcwd(), 'generated_images')
            )
            os.makedirs(output_dir, exist_ok=True)
            username = self.stream_info.get('username') or self.stream_info.get('user_name') or self.stream_info.get('identifier')
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            discord_image = os.path.join(output_dir, f'discord_offline_{username}_{timestamp}.jpg')
            await self.twitter_processor.process_stream_image(self.stream_info, discord_image, force_16_9=True, is_discord=True, is_category_repost=True)
            self.stream_info.setdefault('generated_images', []).append(discord_image)

            customizer_config = self.stream_info.get('customizer_config') or {}
            discord_text_template = customizer_config.get("discord_text_template", "{role} 🔴 {title}\n🕹️ {game}\n➡️ Ver en <{url}>")
            if "{url}" in discord_text_template and "<{url}>" not in discord_text_template:
                discord_text_template = discord_text_template.replace("{url}", "<{url}>")

            for account_id in account_ids:
                try:
                    account = (self.accounts_config or {}).get('discord', {}).get(account_id, {})
                    ping_role = account.get('ping_role')
                    formatted_role = self._format_discord_role(ping_role)
                    webhook_template = discord_text_template.replace("{role}", formatted_role)
                    if not formatted_role and webhook_template.startswith("\n"):
                        webhook_template = webhook_template.lstrip("\n")
                    formatted_text = format_template(webhook_template.strip(), self.stream_info, username)

                    embeds = build_discord_embeds(
                        self.stream_info,
                        username,
                        is_offline=False,
                        is_category_change=True,
                        next_game=current_info.get('game_name'),
                        image_filename=os.path.basename(discord_image),
                        twitter_processor=self.twitter_processor
                    )

                    client = await self._get_discord_client(account_id, account, username)
                    content = {
                        'text': formatted_text,
                        'image_path': discord_image,
                        'stream_url': current_info.get('url') or self.stream_info.get('url') or self.stream_info.get('stream_url'),
                        'language': self.stream_info.get('language', 'en'),
                        'embeds': embeds,
                        'is_category_change': True,
                        'is_offline': False
                    }
                    msg_id = self.post_ids['discord'][account_id]
                    await client.update_content(msg_id, content)
                    logger.info(f"Successfully updated Discord Webhook '{account_id}' to category transition on repost")
                except Exception as update_err:
                    logger.error(f"Failed to update Discord Webhook '{account_id}' on category change repost: {update_err}")
        except Exception as img_err:
            logger.error(f"Failed to generate offline image for Discord updates on category change repost: {img_err}")

    async def _get_discord_client(self, account_id: str, account: Dict[str, Any], username: str) -> DiscordWebhookClient:
        """Get or initialize a cached Discord webhook client."""
        cache_key = f"{username}_{account_id}"
        if cache_key not in self.discord_clients:
            client = discord_client(account_id, account)
            await client.authenticate()
            self.discord_clients[cache_key] = client
        return self.discord_clients[cache_key]

    @staticmethod
    def _format_discord_role(ping_role: Any) -> str:
        """Format discord role string into a pingable token."""
        if not ping_role:
            return ""
        ping_role_str = str(ping_role).strip()
        if ping_role_str.lower() in ("everyone", "@everyone"):
            return "@everyone"
        if ping_role_str.lower() in ("here", "@here"):
            return "@here"
        if ping_role_str.startswith("<") and ping_role_str.endswith(">"):
            return ping_role_str
        if ping_role_str.isdigit():
            return f"<@&{ping_role_str}>"
        return ping_role_str


async def monitor_stream(
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
    igdb_client: Optional[IGDBClient] = None,
    instagram_clients: Optional[Dict[str, Any]] = None,
    twitter_clients: Optional[Dict[str, Any]] = None,
    discord_clients: Optional[Dict[str, Any]] = None,
    instagram_processor: Optional[InstagramImageProcessor] = None,
    twitter_processor: Optional[TwitterImageProcessor] = None,
    config: Optional[Config] = None,
    share_posts_fn: Optional[Callable] = None,
    delete_posts_fn: Optional[Callable] = None,
    destination_authorizer: Optional[Callable] = None
) -> None:
    """Convenience functional interface for StreamMonitor."""
    monitor = StreamMonitor(
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
        response_tweet=response_tweet,
        twitter_manual=twitter_manual,
        tiktok_manual=tiktok_manual,
        twitter_reply_accounts=twitter_reply_accounts,
        monitor_only=monitor_only,
        igdb_client=igdb_client,
        instagram_clients=instagram_clients,
        twitter_clients=twitter_clients,
        discord_clients=discord_clients,
        instagram_processor=instagram_processor,
        twitter_processor=twitter_processor,
        config=config,
        share_posts_fn=share_posts_fn,
        delete_posts_fn=delete_posts_fn,
        destination_authorizer=destination_authorizer
    )
    await monitor.run()

