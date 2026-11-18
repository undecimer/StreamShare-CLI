import os
import math
import copy
import logging
from typing import Dict, Any, List, Tuple, Optional, Union
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageColor

from .processor import ImageProcessor
from ..core.config import Config
from ..core.paths import get_logos_dir, get_repo_root

logger = logging.getLogger(__name__)

# Standard Built-in Formats
CANVAS_FORMATS = {
    "instagram_story": {"width": 1080, "height": 1920, "name": "Instagram / TikTok Story (9:16)"},
    "twitter_post": {"width": 1080, "height": 1350, "name": "Twitter / Feed Post (4:5)"},
    "twitter_landscape": {"width": 1920, "height": 1080, "name": "Landscape Banner (16:9)"},
    "discord_post": {"width": 1920, "height": 1080, "name": "Discord Post (16:9)"}
}

class SceneRenderer(ImageProcessor):
    """Next-Gen Layer-Based Graphic Compositing Engine"""

    def __init__(self):
        super().__init__()
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        backend_assets = os.path.join(backend_dir, "assets")
        self.assets_dir = backend_assets if os.path.isdir(backend_assets) else os.path.join(get_repo_root(), "assets")
        self.fonts_dir = os.path.join(self.assets_dir, "fonts")
        self.images_dir = os.path.join(self.assets_dir, "images")
        self._font_cache = {}

    def get_font(self, font_name: str = "Roboto-Regular", font_size: int = 40) -> ImageFont.FreeTypeFont:
        """Resolve and cache TrueType / OpenType font by name or path"""
        cache_key = f"{font_name}_{font_size}"
        if cache_key in self._font_cache:
            return self._font_cache[cache_key]

        font_file = None
        # 1. Direct path check
        if os.path.isfile(font_name):
            font_file = font_name
        else:
            # 2. Check in assets/fonts/
            candidates = [
                os.path.join(self.fonts_dir, f"{font_name}.ttf"),
                os.path.join(self.fonts_dir, f"{font_name}.otf"),
                os.path.join(self.fonts_dir, font_name),
                os.path.join(self.fonts_dir, "Roboto-Regular.ttf")
            ]
            for c in candidates:
                if os.path.isfile(c):
                    font_file = c
                    break

        # 3. Fallback to system fonts
        if not font_file:
            system_fallbacks = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf"
            ]
            for sf in system_fallbacks:
                if os.path.isfile(sf):
                    font_file = sf
                    break

        try:
            if font_file:
                font = ImageFont.truetype(font_file, font_size)
            else:
                font = ImageFont.load_default()
        except Exception as e:
            logger.warning(f"Failed to load font {font_name} ({e}), falling back to default")
            font = ImageFont.load_default()

        self._font_cache[cache_key] = font
        return font

    def format_text(self, text_template: str, stream_info: Dict[str, Any]) -> str:
        """Resolve universal dynamic placeholders in text strings."""
        from ..engine.templates import format_template
        return format_template(text_template, stream_info)

    @staticmethod
    def parse_color(color_val: Any, default: Tuple[int, int, int, int] = (255, 255, 255, 255)) -> Tuple[int, int, int, int]:
        """Parse Hex, RGB, RGBA strings into (R, G, B, A) tuple"""
        if not color_val:
            return default
        if isinstance(color_val, (tuple, list)):
            if len(color_val) == 3:
                return (int(color_val[0]), int(color_val[1]), int(color_val[2]), 255)
            elif len(color_val) >= 4:
                return (int(color_val[0]), int(color_val[1]), int(color_val[2]), int(color_val[3]))
            return default

        color_str = str(color_val).strip()
        try:
            if color_str.startswith("rgba"):
                parts = color_str.replace("rgba(", "").replace(")", "").split(",")
                r = int(parts[0].strip())
                g = int(parts[1].strip())
                b = int(parts[2].strip())
                a = int(float(parts[3].strip()) * 255) if float(parts[3].strip()) <= 1.0 else int(parts[3].strip())
                return (r, g, b, a)
            elif color_str.startswith("rgb"):
                parts = color_str.replace("rgb(", "").replace(")", "").split(",")
                return (int(parts[0].strip()), int(parts[1].strip()), int(parts[2].strip()), 255)
            elif color_str.startswith("#"):
                rgb = ImageColor.getrgb(color_str)
                return (rgb[0], rgb[1], rgb[2], 255)
            else:
                rgb = ImageColor.getrgb(color_str)
                return (rgb[0], rgb[1], rgb[2], 255)
        except Exception:
            return default

    # =========================================================================
    # Schema Migration: Convert Legacy v1 Configs to v2 SceneGraph
    # =========================================================================
    @classmethod
    def migrate_v1_to_v2(cls, v1_config: Dict[str, Any], format_type: str = "instagram_story") -> Dict[str, Any]:
        """Convert a v1 preset dictionary into a clean v2 SceneGraph JSON specification"""
        if not v1_config:
            v1_config = {}

        # If already v2 scene graph for this format:
        if format_type in v1_config and isinstance(v1_config[format_type], dict) and "layers" in v1_config[format_type]:
            return v1_config[format_type]

        format_spec = CANVAS_FORMATS.get(format_type, CANVAS_FORMATS["instagram_story"])
        width = format_spec["width"]
        height = format_spec["height"]

        # Extract relevant sub-config
        cfg = v1_config.get(format_type, {}) if isinstance(v1_config.get(format_type), dict) else {}

        layers = []

        if format_type == "instagram_story":
            # 1. Top Blur Band
            if cfg.get("show_top_band", True):
                layers.append({
                    "id": "top_band_blur",
                    "type": "background_blur_band",
                    "position": {"x": 0, "y": 0, "w": width, "h": 180},
                    "blur_radius": 10,
                    "head_overlay": True,
                    "visible": True,
                    "locked": False
                })

            # 2. DIRECTO text
            if cfg.get("show_directo", True):
                directo_y = (height // 2) + int(cfg.get("directo_y_offset", -100))
                layers.append({
                    "id": "directo_text",
                    "name": "Live Indicator",
                    "type": "text",
                    "text_template": cfg.get("directo_text", "DIRECTO"),
                    "position": {"x": width // 2, "y": directo_y},
                    "anchor": "center",
                    "font_family": "Roboto-Regular",
                    "font_size": int(cfg.get("directo_font_size", 40)),
                    "color": cfg.get("directo_color", "#ffffff"),
                    "shadow": {"color": "rgba(0,0,0,0.7)", "blur": 5, "offset_x": 3, "offset_y": 3},
                    "text_transform": "uppercase",
                    "visible": True
                })

            # 3. Game Title
            if cfg.get("show_category", True):
                category_y = (height // 2) + int(cfg.get("category_y_offset", 50))
                layers.append({
                    "id": "category_text",
                    "name": "Game Title",
                    "type": "text",
                    "text_template": cfg.get("category_text_template", "{game}"),
                    "position": {"x": width // 2, "y": category_y},
                    "anchor": "center",
                    "font_family": "Roboto-Regular",
                    "font_size": int(cfg.get("category_font_size", 80)),
                    "color": cfg.get("category_color", "#ffffff"),
                    "shadow": {"color": "rgba(0,0,0,0.7)", "blur": 7, "offset_x": 4, "offset_y": 4},
                    "max_width": 1000,
                    "wrap": True,
                    "visible": True
                })

            # 4. Link Sticker
            if cfg.get("show_link_sticker", True):
                sticker_y = int(cfg.get("link_sticker_y", 1280))
                layers.append({
                    "id": "link_sticker",
                    "name": "Link Sticker",
                    "style_id": "link.solid",
                    "style_group": "link.cta",
                    "type": "sticker",
                    "position": {"x": width // 2, "y": sticker_y},
                    "anchor": "center",
                    "scale": 1.0,
                    "platform": "auto",
                    "sticker_text": "VER EN {platform_upper}",
                    "link_url": "{url}",
                    "sticker_style": "solid",
                    "bg_color": "#b8b8b8",
                    "text_color": "#ffffff",
                    "border_color": "rgba(0,0,0,0)",
                    "border_width": 0,
                    "gradient_from": "#9146ff",
                    "gradient_to": "#e1306c",
                    "border_radius": 22,
                    "shadow": None,
                    "visible": True
                })

            # 5. Logo
            if cfg.get("show_logo", True):
                logo_y = int(cfg.get("logo_y", 1488))
                layers.append({
                    "id": "user_logo",
                    "name": "Brand Logo",
                    "type": "logo",
                    "position": {"x": width // 2, "y": logo_y},
                    "anchor": "center",
                    "scale": 1.0,
                    "visible": True
                })

        else:
            # Twitter & Discord Post (1080x1350)
            if cfg.get("show_directo", True):
                directo_y = (height // 2) + int(cfg.get("directo_y_offset", -100))
                layers.append({
                    "id": "directo_text",
                    "name": "Live Indicator",
                    "type": "text",
                    "text_template": cfg.get("directo_text", "DIRECTO"),
                    "position": {"x": width // 2, "y": directo_y},
                    "anchor": "center",
                    "font_family": "Roboto-Regular",
                    "font_size": int(cfg.get("directo_font_size", 40)),
                    "color": cfg.get("directo_color", "#ffffff"),
                    "shadow": {"color": "rgba(0,0,0,0.7)", "blur": 5, "offset_x": 3, "offset_y": 3},
                    "text_transform": "uppercase",
                    "visible": True
                })

            if cfg.get("show_category", True):
                category_y = (height // 2) + int(cfg.get("category_y_offset", 20))
                layers.append({
                    "id": "category_text",
                    "name": "Game Title",
                    "type": "text",
                    "text_template": "{game}",
                    "position": {"x": width // 2, "y": category_y},
                    "anchor": "center",
                    "font_family": "Roboto-Regular",
                    "font_size": int(cfg.get("category_font_size", 80)),
                    "color": cfg.get("category_color", "#ffffff"),
                    "shadow": {"color": "rgba(0,0,0,0.7)", "blur": 7, "offset_x": 4, "offset_y": 4},
                    "max_width": 1000,
                    "wrap": True,
                    "visible": True
                })

            if cfg.get("show_logo", True):
                # Bottom right logo with 20px padding
                logo_w = int(width * 0.12)
                layers.append({
                    "id": "user_logo",
                    "name": "Brand Logo",
                    "type": "logo",
                    "position": {"x": width - logo_w - 20, "y": height - logo_w - 20},
                    "anchor": "top-left",
                    "scale": 1.0,
                    "visible": True
                })

        scene_graph = {
            "version": 2,
            "format": format_type,
            "width": width,
            "height": height,
            "background": {
                "source": "auto",
                "blur": 0,
                "darken": 0.0
            },
            "layers": layers
        }

        return scene_graph

    # =========================================================================
    # Master Render Pipeline
    # =========================================================================
    def render_scene(
        self,
        scene_graph: Dict[str, Any],
        stream_info: Dict[str, Any],
        output_path: Optional[str] = None
    ) -> Image.Image:
        """Render complete multi-layer Scene Graph with PIL vector/raster compositing"""
        width = int(scene_graph.get("width", 1080))
        height = int(scene_graph.get("height", 1920))

        # 1. Base Image / Background Resolution
        canvas = self._render_background(scene_graph.get("background", {}), stream_info, width, height)

        # 2. Render Layers in Order
        layers = scene_graph.get("layers", [])
        for layer in layers:
            if not layer.get("visible", True):
                continue

            ltype = layer.get("type", "text")
            try:
                if ltype == "background_blur_band":
                    canvas = self._render_layer_blur_band(canvas, layer)
                elif ltype == "gradient_overlay":
                    canvas = self._render_layer_gradient(canvas, layer)
                elif ltype == "shape":
                    canvas = self._render_layer_shape(canvas, layer)
                elif ltype == "badge":
                    canvas = self._render_layer_badge(canvas, layer, stream_info)
                elif ltype == "text":
                    canvas = self._render_layer_text(canvas, layer, stream_info)
                elif ltype == "social_pill":
                    canvas = self._render_layer_social_pill(canvas, layer, stream_info)
                elif ltype == "sticker":
                    canvas = self._render_layer_sticker(canvas, layer, stream_info)
                elif ltype in ("instagram_mention", "instagram_hashtag"):
                    canvas = self._render_layer_instagram_tag(canvas, layer, stream_info)
                elif ltype == "logo":
                    canvas = self._render_layer_logo(canvas, layer, stream_info)
                elif ltype in ("image", "game_art", "cover", "keyart"):
                    canvas = self._render_layer_game_art(canvas, layer, stream_info)
            except Exception as e:
                logger.error(f"Error rendering layer {layer.get('id', 'unknown')} ({ltype}): {e}")

        # 3. Final RGB Conversion & Save
        if canvas.mode == 'RGBA':
            bg = Image.new('RGB', canvas.size, (0, 0, 0))
            bg.paste(canvas, mask=canvas.split()[3])
            canvas = bg

        if output_path:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            canvas.save(output_path, "JPEG", quality=95)
            logger.debug(f"Saved rendered scene to {output_path}")

        return canvas

    # =========================================================================
    # Layer Compositors
    # =========================================================================
    def _render_background(self, bg_cfg: Dict[str, Any], stream_info: Dict[str, Any], width: int, height: int) -> Image.Image:
        """Resolve, crop, and filter background artwork / frame with full transform and color controls"""
        if bg_cfg.get("visible") is False:
            return Image.new('RGBA', (width, height), (0, 0, 0, 255))

        # Base underlay tint
        tint_color = self.parse_color(bg_cfg.get("tint_color", "#101116"))
        canvas = Image.new('RGBA', (width, height), tint_color)

        mode = bg_cfg.get("mode", "artwork")
        if mode == "color":
            return canvas

        # Determine image URL
        use_keyart = stream_info.get('use_keyart', False) or mode == "artwork"
        use_boxart = stream_info.get('use_boxart', False) or mode == "cover"

        if mode == "custom" and bg_cfg.get("image_url"):
            image_url = bg_cfg["image_url"]
            source_kind = 'custom'
        elif use_keyart and stream_info.get('key_art_url'):
            image_url = stream_info.get('cached_image', stream_info['key_art_url'])
            source_kind = 'keyart'
        elif use_boxart and (stream_info.get('box_art_url') or stream_info.get('cached_image')):
            image_url = stream_info.get('cached_image', stream_info.get('box_art_url'))
            source_kind = 'boxart'
        else:
            image_url = stream_info.get('cached_image') or stream_info.get('thumbnail_url')
            source_kind = 'thumbnail'

        if not image_url:
            return canvas

        try:
            image = self.load_image(image_url).convert('RGBA')
            if source_kind == 'thumbnail':
                source_aspect_ratio = str(stream_info.get('source_aspect_ratio') or 'auto').lower()
                crop_anchor = str(stream_info.get('crop_anchor') or 'center').lower()
            else:
                source_aspect_ratio = 'auto'
                crop_anchor = 'center'
            image = self.unsquish_image(image, source_aspect_ratio)

            # Handle scale and crop
            scale = float(bg_cfg.get("scale", 1.0))
            off_x = int(bg_cfg.get("offset_x", 0))
            off_y = int(bg_cfg.get("offset_y", 0))

            img_ratio = image.width / image.height
            canvas_ratio = width / height

            if img_ratio > canvas_ratio:
                draw_h = int(height * scale)
                draw_w = int(height * scale * img_ratio)
            else:
                draw_w = int(width * scale)
                draw_h = int((width * scale) / img_ratio)

            scaled_img = image.resize((draw_w, draw_h), Image.Resampling.LANCZOS)

            # Rotation & Flips
            if bg_cfg.get("flip_h"):
                scaled_img = scaled_img.transpose(Image.FLIP_LEFT_RIGHT)
            if bg_cfg.get("flip_v"):
                scaled_img = scaled_img.transpose(Image.FLIP_TOP_BOTTOM)

            rot = int(bg_cfg.get("rotation", 0))
            if rot:
                scaled_img = scaled_img.rotate(-rot, expand=True, resample=Image.Resampling.BICUBIC)

            # Grayscale filter
            grayscale = float(bg_cfg.get("grayscale", 0.0))
            if grayscale > 0:
                gray = scaled_img.convert('L').convert('RGBA')
                scaled_img = Image.blend(scaled_img, gray, grayscale)

            # Blur
            blur_val = int(bg_cfg.get("blur", 0))
            if blur_val > 0:
                scaled_img = scaled_img.filter(ImageFilter.GaussianBlur(radius=blur_val))

            # Opacity
            opacity = float(bg_cfg.get("opacity", 1.0))
            if opacity < 1.0:
                r, g, b, a = scaled_img.split()
                a = a.point(lambda p: int(p * opacity))
                scaled_img.putalpha(a)

            # Paste onto canvas with center + offset
            if scaled_img.width > width and crop_anchor == 'left':
                paste_x = 0
            elif scaled_img.width > width and crop_anchor == 'right':
                paste_x = width - scaled_img.width
            else:
                paste_x = (width - scaled_img.width) // 2

            if scaled_img.height > height and crop_anchor == 'top':
                paste_y = 0
            elif scaled_img.height > height and crop_anchor == 'bottom':
                paste_y = height - scaled_img.height
            else:
                paste_y = (height - scaled_img.height) // 2

            paste_x += off_x
            paste_y += off_y
            canvas.paste(scaled_img, (paste_x, paste_y), scaled_img)

            # Apply darken tint if configured
            darken = float(bg_cfg.get("darken", 0.0))
            if darken > 0:
                dark_layer = Image.new('RGBA', canvas.size, (0, 0, 0, int(darken * 255)))
                canvas = Image.alpha_composite(canvas, dark_layer)

            return canvas
        except Exception as e:
            logger.warning(f"Failed to load background image ({e}), using solid dark canvas")
            return canvas

    def _render_layer_blur_band(self, canvas: Image.Image, layer: Dict[str, Any]) -> Image.Image:
        """Render top/bottom blurred section and head band overlay"""
        pos = layer.get("position", {"x": 0, "y": 0, "w": canvas.width, "h": 180})
        x, y, w, h = int(pos.get("x", 0)), int(pos.get("y", 0)), int(pos.get("w", canvas.width)), int(pos.get("h", 180))
        blur_radius = int(layer.get("blur_radius", 10))

        # Crop top section and blur
        top_crop = canvas.crop((x, y, x + w, y + h))
        blurred = top_crop.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        canvas.paste(blurred, (x, y))

        # Overlay head.png if requested
        if layer.get("head_overlay", True):
            head_path = os.path.join(self.images_dir, "head.png")
            if os.path.exists(head_path):
                head_img = Image.open(head_path).convert('RGBA')
                if head_img.width != w:
                    head_img = head_img.resize((w, int(head_img.height * (w / head_img.width))))
                canvas.paste(head_img, (x, y), head_img)

        return canvas

    def _render_layer_gradient(self, canvas: Image.Image, layer: Dict[str, Any]) -> Image.Image:
        """Render multi-stop linear gradient overlay"""
        pos = layer.get("position", {"x": 0, "y": 0, "w": canvas.width, "h": canvas.height})
        x, y, w, h = int(pos.get("x", 0)), int(pos.get("y", 0)), int(pos.get("w", canvas.width)), int(pos.get("h", canvas.height))

        c_from = self.parse_color(layer.get("from_color", "rgba(0,0,0,0.85)"))
        c_to = self.parse_color(layer.get("to_color", "rgba(0,0,0,0)"))

        direction = layer.get("direction", "vertical")  # vertical or horizontal

        grad = Image.new('RGBA', (w, h))
        draw = ImageDraw.Draw(grad)

        if direction == "vertical":
            for i in range(h):
                t = i / max(1, h - 1)
                r = int(c_from[0] + (c_to[0] - c_from[0]) * t)
                g = int(c_from[1] + (c_to[1] - c_from[1]) * t)
                b = int(c_from[2] + (c_to[2] - c_from[2]) * t)
                a = int(c_from[3] + (c_to[3] - c_from[3]) * t)
                draw.line([(0, i), (w, i)], fill=(r, g, b, a))
        else:
            for i in range(w):
                t = i / max(1, w - 1)
                r = int(c_from[0] + (c_to[0] - c_from[0]) * t)
                g = int(c_from[1] + (c_to[1] - c_from[1]) * t)
                b = int(c_from[2] + (c_to[2] - c_from[2]) * t)
                a = int(c_from[3] + (c_to[3] - c_from[3]) * t)
                draw.line([(i, 0), (i, h)], fill=(r, g, b, a))

        canvas.paste(grad, (x, y), grad)
        return canvas

    def _render_layer_shape(self, canvas: Image.Image, layer: Dict[str, Any]) -> Image.Image:
        """Render rectangle, rounded box, or divider line"""
        pos = layer.get("position", {"x": 0, "y": 0, "w": 200, "h": 50})
        x, y, w, h = int(pos.get("x", 0)), int(pos.get("y", 0)), int(pos.get("w", 200)), int(pos.get("h", 50))
        radius = int(layer.get("border_radius", 0))

        fill_color = self.parse_color(layer.get("fill_color", "#ffffff"))
        stroke_color = self.parse_color(layer.get("stroke_color", None), (0, 0, 0, 0))
        stroke_width = int(layer.get("stroke_width", 0))

        overlay = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        shape_type = layer.get("shape_type", "rounded_rect")
        box = [x, y, x + w, y + h]

        shadow_cfg = layer.get("shadow")
        if shadow_cfg:
            s_color = self.parse_color(shadow_cfg.get("color", "rgba(0,0,0,0.5)"))
            s_blur = int(shadow_cfg.get("blur", 12))
            s_off_x = int(shadow_cfg.get("offset_x", shadow_cfg.get("x", 0)))
            s_off_y = int(shadow_cfg.get("offset_y", shadow_cfg.get("y", 4)))
            s_img = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
            s_draw = ImageDraw.Draw(s_img)
            s_box = [x + s_off_x, y + s_off_y, x + w + s_off_x, y + h + s_off_y]
            if shape_type == "circle":
                s_draw.ellipse(s_box, fill=s_color)
            elif radius > 0:
                s_draw.rounded_rectangle(s_box, radius=radius, fill=s_color)
            else:
                s_draw.rectangle(s_box, fill=s_color)
            if s_blur > 0:
                s_img = s_img.filter(ImageFilter.GaussianBlur(radius=s_blur))
            canvas = Image.alpha_composite(canvas, s_img)
            overlay = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)

        if shape_type == "circle":
            draw.ellipse(box, fill=fill_color, outline=stroke_color if stroke_width > 0 else None, width=stroke_width)
        elif radius > 0:
            draw.rounded_rectangle(box, radius=radius, fill=fill_color, outline=stroke_color if stroke_width > 0 else None, width=stroke_width)
        else:
            draw.rectangle(box, fill=fill_color, outline=stroke_color if stroke_width > 0 else None, width=stroke_width)

        return Image.alpha_composite(canvas, overlay)

    def _render_layer_text(self, canvas: Image.Image, layer: Dict[str, Any], stream_info: Dict[str, Any]) -> Image.Image:
        """Render typography with dynamic placeholders, wrapping, outlines, and Gaussian shadows"""
        raw_template = layer.get("text_template") or layer.get("text") or ""
        text = self.format_text(raw_template, stream_info)

        transform = layer.get("text_transform", "none")
        if transform == "uppercase":
            text = text.upper()
        elif transform == "lowercase":
            text = text.lower()
        elif transform == "capitalize":
            text = text.title()

        if not text:
            return canvas

        font_name = layer.get("font_family", "Roboto-Regular")
        font_size = int(layer.get("font_size", 40))
        font = self.get_font(font_name, font_size)

        color = self.parse_color(layer.get("color", "#ffffff"))
        pos = layer.get("position", {"x": canvas.width // 2, "y": canvas.height // 2})
        x, y = int(pos.get("x", canvas.width // 2)), int(pos.get("y", canvas.height // 2))
        anchor = layer.get("anchor", "center")

        # Wrapping
        max_width = int(layer.get("max_width", 0))
        wrap = layer.get("wrap", False)
        line_spacing = int(layer.get("line_spacing", 16))

        lines = []
        dummy_draw = ImageDraw.Draw(canvas)

        if wrap and max_width > 0:
            words = text.split()
            if words:
                cur_line = words[0]
                for w in words[1:]:
                    test_line = f"{cur_line} {w}"
                    bbox = dummy_draw.textbbox((0, 0), test_line, font=font)
                    if (bbox[2] - bbox[0]) <= max_width:
                        cur_line = test_line
                    else:
                        lines.append(cur_line)
                        cur_line = w
                lines.append(cur_line)
            else:
                lines = [text]
        else:
            lines = text.split("\n")

        # Calculate total bounding dimensions
        line_metrics = []
        total_height = 0
        max_line_width = 0

        for line in lines:
            bbox = dummy_draw.textbbox((0, 0), line, font=font)
            lw = bbox[2] - bbox[0]
            lh = bbox[3] - bbox[1]
            line_metrics.append((line, lw, lh))
            max_line_width = max(max_line_width, lw)
            total_height += lh + line_spacing

        total_height -= line_spacing

        # Anchor origin calculation
        if anchor == "center":
            origin_x = x - (max_line_width // 2)
            origin_y = y - (total_height // 2)
        elif anchor == "top-center":
            origin_x = x - (max_line_width // 2)
            origin_y = y
        elif anchor == "bottom-center":
            origin_x = x - (max_line_width // 2)
            origin_y = y - total_height
        else:
            origin_x = x
            origin_y = y

        # Shadow Layer
        shadow_cfg = layer.get("shadow")
        if shadow_cfg:
            shadow_color = self.parse_color(shadow_cfg.get("color", "rgba(0,0,0,0.8)"))
            blur_radius = int(shadow_cfg.get("blur", 6))
            off_x = int(shadow_cfg.get("offset_x", shadow_cfg.get("x", 0)))
            off_y = int(shadow_cfg.get("offset_y", shadow_cfg.get("y", 4)))

            shadow_img = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
            shadow_draw = ImageDraw.Draw(shadow_img)

            cur_y = origin_y + off_y
            for line, lw, lh in line_metrics:
                # Text alignment within bounding box
                align = layer.get("align", "center")
                if align == "center":
                    line_x = origin_x + off_x + ((max_line_width - lw) // 2)
                elif align == "right":
                    line_x = origin_x + off_x + (max_line_width - lw)
                else:
                    line_x = origin_x + off_x

                shadow_draw.text((line_x, cur_y), line, font=font, fill=shadow_color)
                cur_y += lh + line_spacing

            if blur_radius > 0:
                shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(radius=blur_radius))

            canvas = Image.alpha_composite(canvas, shadow_img)

        # Stroke & Text Layer
        text_img = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
        text_draw = ImageDraw.Draw(text_img)

        stroke_cfg = layer.get("stroke")
        if layer.get("stroke_color") is not None or layer.get("stroke_width") is not None:
            stroke_color = self.parse_color(layer.get("stroke_color", "#000000"))
            stroke_width = int(layer.get("stroke_width", 0) or 0)
        else:
            stroke_color = self.parse_color(stroke_cfg.get("color", "#000000")) if stroke_cfg else None
            stroke_width = int(stroke_cfg.get("width", 0)) if stroke_cfg else 0

        cur_y = origin_y
        for line, lw, lh in line_metrics:
            align = layer.get("align", "center")
            if align == "center":
                line_x = origin_x + ((max_line_width - lw) // 2)
            elif align == "right":
                line_x = origin_x + (max_line_width - lw)
            else:
                line_x = origin_x

            if stroke_width > 0 and stroke_color:
                text_draw.text(
                    (line_x, cur_y),
                    line,
                    font=font,
                    fill=color,
                    stroke_width=stroke_width,
                    stroke_fill=stroke_color
                )
            else:
                text_draw.text((line_x, cur_y), line, font=font, fill=color)

            cur_y += lh + line_spacing

        return Image.alpha_composite(canvas, text_img)

    def _render_layer_badge(self, canvas: Image.Image, layer: Dict[str, Any], stream_info: Dict[str, Any]) -> Image.Image:
        """Render live badge / pill with glowing background box and text"""
        raw_text = layer.get("text_template") or layer.get("text") or "🔴 LIVE"
        text = self.format_text(raw_text, stream_info)

        font_name = layer.get("font_family", "Roboto-Bold")
        font_size = int(layer.get("font_size", 34))
        font = self.get_font(font_name, font_size)

        bg_color = self.parse_color(layer.get("bg_color", "#e0245e"))
        text_color = self.parse_color(layer.get("text_color", "#ffffff"))
        radius = int(layer.get("border_radius", 12))

        pad_x, pad_y = int(layer.get("padding_x", 20)), int(layer.get("padding_y", 10))

        dummy_draw = ImageDraw.Draw(canvas)
        bbox = dummy_draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        badge_w = text_w + (pad_x * 2)
        badge_h = text_h + (pad_y * 2)

        pos = layer.get("position", {"x": canvas.width // 2, "y": 250})
        x, y = int(pos.get("x", canvas.width // 2)), int(pos.get("y", 250))
        anchor = layer.get("anchor", "center")

        if anchor == "center":
            bx = x - (badge_w // 2)
            by = y - (badge_h // 2)
        else:
            bx = x
            by = y

        # Glow / Shadow
        shadow_cfg = layer.get("shadow")
        if shadow_cfg:
            s_color = self.parse_color(shadow_cfg.get("color", "rgba(224,36,94,0.5)"))
            s_blur = int(shadow_cfg.get("blur", 12))
            s_off_x = int(shadow_cfg.get("offset_x", shadow_cfg.get("x", 0)))
            s_off_y = int(shadow_cfg.get("offset_y", shadow_cfg.get("y", 4)))

            shadow_img = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
            s_draw = ImageDraw.Draw(shadow_img)
            s_draw.rounded_rectangle([bx + s_off_x, by + s_off_y, bx + badge_w + s_off_x, by + badge_h + s_off_y], radius=radius, fill=s_color)
            if s_blur > 0:
                shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(radius=s_blur))
            canvas = Image.alpha_composite(canvas, shadow_img)

        # Badge Box & Text
        border_color = self.parse_color(layer.get("border_color"), (0, 0, 0, 0))
        border_width = int(layer.get("border_width", 0) or 0)

        badge_img = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
        b_draw = ImageDraw.Draw(badge_img)
        b_draw.rounded_rectangle(
            [bx, by, bx + badge_w, by + badge_h],
            radius=radius,
            fill=bg_color,
            outline=border_color if border_width > 0 else None,
            width=border_width
        )
        b_draw.text((bx + pad_x, by + pad_y - 2), text, font=font, fill=text_color)

        return Image.alpha_composite(canvas, badge_img)

    SOCIAL_BRAND_COLORS = {
        "twitch": "#9146ff",
        "kick": "#53fc18",
        "youtube": "#ef4444",
        "tiktok": "#00f2fe",
        "twitter": "#ffffff",
        "x": "#ffffff",
        "discord": "#5865f2",
        "instagram": "#e1306c",
        "web": "#38bdf8",
        "url": "#38bdf8"
    }
    SOCIAL_INITIALS = {
        "twitch": "T",
        "kick": "K",
        "youtube": "Y",
        "tiktok": "T",
        "twitter": "X",
        "x": "X",
        "discord": "D",
        "instagram": "I",
        "web": "W",
        "url": "W"
    }

    def _render_layer_social_pill(self, canvas: Image.Image, layer: Dict[str, Any], stream_info: Dict[str, Any]) -> Image.Image:
        """Render social handle badge pill with brand-colored circle and platform initial"""
        platform = str(layer.get("platform", "twitch")).lower()
        raw_handle = layer.get("handle") or "{username}"
        handle_text = self.format_text(raw_handle, stream_info)
        if not handle_text.startswith("@") and platform not in ("url", "web"):
            handle_text = f"@{handle_text}"

        font_size = int(layer.get("font_size", 32))
        font = self.get_font(layer.get("font_family", "Roboto-Bold"), font_size)

        icon_size = int(font_size * 0.9)
        gap = 14
        pad_x = 24
        pad_y = 12

        dummy_draw = ImageDraw.Draw(canvas)
        bbox = dummy_draw.textbbox((0, 0), handle_text, font=font)
        text_w = bbox[2] - bbox[0]
        pill_w = text_w + icon_size + gap + pad_x * 2
        pill_h = max(icon_size, font_size) + pad_y * 2

        pos = layer.get("position", {"x": canvas.width // 2, "y": 1100})
        x, y = int(pos.get("x", canvas.width // 2)), int(pos.get("y", 1100))
        bx = x - (pill_w // 2)
        by = y - (pill_h // 2)

        pill_style = str(layer.get("pill_style") or "glass")
        radius = int(layer.get("border_radius", 24) or 0)
        brand_hex = self.SOCIAL_BRAND_COLORS.get(platform, "#a855f7")
        brand_color = self.parse_color(layer.get("icon_color") or brand_hex)
        border_width = int(float(layer.get("border_width", 0) or 0))
        box = [bx, by, bx + pill_w, by + pill_h]

        pill_img = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
        p_draw = ImageDraw.Draw(pill_img)

        if pill_style == "gradient":
            g_from = self.parse_color(layer.get("gradient_from") or brand_hex)
            g_to = self.parse_color(layer.get("gradient_to") or "#1e1b4b")
            grad = self._horizontal_gradient(pill_w, pill_h, g_from, g_to)
            mask = Image.new('L', (pill_w, pill_h), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, pill_w, pill_h], radius=radius, fill=255)
            pill_img.paste(grad, (bx, by), mask)
            if border_width > 0:
                p_draw.rounded_rectangle(box, radius=radius, outline=self.parse_color(layer.get("border_color") or "rgba(255,255,255,0.2)"), width=border_width)
        elif pill_style == "solid":
            p_draw.rounded_rectangle(box, radius=radius, fill=self.parse_color(layer.get("bg_color") or brand_hex))
            if border_width > 0:
                p_draw.rounded_rectangle(box, radius=radius, outline=self.parse_color(layer.get("border_color") or "rgba(255,255,255,0.2)"), width=border_width)
        elif pill_style == "outline":
            p_draw.rounded_rectangle(box, radius=radius, fill=self.parse_color(layer.get("bg_color") or "rgba(0,0,0,0.4)"))
            outline_color = self.parse_color(layer.get("border_color") or brand_hex)
            outline_width = border_width if border_width > 0 else 3
            p_draw.rounded_rectangle(box, radius=radius, outline=outline_color, width=outline_width)
        else:
            p_draw.rounded_rectangle(box, radius=radius, fill=self.parse_color(layer.get("bg_color") or "rgba(0,0,0,0.65)"))
            glass_width = border_width if border_width > 0 else 2
            p_draw.rounded_rectangle(box, radius=radius, outline=self.parse_color(layer.get("border_color") or "rgba(255,255,255,0.16)"), width=glass_width)

        icon_cx = bx + pad_x + icon_size // 2
        icon_cy = by + pill_h // 2
        p_draw.ellipse(
            [icon_cx - icon_size // 2, icon_cy - icon_size // 2, icon_cx + icon_size // 2, icon_cy + icon_size // 2],
            fill=brand_color
        )

        initial = self.SOCIAL_INITIALS.get(platform, platform[:1].upper() or "?")
        initial_font = self.get_font(layer.get("font_family", "Roboto-Bold"), max(8, int(icon_size * 0.55)))
        p_draw.text((icon_cx, icon_cy), initial, font=initial_font, fill=(255, 255, 255, 255), anchor="mm")

        if pill_style == "solid" and platform in ("kick", "tiktok"):
            default_text = "#000000"
        else:
            default_text = "#ffffff"
        text_color = self.parse_color(layer.get("text_color") or default_text)
        p_draw.text((bx + pad_x + icon_size + gap, icon_cy), handle_text, font=font, fill=text_color, anchor="lm")

        return Image.alpha_composite(canvas, pill_img)

    @staticmethod
    def _horizontal_gradient(width: int, height: int, color_from: Tuple[int, int, int, int], color_to: Tuple[int, int, int, int]) -> Image.Image:
        grad = Image.new('RGBA', (max(1, width), 1))
        px = grad.load()
        span = max(1, width - 1)
        for i in range(max(1, width)):
            t = i / span
            px[i, 0] = tuple(int(color_from[c] + (color_to[c] - color_from[c]) * t) for c in range(4))
        return grad.resize((max(1, width), max(1, height)))

    def _render_layer_sticker(self, canvas: Image.Image, layer: Dict[str, Any], stream_info: Dict[str, Any]) -> Image.Image:
        """Render the interactive link sticker as a dynamic rounded pill"""
        platform = str(stream_info.get('platform') or 'twitch')
        label = self.format_text(str(layer.get('sticker_text') or 'VER EN {platform_upper}'), stream_info).strip()
        if not label:
            label = f"VER EN {platform.upper()}"

        scale = float(layer.get('scale') or 1.0)
        scale = max(0.2, min(scale, 3.0))
        sw = max(120, int(canvas.width * 0.5 * scale))
        sh = max(36, int(sw * 0.24))
        radius = max(0, min(int(float(layer.get('border_radius', 22) or 0)), sh // 2))
        style = str(layer.get('sticker_style') or 'solid')

        default_bg = {
            'solid': '#b8b8b8',
            'glass': 'rgba(15,23,42,0.58)',
            'outline': 'rgba(0,0,0,0)',
            'gradient': '#9146ff',
            'minimal': 'rgba(0,0,0,0)'
        }.get(style, '#b8b8b8')
        bg_color = str(layer.get('bg_color') if layer.get('bg_color') is not None else default_bg)
        text_color = str(layer.get('text_color') or '#ffffff')
        bw_val = layer.get('border_width')
        if bw_val is None:
            border_width = 2 if style == 'glass' else (3 if style == 'outline' else 0)
        else:
            border_width = max(0, int(float(bw_val)))
        border_color = str(layer.get('border_color') or (text_color if style == 'outline' else 'rgba(255,255,255,0.28)'))
        shadow_cfg = layer.get('shadow') if isinstance(layer.get('shadow'), dict) else None

        pos = layer.get("position", {"x": canvas.width // 2, "y": int(canvas.height * 0.7)})
        x = int(float(pos.get("x", canvas.width // 2)))
        y = int(float(pos.get("y", int(canvas.height * 0.7))))
        anchor = layer.get("anchor", "center")
        if anchor == "center":
            sx, sy = x - (sw // 2), y - (sh // 2)
        else:
            sx, sy = x, y

        if style == 'glass':
            left, top = max(0, sx), max(0, sy)
            right, bottom = min(canvas.width, sx + sw), min(canvas.height, sy + sh)
            pill = Image.new('RGBA', (sw, sh), (0, 0, 0, 0))
            if right > left and bottom > top:
                region = canvas.crop((left, top, right, bottom)).convert('RGBA')
                region = region.filter(ImageFilter.GaussianBlur(radius=8))
                pill.paste(region, (left - sx, top - sy))
            mask = Image.new('L', (sw, sh), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, sw - 1, sh - 1], radius=radius, fill=255)
            tint_layer = Image.new('RGBA', (sw, sh), (0, 0, 0, 0))
            ImageDraw.Draw(tint_layer).rounded_rectangle([0, 0, sw - 1, sh - 1], radius=radius, fill=self.parse_color(bg_color))
            pill = Image.alpha_composite(pill, tint_layer)
            pill.putalpha(mask)
        else:
            pill = Image.new('RGBA', (sw, sh), (0, 0, 0, 0))
            if style not in ('outline', 'minimal'):
                fill_img = Image.new('RGBA', (sw, sh), (0, 0, 0, 0))
                fill_draw = ImageDraw.Draw(fill_img)
                if style == 'gradient':
                    grad = self._horizontal_gradient(
                        sw, sh,
                        self.parse_color(layer.get('gradient_from') or '#9146ff'),
                        self.parse_color(layer.get('gradient_to') or '#e1306c')
                    )
                    mask = Image.new('L', (sw, sh), 0)
                    ImageDraw.Draw(mask).rounded_rectangle([0, 0, sw - 1, sh - 1], radius=radius, fill=255)
                    fill_img.paste(grad, (0, 0), mask)
                else:
                    fill_draw.rounded_rectangle([0, 0, sw - 1, sh - 1], radius=radius, fill=self.parse_color(bg_color))
                pill = Image.alpha_composite(pill, fill_img)

        draw = ImageDraw.Draw(pill)
        if border_width > 0:
            draw.rounded_rectangle(
                [0, 0, sw - 1, sh - 1], radius=radius,
                outline=self.parse_color(border_color), width=border_width
            )

        icon_box = max(32, int(sh * 0.7))
        icon_canvas = Image.new('RGBA', (icon_box, icon_box), (0, 0, 0, 0))
        icon_draw = ImageDraw.Draw(icon_canvas)
        link_w = max(8, int(icon_box * 0.42))
        link_h = max(6, int(icon_box * 0.24))
        outline_w = max(2, int(sh * 0.05))
        cx, cy = icon_box // 2, icon_box // 2
        off = max(2, int(icon_box * 0.10))
        icon_draw.rounded_rectangle(
            [cx - off - link_w // 2, cy - link_h // 2, cx - off + link_w // 2, cy + link_h // 2],
            radius=link_h // 2, outline=text_color, width=outline_w
        )
        icon_draw.rounded_rectangle(
            [cx + off - link_w // 2, cy - link_h // 2, cx + off + link_w // 2, cy + link_h // 2],
            radius=link_h // 2, outline=text_color, width=outline_w
        )
        icon_img = icon_canvas.rotate(-35, resample=Image.Resampling.BICUBIC, expand=True)

        font_family = layer.get('font_family', 'Montserrat')
        font_size = max(12, int(sh * 0.34))
        font = self.get_font(font_family, font_size)
        pad_x = max(10, int(sh * 0.28))
        gap = max(6, int(sh * 0.18))
        max_text_w = sw - (2 * pad_x) - icon_img.width - gap
        while font_size > 12 and draw.textlength(label, font=font) > max_text_w:
            font_size -= 1
            font = self.get_font(font_family, font_size)

        text_w = int(draw.textlength(label, font=font))
        group_w = icon_img.width + gap + text_w
        gx = max(0, (sw - group_w) // 2)
        icon_y = (sh - icon_img.height) // 2
        pill.alpha_composite(icon_img, (gx, icon_y))

        tx = gx + icon_img.width + gap
        tb = draw.textbbox((tx, 0), label, font=font)
        ty = (sh - (tb[3] - tb[1])) // 2 - tb[1]
        draw.text((tx, ty), label, font=font, fill=text_color)

        if shadow_cfg:
            s_color = self.parse_color(shadow_cfg.get("color", "rgba(0,0,0,0.5)"))
            s_blur = int(shadow_cfg.get("blur", 8) or 0)
            s_off_x = int(shadow_cfg.get("offset_x", shadow_cfg.get("x", 0)) or 0)
            s_off_y = int(shadow_cfg.get("offset_y", shadow_cfg.get("y", 3)) or 0)
            silhouette = Image.new('RGBA', (sw, sh), s_color)
            silhouette.putalpha(pill.split()[3])
            if s_blur > 0:
                silhouette = silhouette.filter(ImageFilter.GaussianBlur(radius=s_blur))
            canvas.paste(silhouette, (sx + s_off_x, sy + s_off_y), silhouette)

        canvas.paste(pill, (sx, sy), pill)
        return canvas

    def _render_layer_instagram_tag(self, canvas: Image.Image, layer: Dict[str, Any], stream_info: Dict[str, Any]) -> Image.Image:
        """Render an Instagram mention/hashtag Story tag as a styled rounded pill"""
        is_mention = layer.get("type") == "instagram_mention"
        raw_field = layer.get("mention_username" if is_mention else "hashtag_name") or ""
        raw_value = self.format_text(str(raw_field), stream_info).strip().lstrip("@" if is_mention else "#")
        if not raw_value:
            return canvas
        label = ("@" if is_mention else "#") + raw_value

        scale = max(0.2, min(float(layer.get('scale', 0.8) or 0.8), 3.0))
        tw = max(120, int(canvas.width * 0.42 * scale))
        th = max(34, int(tw * 0.16))
        radius = max(0, min(int(float(layer.get('border_radius', 24) or 0)), th // 2))
        style = str(layer.get('pill_style') or ('gradient' if is_mention else 'glass'))

        default_bg = {
            'solid': '#833ab4',
            'glass': 'rgba(15,23,42,0.58)',
            'outline': 'rgba(0,0,0,0)',
            'gradient': '#833ab4',
            'minimal': 'rgba(0,0,0,0)'
        }.get(style, '#833ab4')
        bg_color = str(layer.get('bg_color') if layer.get('bg_color') is not None else default_bg)
        text_color = str(layer.get('text_color') or '#ffffff')
        bw_val = layer.get('border_width')
        if bw_val is None:
            border_width = 2 if style == 'glass' else (3 if style == 'outline' else 0)
        else:
            border_width = max(0, int(float(bw_val)))
        border_color = str(layer.get('border_color') or (text_color if style == 'outline' else 'rgba(255,255,255,0.28)'))
        shadow_cfg = layer.get('shadow') if isinstance(layer.get('shadow'), dict) else None

        pos = layer.get("position", {"x": canvas.width // 2, "y": canvas.height // 2})
        x = int(float(pos.get("x", canvas.width // 2)))
        y = int(float(pos.get("y", canvas.height // 2)))
        anchor = layer.get("anchor", "center")
        if anchor == "center":
            sx, sy = x - (tw // 2), y - (th // 2)
        else:
            sx, sy = x, y

        if style == 'glass':
            left, top = max(0, sx), max(0, sy)
            right, bottom = min(canvas.width, sx + tw), min(canvas.height, sy + th)
            pill = Image.new('RGBA', (tw, th), (0, 0, 0, 0))
            if right > left and bottom > top:
                region = canvas.crop((left, top, right, bottom)).convert('RGBA')
                region = region.filter(ImageFilter.GaussianBlur(radius=8))
                pill.paste(region, (left - sx, top - sy))
            mask = Image.new('L', (tw, th), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, tw - 1, th - 1], radius=radius, fill=255)
            tint_layer = Image.new('RGBA', (tw, th), (0, 0, 0, 0))
            ImageDraw.Draw(tint_layer).rounded_rectangle([0, 0, tw - 1, th - 1], radius=radius, fill=self.parse_color(bg_color))
            pill = Image.alpha_composite(pill, tint_layer)
            pill.putalpha(mask)
        else:
            pill = Image.new('RGBA', (tw, th), (0, 0, 0, 0))
            if style not in ('outline', 'minimal'):
                fill_img = Image.new('RGBA', (tw, th), (0, 0, 0, 0))
                fill_draw = ImageDraw.Draw(fill_img)
                if style == 'gradient':
                    grad = self._horizontal_gradient(
                        tw, th,
                        self.parse_color(layer.get('gradient_from') or '#833ab4'),
                        self.parse_color(layer.get('gradient_to') or '#e1306c')
                    )
                    mask = Image.new('L', (tw, th), 0)
                    ImageDraw.Draw(mask).rounded_rectangle([0, 0, tw - 1, th - 1], radius=radius, fill=255)
                    fill_img.paste(grad, (0, 0), mask)
                else:
                    fill_draw.rounded_rectangle([0, 0, tw - 1, th - 1], radius=radius, fill=self.parse_color(bg_color))
                pill = Image.alpha_composite(pill, fill_img)

        draw = ImageDraw.Draw(pill)
        if border_width > 0:
            draw.rounded_rectangle(
                [0, 0, tw - 1, th - 1], radius=radius,
                outline=self.parse_color(border_color), width=border_width
            )

        font_family = layer.get('font_family', 'Montserrat')
        font_size = max(12, int(th * 0.5))
        font = self.get_font(font_family, font_size)
        pad_x = max(10, int(th * 0.35))
        max_text_w = tw - (2 * pad_x)
        while font_size > 12 and draw.textlength(label, font=font) > max_text_w:
            font_size -= 1
            font = self.get_font(font_family, font_size)

        tb = draw.textbbox((0, 0), label, font=font)
        tx = (tw - int(draw.textlength(label, font=font))) // 2
        ty = (th - (tb[3] - tb[1])) // 2 - tb[1]
        draw.text((tx, ty), label, font=font, fill=text_color)

        if shadow_cfg:
            s_color = self.parse_color(shadow_cfg.get("color", "rgba(0,0,0,0.5)"))
            s_blur = int(shadow_cfg.get("blur", 8) or 0)
            s_off_x = int(shadow_cfg.get("offset_x", shadow_cfg.get("x", 0)) or 0)
            s_off_y = int(shadow_cfg.get("offset_y", shadow_cfg.get("y", 3)) or 0)
            silhouette = Image.new('RGBA', (tw, th), s_color)
            silhouette.putalpha(pill.split()[3])
            if s_blur > 0:
                silhouette = silhouette.filter(ImageFilter.GaussianBlur(radius=s_blur))
            canvas.paste(silhouette, (sx + s_off_x, sy + s_off_y), silhouette)

        canvas.paste(pill, (sx, sy), pill)
        return canvas

    def _render_layer_logo(self, canvas: Image.Image, layer: Dict[str, Any], stream_info: Dict[str, Any]) -> Image.Image:
        """Render user or platform logo with automatic luminance detection"""
        pos = layer.get("position", {"x": canvas.width // 2, "y": 1488})
        x, y = int(pos.get("x", canvas.width // 2)), int(pos.get("y", 1488))
        anchor = layer.get("anchor", "center")
        scale = float(layer.get("scale", 1.0))

        # Detect local background luminance or respect explicit layer theme
        user_theme = layer.get("theme", "auto")
        if user_theme in ("light", "dark"):
            theme = user_theme
        else:
            avg_color = self.get_average_color(canvas.convert('RGB'))
            luminance = self.calculate_luminance(*avg_color[:3])
            theme = "light" if luminance < 127 else "dark"

        username = stream_info.get('username')
        logo_path = None

        if username:
            custom_logo = os.path.join(get_logos_dir(), f"{username}_{theme}.png")
            if os.path.exists(custom_logo):
                logo_path = custom_logo

        if not logo_path:
            logo_filename = "11-light-alpha.png" if theme == "light" else "11-dark-alpha.png"
            logo_path = os.path.join(self.images_dir, logo_filename)

        if not os.path.exists(logo_path):
            return canvas

        logo_img = Image.open(logo_path).convert('RGBA')
        base_w = int(canvas.width * 0.14 * scale)
        base_h = int(base_w * (logo_img.height / logo_img.width))
        logo_img = logo_img.resize((base_w, base_h), Image.Resampling.LANCZOS)

        if anchor == "center":
            lx = x - (base_w // 2)
            ly = y - (base_h // 2)
        elif anchor == "top-left":
            lx = x
            ly = y
        else:
            lx = x - (base_w // 2)
            ly = y

        canvas.paste(logo_img, (lx, ly), logo_img)
        return canvas

    def _render_layer_game_art(self, canvas: Image.Image, layer: Dict[str, Any], stream_info: Dict[str, Any]) -> Image.Image:
        """Render Game Box Art / Key Art / Custom Image layer with object-fit, rounded corners, borders, and shadows"""
        asset_type = layer.get("asset_type", "cover")
        img_src = layer.get("image_url") or layer.get("src")

        if not img_src:
            if asset_type in ("cover", "boxart"):
                img_src = stream_info.get("box_art_url") or stream_info.get("cached_image") or stream_info.get("key_art_url")
            else:
                img_src = stream_info.get("key_art_url") or stream_info.get("cached_image") or stream_info.get("box_art_url")

        if not img_src:
            return canvas

        try:
            raw_img = self.load_image(img_src).convert('RGBA')
            pos = layer.get("position", {"x": 340, "y": 400, "w": 400, "h": 560})
            x, y = int(pos.get("x", 0)), int(pos.get("y", 0))
            w = int(pos.get("w", 400 if asset_type == "cover" else 640))
            h = int(pos.get("h", 560 if asset_type == "cover" else 360))
            radius = int(layer.get("border_radius", 0))
            object_fit = layer.get("object_fit", "cover")

            # Scale / Crop based on object_fit
            if object_fit == "fill":
                img = raw_img.resize((w, h), Image.Resampling.LANCZOS)
            elif object_fit == "contain":
                img_ratio = raw_img.width / raw_img.height
                box_ratio = w / h
                if box_ratio > img_ratio:
                    dw = int(h * img_ratio)
                    dh = h
                else:
                    dw = w
                    dh = int(w / img_ratio)
                scaled = raw_img.resize((dw, dh), Image.Resampling.LANCZOS)
                img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
                img.paste(scaled, ((w - dw) // 2, (h - dh) // 2))
            else: # cover
                img_ratio = raw_img.width / raw_img.height
                box_ratio = w / h
                if box_ratio > img_ratio:
                    sh = int(raw_img.width / box_ratio)
                    sy = (raw_img.height - sh) // 2
                    cropped = raw_img.crop((0, sy, raw_img.width, sy + sh))
                else:
                    sw = int(raw_img.height * box_ratio)
                    sx = (raw_img.width - sw) // 2
                    cropped = raw_img.crop((sx, 0, sx + sw, raw_img.height))
                img = cropped.resize((w, h), Image.Resampling.LANCZOS)

            # Apply Rounded Mask if radius > 0
            if radius > 0:
                mask = Image.new('L', (w, h), 0)
                mask_draw = ImageDraw.Draw(mask)
                mask_draw.rounded_rectangle([(0, 0), (w, h)], radius=radius, fill=255)
                img.putalpha(mask)

            # Render Drop Shadow if configured
            shadow_cfg = layer.get("shadow")
            if shadow_cfg:
                s_color = self.parse_color(shadow_cfg.get("color", "rgba(0,0,0,0.6)"))
                s_blur = int(shadow_cfg.get("blur", 20))
                s_off_y = int(shadow_cfg.get("y", shadow_cfg.get("offset_y", 8)))
                s_off_x = int(shadow_cfg.get("x", shadow_cfg.get("offset_x", 0)))

                s_img = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
                s_draw = ImageDraw.Draw(s_img)
                sx0, sy0 = x + s_off_x, y + s_off_y
                if radius > 0:
                    s_draw.rounded_rectangle([(sx0, sy0), (sx0 + w, sy0 + h)], radius=radius, fill=s_color)
                else:
                    s_draw.rectangle([(sx0, sy0), (sx0 + w, sy0 + h)], fill=s_color)

                if s_blur > 0:
                    s_img = s_img.filter(ImageFilter.GaussianBlur(radius=s_blur))
                canvas = Image.alpha_composite(canvas, s_img)

            # Paste image onto canvas
            canvas.paste(img, (x, y), img)

            # Render Border if configured
            stroke_w = int(layer.get("border_width", layer.get("stroke_width", 0)))
            stroke_col = layer.get("border_color") or layer.get("stroke_color")
            if stroke_w > 0 and stroke_col:
                border_img = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
                b_draw = ImageDraw.Draw(border_img)
                b_color = self.parse_color(stroke_col)
                if radius > 0:
                    b_draw.rounded_rectangle([(x, y), (x + w, y + h)], radius=radius, outline=b_color, width=stroke_w)
                else:
                    b_draw.rectangle([(x, y), (x + w, y + h)], outline=b_color, width=stroke_w)
                canvas = Image.alpha_composite(canvas, border_img)

        except Exception as e:
            logger.warning(f"Failed to render game art layer: {e}")

        return canvas
