from pathlib import Path
from typing import Any, Dict, Optional

from PIL import Image

from ...core.exceptions import PlatformError

VALID_LIVE_FRAME_ORIENTATIONS = {'auto', 'horizontal', 'vertical'}


def normalize_live_frame_orientation(value: Any) -> str:
    orientation = str(value or 'auto').strip().lower()
    return orientation if orientation in VALID_LIVE_FRAME_ORIENTATIONS else 'auto'


def classify_dimensions(width: Any, height: Any) -> Optional[str]:
    try:
        width_value = int(width)
        height_value = int(height)
    except (TypeError, ValueError):
        return None
    if width_value <= 0 or height_value <= 0 or width_value == height_value:
        return None
    return 'horizontal' if width_value > height_value else 'vertical'


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def select_native_stream_url(info: Dict[str, Any], orientation: str) -> Optional[str]:
    requested = normalize_live_frame_orientation(orientation)
    if requested == 'auto':
        return info.get('url')

    matches = []
    for item in info.get('formats') or []:
        if not isinstance(item, dict) or not item.get('url') or item.get('vcodec') == 'none':
            continue
        if classify_dimensions(item.get('width'), item.get('height')) != requested:
            continue
        matches.append(item)
    if not matches:
        return None
    selected = max(matches, key=lambda item: (
        int(item.get('width') or 0) * int(item.get('height') or 0),
        _safe_float(item.get('tbr')),
        _safe_float(item.get('fps')),
    ))
    return selected['url']


def require_native_live_frame(stream_info: Dict[str, Any], platform: Any = None) -> None:
    orientation = normalize_live_frame_orientation(stream_info.get('live_frame_orientation'))
    if not stream_info.get('use_live_frame') or orientation == 'auto' or stream_info.get('live_frame_captured') or stream_info.get('ad_fallback'):
        return
    raw_platform = str(platform or stream_info.get('platform') or 'platform').strip().lower()
    platform_label = {'youtube': 'YouTube', 'tiktok': 'TikTok', 'twitch': 'Twitch'}.get(raw_platform, raw_platform.title())
    raise PlatformError(f'{platform_label} does not expose a native {orientation} live frame for this stream.')


def captured_frame_orientation(path: str) -> Optional[str]:
    candidate = Path(path)
    if not candidate.is_file():
        return None
    try:
        with Image.open(candidate) as image:
            return classify_dimensions(image.width, image.height)
    except OSError:
        return None


def captured_frame_matches(path: str, orientation: str) -> bool:
    requested = normalize_live_frame_orientation(orientation)
    return requested == 'auto' or captured_frame_orientation(path) == requested
