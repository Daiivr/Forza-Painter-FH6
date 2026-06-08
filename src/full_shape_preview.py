from __future__ import annotations

import io
import json
import math
import re
from pathlib import Path

from fh6_vinyl_resources import load_vinyl_polygons
from utils import load_pillow


PREVIEW_JSON_SUPERSAMPLE = 2
FH6_CIRCLE_BASE_SIZE = 63.0
FH6_RECTANGLE_BASE_SIZE = 127.0
FH6_TYPE_CODE_BASE = 0x100000
FH6_PRIMITIVE_NAME_WORDS = {
    "square": 0x0065,
    "rectangle": 0x0065,
    "rect": 0x0065,
    "circle": 0x0066,
    "sphere": 0x0066,
    "triangle": 0x0067,
    "circle border": 0x0070,
    "circle outline": 0x0070,
    "ellipse": 0x0088,
    "oval": 0x0088,
}
FH6_TYPECODE_MARKER_KEYS = (
    "type_word",
    "typeWord",
    "shape_word",
    "shapeWord",
    "font_shape",
    "fontShape",
    "shape_name",
    "shapeName",
    "font",
    "font_index",
    "fontIndex",
    "forza_font",
    "forzaFont",
    "glyph",
    "char",
    "character",
    "text",
    "font_block",
    "fontBlock",
)


def _preview_size_tuple(max_size=None):
    if max_size is None:
        return 520, 520
    if isinstance(max_size, (tuple, list)):
        if len(max_size) >= 2:
            width, height = max_size[0], max_size[1]
        elif len(max_size) == 1:
            width = height = max_size[0]
        else:
            width = height = 520
    else:
        width = height = max_size
    try:
        width = int(width)
        height = int(height)
    except (TypeError, ValueError):
        width = height = 520
    return max(1, width), max(1, height)


def _preview_scale(width, height, max_size=None):
    max_w, max_h = _preview_size_tuple(max_size)
    if width <= 0 or height <= 0:
        return 1.0
    return min(max_w / width, max_h / height, 1.0)


