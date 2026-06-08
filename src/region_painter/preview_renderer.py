"""Preview renderer — same approach as scripts/heatmap.py render_preview.

Uses cv2 ellipse/rectangle draw calls with numpy boolean-mask alpha blending.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from utils import load_cv2

_EXE_PREVIEW_GLOB = "_exe_preview.*.png"


def _copy_exe_preview(
    output_dir: str | Path,
    preview_png: str | Path,
    max_preview_size: int = 500,
) -> bool:
    import shutil
    from PIL import Image as PILImage

    output_dir = Path(output_dir)
    preview_png = Path(preview_png)
    previews = sorted(
        output_dir.glob(_EXE_PREVIEW_GLOB),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not previews:
        return False
    shutil.copy2(previews[0], preview_png)
    try:
        img = PILImage.open(preview_png)
        w, h = img.size
        if max(w, h) > max_preview_size:
            ratio = max_preview_size / max(w, h)
            img = img.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), PILImage.LANCZOS)
            img.save(preview_png, "PNG")
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# Canvas rendering — identical to scripts/heatmap.py render_preview
# ---------------------------------------------------------------------------

def _draw_shapes_to_canvas(cv2, np, shapes, image_w, image_h):
    """Draw shapes onto a BGR canvas, matching heatmap.py."""
    canvas = np.zeros((image_h, image_w, 3), dtype=np.uint8)

    bg = shapes[0]
    bg_color = bg.get("color", [0, 0, 0, 0])
    if len(bg_color) == 4 and int(bg_color[3]) > 0:
        bg_r, bg_g, bg_b, _bg_a = [int(c) for c in bg_color]
        cv2.rectangle(canvas, (0, 0), (image_w, image_h), (bg_b, bg_g, bg_r), thickness=-1)
    # else: keep black (transparent)

    for shape in shapes[1:]:
        color = shape.get("color", [])
        if len(color) == 4 and int(color[3]) <= 0:
            continue
        r, g, b, a = [int(c) for c in color]
        shape_type = int(shape["type"])
        data = shape["data"]

        mask = np.zeros((image_h, image_w), dtype=np.uint8)
        if shape_type == 1:
            x, y, w, h = data
            x0 = int(round(x - w / 2))
            y0 = int(round(y - h / 2))
            x1 = int(round(x + w / 2))
            y1 = int(round(y + h / 2))
            cv2.rectangle(mask, (x0, y0), (x1, y1), 1, thickness=-1)
        elif shape_type == 16:
            x, y, w, h, rot_deg = data
            cv2.ellipse(mask, (int(x), int(y)), (int(h), int(w)), -90 + rot_deg, 0.0, 360.0, 1, thickness=-1)
        else:
            continue

        where = mask > 0
        if a >= 255:
            canvas[where] = (b, g, r)
        else:
            alpha_norm = a / 255.0
            canvas[where] = (
                (1 - alpha_norm) * canvas[where].astype(np.float32)
                + alpha_norm * np.array([b, g, r], dtype=np.float32)
            ).astype(np.uint8)

    return canvas


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_shapes_to_array(shapes: list[dict], target_path: str | Path) -> "np.ndarray | None":
    """Render *shapes* on top of the target image (for composite)."""
    target_path = Path(target_path)
    if not shapes:
        return None
    bg_data = shapes[0].get("data", [])
    if len(bg_data) < 4:
        return None
    image_w = int(bg_data[2])
    image_h = int(bg_data[3])

    loaded = load_cv2()
    if not loaded:
        return None
    cv2, np = loaded
    # Load target as base canvas.
    canvas = cv2.imread(str(target_path), cv2.IMREAD_COLOR)
    if canvas is None:
        return None
    # Draw shapes on top (skip shapes[0] — target is the background).
    for shape in shapes[1:]:
        color = shape.get("color", [])
        if len(color) == 4 and int(color[3]) <= 0:
            continue
        r, g, b, a = [int(c) for c in color]
        shape_type = int(shape["type"])
        data = shape["data"]

        mask = np.zeros((image_h, image_w), dtype=np.uint8)
        if shape_type == 1:
            x, y, w, h = data
            x0 = int(round(x - w / 2)); y0 = int(round(y - h / 2))
            x1 = int(round(x + w / 2)); y1 = int(round(y + h / 2))
            cv2.rectangle(mask, (x0, y0), (x1, y1), 1, thickness=-1)
        elif shape_type == 16:
            x, y, w, h, rot_deg = data
            cv2.ellipse(mask, (int(x), int(y)), (int(h), int(w)), -90 + rot_deg, 0.0, 360.0, 1, thickness=-1)
        else:
            continue

        where = mask > 0
        if a >= 255:
            canvas[where] = (b, g, r)
        else:
            alpha_norm = a / 255.0
            canvas[where] = (
                (1 - alpha_norm) * canvas[where].astype(np.float32)
                + alpha_norm * np.array([b, g, r], dtype=np.float32)
            ).astype(np.uint8)
    return canvas


def render_preview(
    target_path: str | Path,
    shapes: list[dict],
    output_path: str | Path,
    max_preview_size: int = 500,
) -> None:
    """Render *shapes* exactly like heatmap.py and save as BGR PNG."""
    target_path = Path(target_path)
    output_path = Path(output_path)

    loaded = load_cv2()
    if not loaded:
        _copy_exe_preview(output_path.parent, output_path, max_preview_size)
        return

    cv2, np = loaded
    if not shapes:
        return
    bg_data = shapes[0].get("data", [])
    if len(bg_data) < 4:
        return
    image_w = int(bg_data[2])
    image_h = int(bg_data[3])

    canvas = _draw_shapes_to_canvas(cv2, np, shapes, image_w, image_h)

    if max(image_w, image_h) > max_preview_size:
        ratio = max_preview_size / max(image_w, image_h)
        new_w = max(1, int(image_w * ratio))
        new_h = max(1, int(image_h * ratio))
        canvas = cv2.resize(canvas, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)

    # Build RGBA: R,G,B from canvas (BGR→RGB), alpha=255 where non-black.
    alpha = np.where(canvas.max(axis=2) > 0, 255, 0).astype(np.uint8)
    rgba = np.dstack([canvas[:, :, 2], canvas[:, :, 1], canvas[:, :, 0], alpha])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image as PILImage
    PILImage.fromarray(rgba, "RGBA").save(str(output_path), "PNG")


# ---------------------------------------------------------------------------
# High-quality renderer — matches Go generator's float32 RGBA approach
# ---------------------------------------------------------------------------

def render_preview_high_quality(
    target_path: str | Path,
    shapes: list[dict],
    output_path: str | Path,
    max_preview_size: int = 500,
) -> None:
    """Render shapes with sub-pixel float32 RGBA blending (matches Go generator).

    Optimizations:
    - Renders directly at preview resolution (no full-res then downscale).
    - Pre-computes coordinate grids once (fx, fy) and reuses across all shapes.
    """
    target_path = Path(target_path)
    output_path = Path(output_path)

    if not shapes:
        return
    bg_data = shapes[0].get("data", [])
    if len(bg_data) < 4:
        return
    image_w = int(bg_data[2])
    image_h = int(bg_data[3])

    # --- Determine canvas size: render directly at preview resolution ---
    max_dim = max(image_w, image_h)
    if max_dim > max_preview_size:
        scale = max_preview_size / max_dim
    else:
        scale = 1.0
    pw = max(1, int(image_w * scale))
    ph = max(1, int(image_h * scale))

    # Load target image as float32 RGBA canvas (0-1), already at preview size.
    try:
        from PIL import Image as PILImage
        target_img = PILImage.open(target_path).convert("RGBA")
        if target_img.size != (pw, ph):
            target_img = target_img.resize((pw, ph), PILImage.LANCZOS)
        canvas = np.array(target_img, dtype=np.float32) / 255.0
    except Exception:
        # Fallback: start with transparent canvas.
        canvas = np.zeros((ph, pw, 4), dtype=np.float32)
        bg_color = shapes[0].get("color", [0, 0, 0, 0])
        if len(bg_color) == 4 and int(bg_color[3]) > 0:
            canvas[:, :, 0] = int(bg_color[0]) / 255.0
            canvas[:, :, 1] = int(bg_color[1]) / 255.0
            canvas[:, :, 2] = int(bg_color[2]) / 255.0
            canvas[:, :, 3] = int(bg_color[3]) / 255.0

    # --- Pre-compute coordinate grids once for all shapes (plan 2) ---
    yy, xx = np.mgrid[:ph, :pw]
    fx = xx.astype(np.float32) + 0.5
    fy = yy.astype(np.float32) + 0.5

    # --- Draw each shape with sub-pixel float32 alpha blending ---
    for shape in shapes[1:]:
        color = shape.get("color", [])
        if len(color) < 4 or int(color[3]) <= 0:
            continue
        r = int(color[0]) / 255.0
        g = int(color[1]) / 255.0
        b = int(color[2]) / 255.0
        a = int(color[3]) / 255.0
        shape_type = int(shape["type"])
        data = shape["data"]

        # Scale shape coordinates to preview size.
        scaled_data = _scale_shape_data(shape_type, data, scale)

        mask = _make_shape_mask_f32(shape_type, scaled_data, fx, fy)
        if mask is None:
            continue

        # Alpha blend: dst = dst*(1-a) + src*a  (all 4 channels)
        inv_a = 1.0 - a
        canvas[mask, 0] = canvas[mask, 0] * inv_a + r * a
        canvas[mask, 1] = canvas[mask, 1] * inv_a + g * a
        canvas[mask, 2] = canvas[mask, 2] * inv_a + b * a
        canvas[mask, 3] = canvas[mask, 3] * inv_a + a

    # Convert back to uint8 RGBA and save (no resize needed — already at preview size).
    result = (np.clip(canvas * 255.0, 0, 255)).astype(np.uint8)
    from PIL import Image as PILImage
    pil_img = PILImage.fromarray(result, "RGBA")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pil_img.save(str(output_path), "PNG")


def _scale_shape_data(shape_type: int, data: list, scale: float) -> list:
    """Scale shape coordinates by *scale* (x, y, w, h are scaled; rot_deg unchanged)."""
    if shape_type == 1:  # Rectangle: [x, y, w, h]
        return [data[0] * scale, data[1] * scale, data[2] * scale, data[3] * scale]
    elif shape_type == 16:  # Rotated Ellipse: [x, y, w, h, rot_deg]
        return [data[0] * scale, data[1] * scale, data[2] * scale, data[3] * scale, data[4]]
    return data


def _make_shape_mask_f32(shape_type: int, data: list, fx: "np.ndarray", fy: "np.ndarray"):
    """Generate a boolean mask for a shape with sub-pixel precision.

    *fx* and *fy* are pre-computed coordinate grids (float32) covering the
    canvas, already offset by +0.5 for sub-pixel accuracy.  They should be
    computed once and reused across all shapes.
    """
    if shape_type == 1:  # Rectangle
        x, y, sw, sh = [float(v) for v in data]
        return (fx >= x - sw / 2.0) & (fx <= x + sw / 2.0) & (fy >= y - sh / 2.0) & (fy <= y + sh / 2.0)
    elif shape_type == 16:  # Rotated Ellipse
        x, y, sw, sh = [float(v) for v in data[:4]]
        rot_deg = float(data[4]) if len(data) >= 5 else 0.0
        theta = np.radians(-90.0 + rot_deg)
        cos_t, sin_t = np.cos(theta), np.sin(theta)
        dx = fx - x
        dy = fy - y
        xr = dx * cos_t + dy * sin_t
        yr = -dx * sin_t + dy * cos_t
        return (xr * xr) / (sh * sh) + (yr * yr) / (sw * sw) <= 1.0
    return None


def prune_shapes_for_region(
    shapes: list[dict],
    mask: "Image.Image",
    width: int,
    height: int,
) -> tuple[list[dict], int]:
    """Prune shapes based on a selection mask for region-focused painting.

    Shapes that have *any* pixel inside the mask are kept.
    Shapes completely outside the mask are deleted (they are irrelevant
    to the current region pass).  No occlusion culling is performed.

    Returns ``(pruned_shapes, num_kept_type16)``.
    """
    import math
    from PIL import Image as PILImage

    import numpy as np  # local import to avoid top-level dependency

    # Convert PIL mask to numpy uint8 array (1 = inside selection).
    mask_np = np.array(mask.resize((width, height), PILImage.NEAREST), dtype=np.uint8)
    mask_bin = (mask_np > 0).astype(np.uint8)

    keep = [False] * len(shapes)
    keep[0] = True  # background shape always kept

    for j in range(1, len(shapes)):
        s = shapes[j]
        if s.get("type", 0) != 16:
            keep[j] = True
            continue

        data = s.get("data", [])
        if len(data) < 5:
            keep[j] = True
            continue

        cx, cy, rx, ry, rot_deg = data[:5]
        if rx < 1:
            rx = 1
        if ry < 1:
            ry = 1

        theta = math.radians(rot_deg)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        inv_rx2 = 1.0 / (rx * rx)
        inv_ry2 = 1.0 / (ry * ry)

        x_min = max(0, int(cx - rx - 1))
        x_max = min(width - 1, int(cx + rx + 1))
        y_min = max(0, int(cy - ry - 1))
        y_max = min(height - 1, int(cy + ry + 1))

        has_pixels_in_mask = False

        for py in range(y_min, y_max + 1):
            row_mask = mask_bin[py, x_min:x_max + 1]
            if not row_mask.any():
                continue
            for px_rel in np.where(row_mask)[0]:
                px = x_min + int(px_rel)
                dx = float(px) + 0.5 - cx
                dy = float(py) + 0.5 - cy
                xr = dx * cos_t + dy * sin_t
                yr = -dx * sin_t + dy * cos_t
                if xr * xr * inv_rx2 + yr * yr * inv_ry2 <= 1.0:
                    has_pixels_in_mask = True
                    break
            if has_pixels_in_mask:
                break

        # Keep shapes that intersect the mask; delete those completely outside.
        keep[j] = has_pixels_in_mask

    pruned = [s for i, s in enumerate(shapes) if keep[i]]
    num_type16 = sum(1 for s in pruned if s.get("type", 0) == 16)
    return pruned, num_type16


def load_shapes_from_json(json_path: str | Path) -> list[dict]:
    """Load the ``shapes`` list from a geometry JSON file."""
    with open(json_path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if isinstance(payload, list):
        return payload
    return payload.get("shapes", [])
