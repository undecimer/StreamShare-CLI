import os
import copy
import logging
import asyncio
from typing import Dict, Any
from .instagram import InstagramImageProcessor
from .scene_renderer import SceneRenderer

logger = logging.getLogger(__name__)

# Layers that encode Instagram-specific affordances (link sticker mock,
# @mention/#hashtag tags). TikTok Stories can't render these natively —
# the real link sticker is added by the user inside the TikTok app.
_STRIPPED_LAYER_TYPES = {"sticker", "instagram_mention", "instagram_hashtag"}

_DEFAULT_CTA = {
    "es": "DIRECTO — LINK EN LA BIO",
    "en": "LIVE — LINK IN BIO",
}


class TikTokStoryImageProcessor(InstagramImageProcessor):
    """9:16 story renderer for TikTok: reuses the Instagram Story canvas but
    removes the burned-in Instagram sticker/tag layers and replaces them with
    a clean call-to-action badge so the story still converts when the user
    skips adding TikTok's native link sticker."""

    def process_stream_image_sync(self, stream_info: Dict[str, Any], output_path: str) -> None:
        logger.info(f"Generating TikTok Story ({self.width}x{self.height})...")
        customizer_config = copy.deepcopy(stream_info.get('customizer_config') or {})

        renderer = SceneRenderer()

        # An explicit v2 scene authored for tiktok_story wins outright;
        # otherwise derive the Instagram Story scene and re-skin it.
        tt_cfg = customizer_config.get('tiktok_story')
        if isinstance(tt_cfg, dict) and isinstance(tt_cfg.get('layers'), list):
            scene_graph = tt_cfg
        else:
            scene_graph = renderer.migrate_v1_to_v2(customizer_config, "instagram_story")
            scene_graph["format"] = "tiktok_story"
            self._reskin_scene(scene_graph, customizer_config, stream_info)

        renderer.render_scene(scene_graph, stream_info, output_path)
        logger.info(f"Saved TikTok Story: {os.path.basename(output_path)}")

    def _reskin_scene(self, scene_graph: Dict[str, Any], customizer_config: Dict[str, Any], stream_info: Dict[str, Any]) -> None:
        layers = [l for l in scene_graph.get("layers", []) if l.get("type") not in _STRIPPED_LAYER_TYPES]

        width = int(scene_graph.get("width") or self.width)
        sticker_y = 1280
        for l in scene_graph.get("layers", []):
            if l.get("type") == "sticker":
                sticker_y = int((l.get("position") or {}).get("y", sticker_y))
                break

        tt_opts = customizer_config.get('tiktok_story') if isinstance(customizer_config.get('tiktok_story'), dict) else {}
        language = str(stream_info.get('language') or 'en').lower()
        cta_text = tt_opts.get('cta_text') or _DEFAULT_CTA.get(language, _DEFAULT_CTA['en'])

        layers.append({
            "id": "tiktok_cta",
            "name": "CTA Banner",
            "type": "badge",
            "text_template": cta_text,
            "position": {"x": width // 2, "y": sticker_y},
            "anchor": "center",
            "font_family": "Roboto-Bold",
            "font_size": int(tt_opts.get('cta_font_size', 36)),
            "bg_color": tt_opts.get('cta_bg_color', "#fe2c55"),
            "text_color": tt_opts.get('cta_text_color', "#ffffff"),
            "border_radius": int(tt_opts.get('cta_border_radius', 16)),
            "padding_x": int(tt_opts.get('cta_padding_x', 26)),
            "padding_y": int(tt_opts.get('cta_padding_y', 14)),
            "visible": True,
        })
        scene_graph["layers"] = layers

    async def process_stream_image(self, stream_info: Dict[str, Any], output_path: str) -> None:
        await asyncio.to_thread(self.process_stream_image_sync, stream_info, output_path)
