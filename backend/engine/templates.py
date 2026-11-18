from datetime import datetime
from typing import Dict, Any, Optional


def format_template(text_template: str, stream_info: Dict[str, Any], username: Optional[str] = None) -> str:
    """Resolve universal dynamic placeholders in text strings.
    
    Unified superset of template resolution across StreamShare:
    - {title} / {stream_title}
    - {game} / {category} (defaults to "Live")
    - {username} / {user_name}
    - {platform} (capitalized, e.g. "Twitch") / {platform_upper} (uppercase, e.g. "TWITCH")
    - {url} / {stream_url} / {channel_url} (defaults to https://twitch.tv/{username})
    - {date} (defaults to stream_info['date_str'] or empty string)
    - {previous_game}
    """
    if not text_template:
        return ""

    u_name = username or stream_info.get('username') or stream_info.get('user_name') or stream_info.get('identifier') or ""
    title = stream_info.get('title') or stream_info.get('stream_title') or ""
    game = stream_info.get('game_name') or stream_info.get('category') or "Live"
    platform = stream_info.get('platform') or "Twitch"
    url = stream_info.get('url') or stream_info.get('stream_url') or stream_info.get('channel_url') or (f"https://twitch.tv/{u_name}" if u_name else "")
    date_str = stream_info.get('date_str') or stream_info.get('date') or ""
    previous_game = stream_info.get('previous_game') or ""

    resolved = str(text_template)
    resolved = resolved.replace("{title}", str(title))
    resolved = resolved.replace("{game}", str(game))
    resolved = resolved.replace("{category}", str(game))
    resolved = resolved.replace("{username}", str(u_name))
    resolved = resolved.replace("{platform_upper}", str(platform).upper())
    resolved = resolved.replace("{platform}", str(platform).capitalize())
    resolved = resolved.replace("{url}", str(url))
    resolved = resolved.replace("{date}", str(date_str))
    resolved = resolved.replace("{previous_game}", str(previous_game))

    return resolved


# Function alias for callers expecting format_text
format_text = format_template
