from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from ..media.twitter import TwitterImageProcessor


def build_discord_embeds(
    stream_info: Dict[str, Any],
    username: str,
    is_offline: bool = False,
    is_category_change: bool = False,
    next_game: Optional[str] = None,
    image_filename: Optional[str] = None,
    twitter_processor: Optional[TwitterImageProcessor] = None
) -> Optional[List[Dict[str, Any]]]:
    """Build Discord embeds list based on customizer config and stream info."""
    customizer_config = stream_info.get('customizer_config') or {}
    embed_config = customizer_config.get('discord_embed')
    if not embed_config:
        embed_config = {
            "enabled": True,
            "color": "#5865f2",
            "title_template": "{username} is LIVE!",
            "description_template": "{title}",
            "offline_title_template": "{username} was live",
            "offline_description_template": "Thanks for watching! The stream has ended.",
            "category_change_title_template": "{username} played {game}",
            "category_change_desc_template": "🎮 Category switch! Stream is still live, now playing **{next_game}**.",
            "footer_template": "Last online • {time}",
            "show_fields": ["game", "views", "duration"]
        }
    
    # If embeds are not explicitly enabled, return None
    if not embed_config.get('enabled', True):
        return None
        
    # Calculate duration
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
                    
    # Get border color
    color_hex = embed_config.get('color', '#5865f2').replace('#', '')
    try:
        color_int = int(color_hex, 16)
    except ValueError:
        color_int = 5814783  # default Discord blurple (#5865f2)
        
    # Select title and description template based on live / category change / offline status
    lang = stream_info.get('language', 'en')
    if is_category_change:
        title_tpl = embed_config.get('category_change_title_template', '{username} estuvo jugando a {game}' if lang == 'es' else '{username} played {game}')
        if lang == 'es' and (title_tpl == '{username} played {game}' or title_tpl == '{username} was playing {game}'):
            title_tpl = '{username} estuvo jugando a {game}'
        desc_tpl = embed_config.get('category_change_desc_template', '🎮 ¡Cambio de categoría! El directo continúa ahora con **{next_game}**.' if lang == 'es' else '🎮 Category switch! Stream is still live, now playing **{next_game}**.')
        if lang == 'es' and 'Category switch' in desc_tpl:
            desc_tpl = '🎮 ¡Cambio de categoría! El directo continúa ahora con **{next_game}**.'
    elif is_offline:
        title_tpl = embed_config.get('offline_title_template', '{username} was live')
        if lang == 'es' and (title_tpl == '{username} was live' or title_tpl == '{username} was live!'):
            title_tpl = '{username} transmitió en vivo'
        desc_tpl = embed_config.get('offline_description_template', 'Thanks for watching! The stream has ended.')
        if lang == 'es' and desc_tpl == 'Thanks for watching! The stream has ended.':
            desc_tpl = '¡Gracias por ver! La transmisión ha terminado.'
    else:
        title_tpl = embed_config.get('title_template', '{username} is LIVE!')
        if lang == 'es' and (title_tpl == '{username} is LIVE!' or title_tpl == '{username} is LIVE' or title_tpl == '{username}'):
            title_tpl = '{username} está EN VIVO!'
        desc_tpl = embed_config.get('description_template', '{title}')
        
    # Format text fields
    twit_proc = twitter_processor
    title_text = twit_proc.format_text(title_tpl, stream_info) if twit_proc else title_tpl
    title_text = title_text.replace('{username}', username)
    if next_game:
        title_text = title_text.replace('{next_game}', next_game)
    
    desc_text = twit_proc.format_text(desc_tpl, stream_info) if twit_proc else desc_tpl
    desc_text = desc_text.replace('{username}', username)
    if next_game:
        desc_text = desc_text.replace('{next_game}', next_game)
    
    # Footer
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if stream_info.get('no_monitor', False):
        footer_text = f"Compartido a las {current_time}" if lang == "es" else f"Shared at {current_time}"
    elif is_category_change:
        footer_text = f"🔄 Cambio de categoría • {current_time}" if lang == "es" else f"🔄 Category changed • {current_time}"
    elif not is_offline:
        footer_text = "🔴 EN VIVO" if lang == "es" else "🔴 LIVE"
    else:
        footer_tpl = embed_config.get('footer_template', 'Last online • {time}')
        if lang == 'es' and footer_tpl == 'Last online • {time}':
            footer_tpl = 'Última vez en línea • {time}'
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
    
    # Show fields
    show_fields = embed_config.get('show_fields', ['game', 'views', 'duration'])
    if 'game' in show_fields:
        embed['fields'].append({
            "name": "Juego" if lang == "es" else "Game",
            "value": stream_info.get('game_name', 'Unknown Game') or 'Unknown Game',
            "inline": True
        })
    if 'views' in show_fields and not is_offline:
        embed['fields'].append({
            "name": "Espectadores" if lang == "es" else "Views",
            "value": str(stream_info.get('viewer_count', 0)),
            "inline": True
        })
    if 'duration' in show_fields:
        embed['fields'].append({
            "name": "Duración" if lang == "es" else "Duration",
            "value": formatted_duration,
            "inline": True
        })
        
    # Thumbnail (box art)
    box_art_url = stream_info.get('box_art_url') or stream_info.get('key_art_url')
    if box_art_url:
        embed['thumbnail'] = {"url": box_art_url}
        
    # Image
    if image_filename:
        embed['image'] = {"url": f"attachment://{image_filename}"}
        
    return [embed]