def _pil_to_photo(image, max_size=None):
    loaded = load_pillow()
    if not loaded:
        return None
    Image, _ImageDraw = loaded
    image = image.convert("RGB")
    image.thumbnail(_preview_size_tuple(max_size), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def typecode_shape_count(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    shapes = payload.get("shapes")
    if not isinstance(shapes, list):
        raise ValueError("full-shape JSON must contain a shapes list")
    return len(shapes)


def parse_preview_int(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text, 0)
        except ValueError:
            return None
    return None


def normalize_typecode_name(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()


def shape_has_typecode_marker(shape):
    if not isinstance(shape, dict):
        return False
    if any(key in shape for key in FH6_TYPECODE_MARKER_KEYS):
        return True
    for key in ("type", "name"):
        value = shape.get(key)
        numeric = parse_preview_int(value)
        if numeric is not None and numeric >= FH6_TYPE_CODE_BASE:
            return True
        if normalize_typecode_name(value) in FH6_PRIMITIVE_NAME_WORDS:
            return True
    return False


def is_typecode_payload(payload):
    if not isinstance(payload, dict):
        return False
    fmt = str(payload.get("format") or "")
    if fmt.startswith("fh6_typecode_json"):
        return True
    source = payload.get("source") or {}
    if isinstance(source, dict) and "uint16_at_layer_0x7A" in str(source.get("type_model") or ""):
        return True
    shapes = payload.get("shapes")
    if not isinstance(shapes, list):
        return False
    return any(shape_has_typecode_marker(shape) for shape in shapes)


def is_typecode_json(path):
    try:
        return is_typecode_payload(json.loads(Path(path).read_text(encoding="utf-8")))
    except Exception:
        return False


def _typecode_preview_shapes(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not is_typecode_payload(payload):
        return None
    shapes = payload.get("shapes")
    if not isinstance(shapes, list) or not shapes:
        return None
    out = []
    for shape in shapes:
        if not isinstance(shape, dict):
            continue
        data = list(shape.get("data") or [])
        color = list(shape.get("color") or [])
        if len(data) < 4 or len(color) < 4:
            continue
        try:
            x, y, w, h = [float(v) for v in data[:4]]
            rotation = float(data[4]) if len(data) >= 5 else 0.0
            rgba = [max(0, min(255, int(round(float(v))))) for v in color[:4]]
        except (TypeError, ValueError):
            continue
        type_code = typecode_preview_type_code(shape)
        word = type_code & 0xFFFF if type_code is not None else typecode_preview_word(shape)
        if word is None or rgba[3] <= 0:
            continue
        skew = float(data[5]) if len(data) >= 6 else 0.0
        resource = load_vinyl_polygons(type_code) if type_code is not None else None
        if resource:
            polygons = [
                [_transform_typecode_point(px, py, x, y, w, h, rotation, skew) for px, py in polygon]
                for polygon in resource["polygons"]
            ]
        else:
            polygons = _fallback_typecode_polygons(x, y, w, h, rotation, skew, word & 0xFFFF)
        try:
            data_mask = bool(int(float(data[6]))) if len(data) >= 7 else False
        except (TypeError, ValueError):
            data_mask = bool(data[6]) if len(data) >= 7 else False
        is_mask = bool(shape.get("mask") or shape.get("is_mask") or shape.get("isMask") or data_mask)
        if polygons:
            out.append({"polygons": polygons, "color": rgba, "word": word & 0xFFFF, "type_code": type_code, "mask": is_mask})
    return out


def typecode_preview_type_code(shape):
    type_code = parse_preview_int(shape.get("type"))
    if type_code is not None and type_code >= FH6_TYPE_CODE_BASE:
        return type_code
    word = None
    for key in ("type_word", "typeWord", "shape_word", "shapeWord"):
        value = parse_preview_int(shape.get(key))
        if value is not None:
            word = value & 0xFFFF
            break
    if word is not None:
        return FH6_TYPE_CODE_BASE + word
    for key in ("shape_name", "shapeName", "name", "type"):
        primitive_word = FH6_PRIMITIVE_NAME_WORDS.get(normalize_typecode_name(shape.get(key)))
        if primitive_word is not None:
            return FH6_TYPE_CODE_BASE + primitive_word
    try:
        from fh6_typecode_import import load_font_registry, shape_type_fields

        resolved_type_code, resolved_word, _font_item = shape_type_fields(shape, load_font_registry())
        if resolved_type_code >= FH6_TYPE_CODE_BASE:
            return int(resolved_type_code)
        return FH6_TYPE_CODE_BASE + (int(resolved_word) & 0xFFFF)
    except Exception:
        return None


def typecode_preview_word(shape):
    type_code = typecode_preview_type_code(shape)
    if type_code is not None:
        return type_code & 0xFFFF
    for key in ("type_word", "typeWord", "shape_word", "shapeWord"):
        value = parse_preview_int(shape.get(key))
        if value is not None:
            return value & 0xFFFF
    type_code = parse_preview_int(shape.get("type"))
    if type_code is not None:
        return type_code & 0xFFFF
    for key in ("shape_name", "shapeName", "name", "type"):
        word = FH6_PRIMITIVE_NAME_WORDS.get(normalize_typecode_name(shape.get(key)))
        if word is not None:
            return word & 0xFFFF
    try:
        from fh6_typecode_import import load_font_registry, shape_type_fields

        _type_code, word, _font_item = shape_type_fields(shape, load_font_registry())
        return int(word) & 0xFFFF
    except Exception:
        return None


def _safe_nonzero_float(value, default=1.0):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(numeric) or numeric == 0.0:
        return default
    return numeric


def _transform_typecode_point(local_x, local_y, shape_x, shape_y, scale_x, scale_y, rotation, skew):
    sx = _safe_nonzero_float(scale_x)
    sy = _safe_nonzero_float(scale_y)
    x = float(local_x) * sx
    y = float(local_y) * sy
    x = x + float(skew) * y
    theta = math.radians(-float(rotation))
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    return (
        float(shape_x) + x * cos_t - y * sin_t,
        -float(shape_y) + x * sin_t + y * cos_t,
    )


def _fallback_typecode_polygons(x, y, sx, sy, rotation, skew, word):
    base_size = FH6_CIRCLE_BASE_SIZE if word in (0x66, 0x88) else FH6_RECTANGLE_BASE_SIZE
    if word in (0x66, 0x88):
        points = []
        steps = 48
        for i in range(steps):
            theta = (math.pi * 2.0 * i) / steps
            points.append((math.cos(theta) * base_size, math.sin(theta) * base_size))
        return [[_transform_typecode_point(px, py, x, y, sx, sy, rotation, skew) for px, py in points]]
    half = base_size / 2.0
    points = ((-half, -half), (half, -half), (half, half), (-half, half))
    if word == 0x67:
        points = ((0.0, -half), (half, half), (-half, half))
    return [[_transform_typecode_point(px, py, x, y, sx, sy, rotation, skew) for px, py in points]]


def render_typecode_json(path, max_size=None):
    loaded = load_pillow()
    if not loaded:
        return None
    Image, ImageDraw = loaded
    try:
        shapes = _typecode_preview_shapes(path)
        if not shapes:
            return None
        margin = 64.0
        all_points = [point for item in shapes for polygon in item["polygons"] for point in polygon]
        min_x = min(point[0] for point in all_points) - margin
        max_x = max(point[0] for point in all_points) + margin
        min_y = min(point[1] for point in all_points) - margin
        max_y = max(point[1] for point in all_points) + margin
        image_w = max(1, int(math.ceil(max_x - min_x)))
        image_h = max(1, int(math.ceil(max_y - min_y)))
        scale = _preview_scale(image_w, image_h, max_size)
        preview_w = max(1, int(round(image_w * scale)))
        preview_h = max(1, int(round(image_h * scale)))
        render_scale = scale * PREVIEW_JSON_SUPERSAMPLE
        render_w = max(1, preview_w * PREVIEW_JSON_SUPERSAMPLE)
        render_h = max(1, preview_h * PREVIEW_JSON_SUPERSAMPLE)
        background = Image.new("RGB", (render_w, render_h), (38, 38, 38))
        draw_bg = ImageDraw.Draw(background)
        tile = max(8, int(round(32 * render_scale)))
        for y in range(0, render_h, tile):
            for x in range(0, render_w, tile):
                if ((x // tile) + (y // tile)) % 2 == 0:
                    draw_bg.rectangle((x, y, min(render_w, x + tile), min(render_h, y + tile)), fill=(58, 58, 58))
        preview_layer = Image.new("RGBA", (render_w, render_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(preview_layer, "RGBA")
        for item in shapes:
            r, g, b, a = item["color"]
            if item.get("mask"):
                mask = Image.new("L", (render_w, render_h), 0)
                mask_draw = ImageDraw.Draw(mask)
                for polygon in item["polygons"]:
                    points = [((px - min_x) * render_scale, (py - min_y) * render_scale) for px, py in polygon]
                    mask_draw.polygon(points, fill=a)
                alpha = preview_layer.getchannel("A")
                alpha.paste(0, (0, 0), mask)
                preview_layer.putalpha(alpha)
                continue
            for polygon in item["polygons"]:
                points = [((px - min_x) * render_scale, (py - min_y) * render_scale) for px, py in polygon]
                draw.polygon(points, fill=(r, g, b, a))
        preview = background.convert("RGBA")
        preview.alpha_composite(preview_layer)
        preview = preview.convert("RGB")
        if PREVIEW_JSON_SUPERSAMPLE > 1:
            preview = preview.resize((preview_w, preview_h), Image.Resampling.LANCZOS)
        return _pil_to_photo(preview)
    except Exception:
        return None
