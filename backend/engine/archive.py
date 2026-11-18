import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

_DURATION_RE = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?")
_VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")


def parse_youtube_duration(value: Any) -> Optional[int]:
    if not isinstance(value, str) or len(value) > 64 or value.endswith("T"):
        return None
    match = _DURATION_RE.fullmatch(value)
    if not match or not any(part is not None for part in match.groups()):
        return None
    days, hours, minutes, seconds = match.groups()
    total = int(days or 0) * 86400 + int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)
    return int(math.floor(total + 0.5)) if math.isfinite(total) else None


def _timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _thumbnail(snippet: Dict[str, Any]) -> Optional[str]:
    thumbnails = snippet.get("thumbnails") or {}
    if not isinstance(thumbnails, dict):
        return None
    for size in ("maxres", "standard", "high", "medium", "default"):
        entry = thumbnails.get(size)
        value = entry.get("url") if isinstance(entry, dict) else None
        if not isinstance(value, str):
            continue
        try:
            parsed = urlsplit(value)
        except ValueError:
            continue
        if parsed.scheme == "https" and parsed.hostname in {"i.ytimg.com", "img.youtube.com"} and not parsed.username and not parsed.password:
            return value
    return None


def normalize_youtube_archive_item(item: Any, expected_channel_id: str) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    video_id = str(item.get("id") or "")
    if not _VIDEO_ID_RE.fullmatch(video_id):
        return None
    snippet = item.get("snippet") or {}
    details = item.get("liveStreamingDetails") or {}
    status = item.get("status") or {}
    content = item.get("contentDetails") or {}
    if not all(isinstance(value, dict) for value in (snippet, details, status, content)):
        return None
    if not expected_channel_id or snippet.get("channelId") != expected_channel_id:
        return None
    started_at = _timestamp(details.get("actualStartTime"))
    if started_at is None:
        return None
    ended_at = _timestamp(details.get("actualEndTime"))
    if ended_at is not None and ended_at < started_at:
        return None
    visibility = str(status.get("privacyStatus") or "unknown").lower()
    if visibility not in {"public", "unlisted", "private"}:
        visibility = "unknown"
    elapsed = int(math.floor((ended_at - started_at).total_seconds() + 0.5)) if ended_at is not None else None
    vod_duration = parse_youtube_duration(content.get("duration"))
    return {
        "external_id": video_id,
        "channel_id": expected_channel_id,
        "title": str(snippet.get("title") or "")[:300],
        "source_visibility": visibility,
        "started_at": _iso(started_at),
        "ended_at": _iso(ended_at),
        "duration_seconds": elapsed,
        "vod_duration_seconds": vod_duration,
        "published_at": _iso(_timestamp(snippet.get("publishedAt"))),
        "thumbnail_url": _thumbnail(snippet),
        "vod_url": f"https://www.youtube.com/watch?v={video_id}" if visibility == "public" and status.get("embeddable") is not False else None,
        "provenance": {
            "started_at": "provider_reported",
            "ended_at": "provider_reported" if ended_at is not None else "unknown",
            "duration_seconds": "derived_from_provider_timestamps" if elapsed is not None else "unknown",
            "vod_duration_seconds": "provider_reported" if vod_duration is not None else "unknown",
            "category": "unknown",
        },
    }


def youtube_portal_projection(record: Dict[str, Any], publish_to_portal: bool) -> Dict[str, Any]:
    identity = {"external_id": record["external_id"], "channel_id": record["channel_id"]}
    if record.get("source_visibility") != "public" or record.get("ended_at") is None or publish_to_portal is not True:
        return {**identity, "source_visibility": record.get("source_visibility", "unknown"), "portal_visibility": "private", "withdraw": True}
    return {
        **identity,
        "source_visibility": "public",
        "portal_visibility": "public",
        "vod_visibility": "public" if record.get("vod_url") else "unknown",
        "title": record["title"],
        "started_at": record["started_at"],
        "ended_at": record["ended_at"],
        "duration_seconds": record["duration_seconds"],
        "thumbnail_url": record.get("thumbnail_url"),
        "vod_url": record.get("vod_url"),
        "provenance": record["provenance"],
        "withdraw": False,
    }


_YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


