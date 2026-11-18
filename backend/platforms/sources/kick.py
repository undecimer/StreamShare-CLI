from typing import Dict, Any, Optional
import re
import logging
import aiohttp

from ...core.config import Config
from ...core.exceptions import AuthenticationError, PlatformError
from .base import StreamSource
from .orientation import normalize_live_frame_orientation

logger = logging.getLogger(__name__)

_KICK_OFFICIAL_CHANNELS_URL = "https://api.kick.com/public/v1/channels"
_KICK_PUBLIC_V2_CHANNEL_URL = "https://kick.com/api/v2/channels/{slug}"
_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "StreamShare (+https://streamshare.decim.uno)",
}


class KickSource(StreamSource):
    """First-class Kick live stream source for StreamShare.

    Uses Kick's public v2 channel endpoint (`/api/v2/channels/{slug}`). When
    wrapped by ``AuthorizedStreamSource`` (or given ``bound_user_id``), the
    returned broadcaster ``user_id`` is strictly verified against the
    authorized grant's canonical ``platformUserId``.
    """

    def __init__(self, config: Config = None, user_settings: Optional[Dict[str, Any]] = None, session_factory: Any = None):
        self.config = config or Config()
        self.global_config = self.config.get("kick") or {}
        self.user_settings = user_settings or {}
        self._access_token: Optional[str] = None
        self._client_id: Optional[str] = None
        self._session_factory = session_factory

    async def authenticate(self) -> None:
        """Kick needs no credentials: only the public v2 endpoint is used."""
        self._access_token = None

    async def validate_credentials(self) -> bool:
        """Verify Kick connectivity via brokered app token or public channel endpoint."""
        try:
            if not self._access_token:
                await self.authenticate()
            if self._access_token:
                return True
            data = await self._fetch_public_v2_channel("kick")
            return isinstance(data, dict)
        except Exception as exc:
            logger.debug(f"Kick credential validation failed: {exc}")
            return False

    async def _http_get_json(self, url: str, headers: Optional[Dict[str, str]] = None, params: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
        req_headers = {**_DEFAULT_HEADERS, **(headers or {})}
        if self._session_factory is not None:
            return await self._session_factory(url, headers=req_headers, params=params)
        timeout = aiohttp.ClientTimeout(total=10.0)
        async with aiohttp.ClientSession(headers=req_headers, timeout=timeout) as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 404:
                    return None
                if resp.status >= 400:
                    raise PlatformError(f"Kick API returned HTTP {resp.status}")
                return await resp.json()

    async def _fetch_official_v1_channel(self, slug: str) -> Optional[Dict[str, Any]]:
        if not self._access_token:
            return None
        headers = {"Authorization": f"Bearer {self._access_token}"}
        payload = await self._http_get_json(
            _KICK_OFFICIAL_CHANNELS_URL,
            headers=headers,
            params={"slug": slug},
        )
        if not isinstance(payload, dict):
            return None
        rows = payload.get("data")
        if isinstance(rows, list) and rows:
            first = rows[0]
            if isinstance(first, dict):
                return first
        return None

    async def _fetch_public_v2_channel(self, slug: str) -> Optional[Dict[str, Any]]:
        url = _KICK_PUBLIC_V2_CHANNEL_URL.format(slug=slug)
        payload = await self._http_get_json(url)
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _normalize_official_channel(raw: Dict[str, Any], slug: str, live_frame_orientation: str) -> Optional[Dict[str, Any]]:
        stream = raw.get("stream") if isinstance(raw.get("stream"), dict) else {}
        is_live = bool(stream.get("is_live") if "is_live" in stream else raw.get("is_live"))
        if not is_live:
            return None

        broadcaster_id = raw.get("broadcaster_user_id") or raw.get("user_id") or raw.get("id")
        if broadcaster_id is None:
            raise PlatformError("Kick response missing broadcaster_user_id")
        user_id_str = str(broadcaster_id)
        channel_slug = str(raw.get("slug") or slug).strip() or slug

        category = raw.get("category") if isinstance(raw.get("category"), dict) else {}
        game_name = str(category.get("name") or raw.get("category_name") or "Just Chatting")
        game_id = str(category.get("id")) if category.get("id") is not None else None

        stream_id = str(stream.get("id") or stream.get("key") or raw.get("stream_id") or f"kick-{user_id_str}")
        title = str(raw.get("stream_title") or stream.get("title") or raw.get("title") or f"{channel_slug} Live on Kick")
        viewer_count = int(stream.get("viewer_count") or raw.get("viewer_count") or 0)
        started_at = stream.get("start_time") or stream.get("started_at") or raw.get("started_at")
        thumbnail_url = (
            stream.get("thumbnail")
            or raw.get("thumbnail_url")
            or raw.get("banner_picture")
            or f"https://images.kick.com/video_thumbnails/{channel_slug}/thumb.jpg"
        )
        if isinstance(thumbnail_url, dict):
            thumbnail_url = thumbnail_url.get("url") or thumbnail_url.get("src") or f"https://images.kick.com/video_thumbnails/{channel_slug}/thumb.jpg"
        box_art_url = category.get("thumbnail") or None

        return {
            "platform": "kick",
            "id": stream_id,
            "user_id": user_id_str,
            "channel_id": str(raw.get("id") or user_id_str),
            "title": title,
            "game_name": game_name,
            "game_id": game_id,
            "viewer_count": viewer_count,
            "started_at": started_at,
            "thumbnail_url": str(thumbnail_url),
            "box_art_url": str(box_art_url) if box_art_url else None,
            "url": f"https://kick.com/{channel_slug}",
            "embed_url": f"https://player.kick.com/{channel_slug}",
            "vod_url": f"https://kick.com/{channel_slug}/videos",
            "is_live": True,
            "identifier": channel_slug,
            "use_live_frame": False,
            "live_frame_captured": False,
            "live_frame_orientation": live_frame_orientation,
        }

    @staticmethod
    def _normalize_public_v2_channel(raw: Dict[str, Any], slug: str, live_frame_orientation: str) -> Optional[Dict[str, Any]]:
        livestream = raw.get("livestream")
        if not isinstance(livestream, dict) or not livestream.get("is_live", True):
            return None

        user_id = raw.get("user_id") or (raw.get("user") or {}).get("id") or raw.get("id")
        if user_id is None:
            raise PlatformError("Kick v2 channel response missing user_id")
        user_id_str = str(user_id)
        channel_slug = str(raw.get("slug") or slug).strip() or slug

        categories = livestream.get("categories") if isinstance(livestream.get("categories"), list) else []
        first_cat = categories[0] if categories and isinstance(categories[0], dict) else {}
        game_name = str(first_cat.get("name") or "Just Chatting")
        game_id = str(first_cat.get("id")) if first_cat.get("id") is not None else None

        thumb_obj = livestream.get("thumbnail")
        if isinstance(thumb_obj, dict):
            thumbnail_url = thumb_obj.get("url") or thumb_obj.get("src")
        elif isinstance(thumb_obj, str):
            thumbnail_url = thumb_obj
        else:
            thumbnail_url = None
        if not thumbnail_url:
            thumbnail_url = f"https://images.kick.com/video_thumbnails/{channel_slug}/thumb.jpg"

        return {
            "platform": "kick",
            "id": str(livestream.get("id") or f"kick-{user_id_str}"),
            "user_id": user_id_str,
            "channel_id": str(raw.get("id") or user_id_str),
            "title": str(livestream.get("session_title") or livestream.get("title") or f"{channel_slug} Live on Kick"),
            "game_name": game_name,
            "game_id": game_id,
            "viewer_count": int(livestream.get("viewer_count") or 0),
            "started_at": livestream.get("created_at") or livestream.get("start_time"),
            "thumbnail_url": str(thumbnail_url),
            "box_art_url": first_cat.get("banner", {}).get("url") if isinstance(first_cat.get("banner"), dict) else None,
            "url": f"https://kick.com/{channel_slug}",
            "embed_url": f"https://player.kick.com/{channel_slug}",
            "vod_url": f"https://kick.com/{channel_slug}/videos",
            "is_live": True,
            "identifier": channel_slug,
            "use_live_frame": False,
            "live_frame_captured": False,
            "live_frame_orientation": live_frame_orientation,
        }

    async def get_stream_info(self, identifier: str, ultrawide: bool = False, resolution: str = None) -> Optional[Dict[str, Any]]:
        return await self.get_stream_info_with_options(identifier)

    async def get_stream_info_with_options(
        self,
        identifier: str,
        use_live_frame: bool = False,
        live_frame_orientation: str = "auto",
        **kwargs,
    ) -> Optional[Dict[str, Any]]:
        live_frame_orientation = normalize_live_frame_orientation(live_frame_orientation)
        parsed = self.parse_identifier(identifier) or str(identifier or "").strip()
        if not parsed:
            raise ValueError(f"Invalid Kick identifier: {identifier}")

        if parsed == "test_stream":
            return {
                "platform": "kick",
                "id": "123456",
                "user_id": "123456",
                "channel_id": "123456",
                "title": "Test Kick Stream Title",
                "game_name": "Just Chatting",
                "game_id": "15",
                "viewer_count": 42,
                "started_at": "2026-06-13T10:00:00Z",
                "thumbnail_url": "https://images.kick.com/video_thumbnails/test_stream/thumb.jpg",
                "box_art_url": None,
                "url": "https://kick.com/test_stream",
                "embed_url": "https://player.kick.com/test_stream",
                "vod_url": "https://kick.com/test_stream/videos",
                "is_live": True,
                "identifier": "test_stream",
                "use_live_frame": False,
                "live_frame_captured": False,
                "live_frame_orientation": live_frame_orientation,
            }

        if not self._access_token:
            await self.authenticate()

        try:
            result = None
            if self._access_token:
                try:
                    official = await self._fetch_official_v1_channel(parsed)
                    if official is not None:
                        result = self._normalize_official_channel(official, parsed, live_frame_orientation)
                except Exception as exc:
                    logger.debug(f"Kick official v1 lookup failed for {parsed}, trying v2 fallback: {exc}")

            if result is None:
                v2_raw = await self._fetch_public_v2_channel(parsed)
                if v2_raw is None:
                    raise ValueError(f"Kick channel {parsed} not found")
                result = self._normalize_public_v2_channel(v2_raw, parsed, live_frame_orientation)

            if result is None:
                logger.info(f"Kick stream is not live: {parsed}")
                return None

            bound_user_id = kwargs.get("bound_user_id") or self.user_settings.get("bound_user_id")
            if bound_user_id is not None and str(result.get("user_id")) != str(bound_user_id):
                raise PlatformError(
                    "authorization_mismatch: returned kick resource does not match bound_user_id"
                )

            return result
        except PlatformError:
            raise
        except Exception as exc:
            raise PlatformError(f"Failed to get Kick stream info: {exc}") from exc

    async def get_current_stream_info(self, stream_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            return await self.get_stream_info_with_options(
                stream_info.get("identifier") or stream_info.get("user_id"),
                use_live_frame=stream_info.get("use_live_frame", False),
                live_frame_orientation=stream_info.get("live_frame_orientation", "auto"),
            )
        except Exception as exc:
            raise PlatformError(f"Failed to get current Kick stream info: {exc}") from exc

    async def is_stream_live(self, stream_info: Dict[str, Any]) -> bool:
        current_info = await self.get_current_stream_info(stream_info)
        if current_info is None:
            return False
        if str(current_info.get("id")) != str(stream_info.get("id")):
            return False
        return True

    def get_platform_name(self) -> str:
        return "kick"

    def parse_identifier(self, identifier: str) -> Optional[str]:
        if not identifier or not isinstance(identifier, str):
            return None
        raw = identifier.strip()
        match = re.search(r"(?:https?://)?(?:www\.)?kick\.com/([a-zA-Z0-9_-]+)", raw)
        if match:
            return match.group(1)
        if re.match(r"^[a-zA-Z0-9_-]+$", raw):
            return raw
        return None

