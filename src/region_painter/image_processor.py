"""Image processing utilities for region-focused painting.

Handles: blank mask creation, canvas-shape 鈫?PIL mask conversion, and
alpha-channel masking (apply selection mask to target images).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


def create_blank_mask(width: int, height: int) -> Image.Image:
    """Return a blank (all zeros) ``'L'`` mode mask at *width* 脳 *height*."""
    return Image.new("L", (width, height), 0)


def mask_from_canvas_shapes(
    shapes: list[dict],
    width: int,
    height: int,
    scale: float = 1.0,
) -> Image.Image:
    """Convert a list of canvas-selection shape dicts into a PIL ``'L'`` mask.

    Each shape dict must have:
        ``"tool"``: one of ``"rect"``, ``"ellipse"``, ``"brush"``, ``"polygon"``
        ``"coords"``: tool-specific coordinate list (in *canvas* pixels)
        ``"rotation"``: (rect/ellipse) rotation in degrees (default 0)
        ``"brush_size"``: (brush only) stroke width in canvas pixels

    Coordinates are divided by *scale* to map from canvas space to working
    (full-resolution) pixel space.
    """
    mask = create_blank_mask(width, height)
    draw = ImageDraw.Draw(mask)

    # Try OpenCV for rotated shapes
    try:
        from utils import load_cv2
        loaded = load_cv2()
        cv2_mod, np_mod = (loaded if loaded else (None, None))
    except Exception:
        cv2_mod, np_mod = None, None

    has_rotated = any(shape.get("rotation", 0) != 0 for shape in shapes)
    cv2_mask = None
    if cv2_mod is not None and np_mod is not None and has_rotated:
        cv2_mask = np_mod.zeros((height, width), dtype=np_mod.uint8)

    for shape in shapes:
        tool = shape.get("tool", "")
        coords = shape.get("coords", [])
        rotation = shape.get("rotation", 0)

        # Scale coords from canvas 鈫?working pixels.
        scaled: list[float] = [c / scale for c in coords]

        if tool == "rect" and len(scaled) >= 4:
            x1, y1, x2, y2 = scaled[:4]
            if rotation != 0 and cv2_mask is not None:
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
                hw = abs(x2 - x1) / 2.0
                hh = abs(y2 - y1) / 2.0
                rect = ((cx, cy), (hw * 2, hh * 2), rotation)
                box = cv2_mod.boxPoints(rect).astype(np_mod.int32)
                cv2_mod.fillPoly(cv2_mask, [box], 255)
            else:
                draw.rectangle([x1, y1, x2, y2], fill=255)

        elif tool == "ellipse" and len(scaled) >= 4:
            x1, y1, x2, y2 = scaled[:4]
            if rotation != 0 and cv2_mask is not None:
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
                hw = abs(x2 - x1) / 2.0
                hh = abs(y2 - y1) / 2.0
                cv2_mod.ellipse(cv2_mask,
                                (int(round(cx)), int(round(cy))),
                                (int(round(hw)), int(round(hh))),
                                rotation, 0.0, 360.0, 255, thickness=-1)
            else:
                draw.ellipse([x1, y1, x2, y2], fill=255)

        elif tool == "brush":
            brush_size = shape.get("brush_size", 15) / scale
            # Convert flat [x1,y1,x2,y2,...] into (x,y) pairs.
            points: list[tuple[float, float]] = []
            for i in range(0, len(scaled) - 1, 2):
                points.append((scaled[i], scaled[i + 1]))
            if len(points) >= 2:
                draw.line(points, fill=255, width=max(1, int(round(brush_size))))
            elif len(points) == 1:
                # Single click 鈫?draw a dot.
                x, y = points[0]
                r = max(1, int(round(brush_size / 2)))
                draw.ellipse([x - r, y - r, x + r, y + r], fill=255)

        elif tool == "polygon" and len(scaled) >= 6:
            points = [(scaled[i], scaled[i + 1]) for i in range(0, len(scaled) - 1, 2)]
            if len(points) >= 3:
                draw.polygon(points, fill=255)

    # Merge OpenCV mask back into PIL mask
    if cv2_mask is not None:
        mask_np = np_mod.array(mask)
        mask_np[cv2_mask > 0] = 255
        mask = Image.fromarray(mask_np, mode="L")

    return mask


def apply_selection_mask(
    target_path: str | Path,
    mask: Image.Image,
    output_path: str | Path,
    feather_radius: int = 0,
) -> None:
    """Apply a selection mask to *target_path*, writing to *output_path*.

    1. Load target RGBA PNG.
    2. Optionally Gaussian-blur the mask (*feather_radius* > 0).
    3. Multiply mask into the alpha channel:
       ``new_alpha = original_alpha * (mask_pixel / 255)``
    4. Save as RGBA PNG.
    """
    target_path = Path(target_path)
    output_path = Path(output_path)

    target = Image.open(target_path).convert("RGBA")

    # Ensure mask matches target dimensions.
    if mask.size != target.size:
        mask = mask.resize(target.size, Image.NEAREST)

    if feather_radius > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=feather_radius))

    # Multiply mask into alpha channel.
    r, g, b, a = target.split()
    # mask is 'L' (0-255); use it as a multiplier.
    mask_data = mask.point(lambda v: v)  # copy
    new_alpha = Image.composite(
        Image.new("L", target.size, 0),
        a,
        mask_data,
    )
    # Actually: new_alpha = a * (mask / 255)
    # We can do this via point operations.
    a_pixels = a.load()
    m_pixels = mask_data.load()
    for y in range(target.height):
        for x in range(target.width):
            a_val = a_pixels[x, y]
            m_val = m_pixels[x, y]
            a_pixels[x, y] = int(a_val * (m_val / 255.0))

    result = Image.merge("RGBA", (r, g, b, a))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.save(output_path, "PNG")