def _chunked(items: list[str], size: int = 50):
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def enumerate_youtube_channel_archive(
    *,
    access_token: str,
    expected_channel_id: str,
    max_pages: int = 5,
    page_token: Optional[str] = None,
    client=None,
) -> Dict[str, Any]:
    """Enumerate a creator's YouTube livestream archive using a creator-authorized
    OAuth access token (`youtube.readonly`).

    Per YouTube Data API v3 rules (`platform_integration.md` §5.4):
    - Never calls ``videos.list(mine=true)`` (unsupported by YouTube).
    - Resolves the channel's ``uploads`` playlist via ``channels.list(mine=true)``,
      verifies the returned channel id matches ``expected_channel_id``,
      paginates ``playlistItems.list`` (resumable via ``page_token`` /
      ``next_page_token``) and ``liveBroadcasts.list(mine=true)``, then fetches
      ``videos.list(id=...)`` in batches of up to 50 IDs and normalizes only
      items carrying ``liveStreamingDetails.actualStartTime`` and matching
      ``expected_channel_id``.
    """
    import httpx

    if not access_token or not expected_channel_id:
        raise ValueError("access_token and expected_channel_id are required")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    async def _get(http_client, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        resp = await http_client.get(
            f"{_YOUTUBE_API_BASE}/{endpoint}",
            headers=headers,
            params=params,
        )
        if resp.status_code == 401:
            err = PermissionError("youtube_token_unauthorized")
            setattr(err, "status", 401)
            raise err
        if resp.status_code != 200:
            err = RuntimeError(f"youtube_api_error_{resp.status_code}")
            setattr(err, "status", resp.status_code)
            raise err
        data = resp.json()
        return data if isinstance(data, dict) else {}

    async def _run(http_client) -> Dict[str, Any]:
        ch_data = await _get(
            http_client,
            "channels",
            {"part": "id,contentDetails", "mine": "true"},
        )
        ch_items = ch_data.get("items") or []
        if not ch_items or not isinstance(ch_items[0], dict):
            raise PermissionError("youtube_channel_not_found")
        actual_channel_id = str(ch_items[0].get("id") or "").strip()
        if actual_channel_id != expected_channel_id:
            raise PermissionError("youtube_channel_mismatch")

        uploads_playlist_id = (
            ((ch_items[0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        )

        video_ids: list[str] = []
        seen_ids: set[str] = set()
        cursor = page_token
        pages_fetched = 0

        if uploads_playlist_id:
            while pages_fetched < max(1, max_pages):
                pl_params: Dict[str, Any] = {
                    "part": "contentDetails",
                    "playlistId": uploads_playlist_id,
                    "maxResults": 50,
                }
                if cursor:
                    pl_params["pageToken"] = cursor
                pl_data = await _get(http_client, "playlistItems", pl_params)
                pages_fetched += 1
                for item in pl_data.get("items") or []:
                    vid = str(((item or {}).get("contentDetails") or {}).get("videoId") or "")
                    if _VIDEO_ID_RE.fullmatch(vid) and vid not in seen_ids:
                        seen_ids.add(vid)
                        video_ids.append(vid)
                cursor = pl_data.get("nextPageToken")
                if not cursor:
                    break

        # Also include owner-authorized broadcasts on the initial pass so
        # unlisted/completed broadcasts not yet surfaced in uploads are covered.
        if page_token is None:
            try:
                bc_data = await _get(
                    http_client,
                    "liveBroadcasts",
                    {"part": "id", "mine": "true", "maxResults": 50},
                )
                for item in bc_data.get("items") or []:
                    vid = str((item or {}).get("id") or "")
                    if _VIDEO_ID_RE.fullmatch(vid) and vid not in seen_ids:
                        seen_ids.add(vid)
                        video_ids.append(vid)
            except Exception:
                # liveBroadcasts may be disabled on channels without live streaming enabled.
                pass

        records: list[Dict[str, Any]] = []
        for batch in _chunked(video_ids, 50):
            v_data = await _get(
                http_client,
                "videos",
                {
                    "part": "snippet,liveStreamingDetails,status,contentDetails",
                    "id": ",".join(batch),
                },
            )
            for raw_item in v_data.get("items") or []:
                norm = normalize_youtube_archive_item(raw_item, expected_channel_id)
                if norm is not None:
                    records.append(norm)

        return {
            "channel_id": expected_channel_id,
            "items": records,
            "scanned_video_count": len(video_ids),
            "next_page_token": cursor or None,
        }

    if client is not None:
        return await _run(client)
    async with httpx.AsyncClient(timeout=15.0) as http_client:
        return await _run(http_client)
