"""
Shape Density Heatmap Generator for Forza Painter
=================================================
Reads a geometry JSON file and generates heatmap visualizations
showing shape density across the canvas area.

Usage:
    python scripts/heatmap.py <geometry.json>
    python scripts/heatmap.py <geometry.json> --colormap COLORMAP_INFERNO
    python scripts/heatmap.py <geometry.json> --no-overlay --alpha 0.6

Outputs (by default, all three):
    {name}_heatmap.png   – standalone heatmap with density colorbar
    {name}_compare.png   – side-by-side preview vs. heatmap
    {name}_overlay.png   – alpha-blended preview + heatmap
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow importing from the src/ directory
_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from geometry_json import load_normalized_geometry
from utils import load_cv2

# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def build_accumulator(shapes, image_w, image_h, cv2, np):
    """Build a pixel-accurate density accumulator.

    Every drawable shape (alpha > 0, excluding background shapes[0]) is drawn
    as a filled white (value=1) region onto a temporary single-channel mask.
    The mask is added to the accumulator so each pixel stores the exact count
    of shapes covering it.

    Returns a float32 numpy array of shape (image_h, image_w).
    """
    acc = np.zeros((image_h, image_w), dtype=np.float32)

    for shape in shapes[1:]:
        color = shape.get("color", [])
        if len(color) == 4 and int(color[3]) <= 0:
            continue

        mask = np.zeros((image_h, image_w), dtype=np.uint8)
        shape_type = int(shape["type"])
        data = shape["data"]

        if shape_type == 1:  # RECTANGLE
            x, y, w, h = data
            x0 = int(round(x - w / 2))
            y0 = int(round(y - h / 2))
            x1 = int(round(x + w / 2))
            y1 = int(round(y + h / 2))
            cv2.rectangle(mask, (x0, y0), (x1, y1), 1, thickness=-1)
        elif shape_type == 16:  # ROTATED_ELLIPSE
            x, y, w, h, rot_deg = data
            cv2.ellipse(
                mask,
                (int(x), int(y)),
                (int(h), int(w)),
                -90 + rot_deg,
                0.0,
                360.0,
                1,
                thickness=-1,
            )

        acc += mask.astype(np.float32)

    return acc


def render_preview(shapes, image_w, image_h, cv2, np):
    """Render the geometry preview.

    Uses the same OpenCV draw-call conventions (centre positions, ellipse
    axis / angle order) as main.py, but properly alpha-blends
    semi-transparent shapes instead of discarding the alpha channel.
    """
    preview = np.zeros((image_h, image_w, 3), dtype=np.uint8)

    # Background fill
    bg = shapes[0]
    bg_color = bg.get("color", [0, 0, 0, 0])
    if len(bg_color) == 4 and int(bg_color[3]) > 0:
        bg_r, bg_g, bg_b, _bg_a = [int(c) for c in bg_color]
        cv2.rectangle(
            preview, (0, 0), (image_w, image_h), (bg_b, bg_g, bg_r), thickness=-1
        )
    else:
        preview[:, :] = (38, 38, 38)

    # Drawable shapes
    for shape in shapes[1:]:
        color = shape.get("color", [])
        if len(color) == 4 and int(color[3]) <= 0:
            continue
        r, g, b, a = [int(c) for c in color]
        shape_type = int(shape["type"])
        data = shape["data"]

        # Draw the shape filled on a temporary single-channel mask.
        mask = np.zeros((image_h, image_w), dtype=np.uint8)
        if shape_type == 1:  # RECTANGLE
            x, y, w, h = data
            x0 = int(round(x - w / 2))
            y0 = int(round(y - h / 2))
            x1 = int(round(x + w / 2))
            y1 = int(round(y + h / 2))
            cv2.rectangle(mask, (x0, y0), (x1, y1), 1, thickness=-1)
        elif shape_type == 16:  # ROTATED_ELLIPSE
            x, y, w, h, rot_deg = data
            cv2.ellipse(
                mask,
                (int(x), int(y)),
                (int(h), int(w)),
                -90 + rot_deg,
                0.0,
                360.0,
                1,
                thickness=-1,
            )

        # Blend the shape onto the preview using its alpha channel.
        where = mask > 0
        if a >= 255:
            preview[where] = (b, g, r)
        else:
            alpha_norm = a / 255.0
            preview[where] = (
                (1 - alpha_norm) * preview[where].astype(np.float32)
                + alpha_norm * np.array([b, g, r], dtype=np.float32)
            ).astype(np.uint8)

    return preview


def render_heatmap_image(accumulator, cv2, np, colormap_name="COLORMAP_JET"):
    """Apply a colormap to the normalised accumulator.

    Returns a (image_h, image_w, 3) uint8 BGR image with no colorbar.
    """
    max_val = accumulator.max()
    if max_val <= 0:
        max_val = 1.0

    norm = (accumulator / max_val * 255).astype(np.uint8)
    colormap = getattr(cv2, colormap_name, cv2.COLORMAP_JET)
    return cv2.applyColorMap(norm, colormap)


def add_colorbar(heatmap, max_val, cv2, np):
    """Append a vertical colour-scale bar to the right of the heatmap.

    The bar is labelled with the maximum density value at the top and 0 at
    the bottom.  Returns a new image that is (h, w + bar + label_area, 3).
    """
    h, w = heatmap.shape[:2]
    bar_width = 30
    label_width = 70
    pad_total = bar_width + label_width

    # Build the colour-scale bar (same colormap transformation as the heatmap)
    bar_gray = np.zeros((h, bar_width), dtype=np.uint8)
    for row in range(h):
        val = int((1.0 - row / max(h - 1, 1)) * 255)
        bar_gray[row, :] = val
    bar_color = cv2.applyColorMap(bar_gray, cv2.COLORMAP_JET)

    # Stitch: heatmap | padding area (for bar + labels)
    padded = np.zeros((h, w + pad_total, 3), dtype=np.uint8)
    padded[:, :w] = heatmap
    padded[:, w:] = (30, 30, 30)  # dark background for the label region
    bar_x = w + 8
    padded[:, bar_x : bar_x + bar_width] = bar_color

    # Text labels
    font = cv2.FONT_HERSHEY_SIMPLEX
    text_color = (220, 220, 220)

    cv2.putText(
        padded,
        f"{int(max_val)}",
        (bar_x + bar_width + 6, 22),
        font,
        0.55,
        text_color,
        1,
    )
    cv2.putText(
        padded,
        "0",
        (bar_x + bar_width + 6, h - 10),
        font,
        0.5,
        text_color,
        1,
    )
    cv2.putText(
        padded,
        "# shapes",
        (bar_x + bar_width + 4, h // 2),
        font,
        0.45,
        text_color,
        1,
    )

    return padded


def generate_standalone_heatmap(geometry_json_path, output_png_path, colormap_name="COLORMAP_JET"):
    """Convenience function: generate a standalone heatmap PNG (with colorbar)
    from a geometry JSON file.  Returns the Path to the written PNG, or None
    on failure.

    This is the function intended for programmatic use from other modules
    (e.g. the Region Paint workflow in app.py).
    """
    loaded = load_cv2()
    if not loaded:
        raise RuntimeError("OpenCV (cv2) and NumPy are required but could not be loaded.")
    cv2, np = loaded

    data = load_normalized_geometry(str(geometry_json_path))
    shapes = data["shapes"]
    if len(shapes) < 2:
        raise ValueError("No drawable shapes found in the geometry file.")

    bg_data = shapes[0]["data"]
    image_w = int(bg_data[2])
    image_h = int(bg_data[3])

    accumulator = build_accumulator(shapes, image_w, image_h, cv2, np)
    heatmap_raw = render_heatmap_image(accumulator, cv2, np, colormap_name)
    heatmap_with_bar = add_colorbar(heatmap_raw, accumulator.max(), cv2, np)

    output_png_path = Path(output_png_path)
    output_png_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_png_path), heatmap_with_bar)
    return output_png_path


def render_comparison(preview, heatmap_with_bar, cv2, np):
    """Horizontally concatenate preview and heatmap (with colorbar) side by side."""
    h1, w1 = preview.shape[:2]
    h2, w2 = heatmap_with_bar.shape[:2]
    h = max(h1, h2)

    # Pad the shorter image vertically so they align
    if h1 < h:
        pad = np.zeros((h - h1, w1, 3), dtype=np.uint8)
        pad[:, :] = (30, 30, 30)
        preview = np.vstack([preview, pad])
    if h2 < h:
        pad = np.zeros((h - h2, w2, 3), dtype=np.uint8)
        pad[:, :] = (30, 30, 30)
        heatmap_with_bar = np.vstack([heatmap_with_bar, pad])

    # Thin vertical separator
    sep_w = 4
    sep = np.zeros((h, sep_w, 3), dtype=np.uint8)
    sep[:, :] = (200, 200, 200)
    combined = np.hstack([preview, sep, heatmap_with_bar])

    # Title bar at the top
    title_h = 40
    title = np.zeros((title_h, combined.shape[1], 3), dtype=np.uint8)
    title[:, :] = (40, 40, 40)
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(title, "Preview", (w1 // 2 - 45, 28), font, 0.7, (255, 255, 255), 2)
    cv2.putText(
        title,
        "Heatmap",
        (w1 + sep_w + w2 // 2 - 55, 28),
        font,
        0.7,
        (255, 255, 255),
        2,
    )

    return np.vstack([title, combined])


def render_overlay(preview, heatmap_no_bar, cv2, np, alpha=0.45):
    """Alpha-blend the raw preview and heatmap (same dimensions, no colorbar)."""
    return cv2.addWeighted(preview, 1.0 - alpha, heatmap_no_bar, alpha, 0)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Generate shape density heatmaps from Forza Painter geometry JSON files."
    )
    parser.add_argument(
        "geometry_path",
        help="Path to the generated geometry .json file.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for output images. Defaults to the same folder as the JSON.",
    )
    parser.add_argument(
        "--colormap",
        default="COLORMAP_JET",
        choices=[
            "COLORMAP_JET",
            "COLORMAP_HOT",
            "COLORMAP_INFERNO",
            "COLORMAP_PLASMA",
            "COLORMAP_VIRIDIS",
            "COLORMAP_MAGMA",
            "COLORMAP_TURBO",
            "COLORMAP_PARULA",
        ],
        help="OpenCV colormap to use (default: COLORMAP_JET).",
    )
    parser.add_argument(
        "--no-heatmap",
        action="store_true",
        help="Skip standalone heatmap output.",
    )
    parser.add_argument(
        "--no-compare",
        action="store_true",
        help="Skip side-by-side comparison output.",
    )
    parser.add_argument(
        "--no-overlay",
        action="store_true",
        help="Skip overlay blend output.",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.45,
        help="Overlay blend alpha for heatmap (0.0-1.0, default: 0.45).",
    )

    args = parser.parse_args()

    # -- Validate input -------------------------------------------------------
    json_path = Path(args.geometry_path)
    if not json_path.is_file():
        print(f"Error: '{json_path}' is not a valid file.")
        sys.exit(1)
    if json_path.suffix.lower() != ".json":
        print(f"Error: Expected a .json file, got '{json_path.suffix}'")
        sys.exit(1)

    # -- Load dependencies ----------------------------------------------------
    loaded = load_cv2()
    if not loaded:
        print("Error: OpenCV (cv2) and NumPy are required but could not be loaded.")
        print("Install with: pip install opencv-python numpy")
        sys.exit(1)
    cv2, np = loaded

    # -- Load geometry --------------------------------------------------------
    print(f"Loading geometry from: {json_path}")
    try:
        data = load_normalized_geometry(str(json_path))
    except Exception as exc:
        print(f"Error: Not a valid geometry JSON file: {exc}")
        sys.exit(1)

    shapes = data["shapes"]
    if len(shapes) < 2:
        print("Error: No drawable shapes found in the geometry file.")
        sys.exit(1)

    bg_data = shapes[0]["data"]
    image_w = int(bg_data[2])
    image_h = int(bg_data[3])
    drawable = sum(
        1
        for s in shapes[1:]
        if not (len(s.get("color", [])) == 4 and int(s["color"][3]) <= 0)
    )
    print(f"Canvas: {image_w} x {image_h}  |  Drawable shapes: {drawable}")

    # -- Output directory -----------------------------------------------------
    out_dir = Path(args.output_dir) if args.output_dir else json_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = json_path.stem

    # -- Build accumulator ----------------------------------------------------
    print("Building density accumulator...")
    accumulator = build_accumulator(shapes, image_w, image_h, cv2, np)
    max_density = accumulator.max()
    print(f"Max density: {int(max_density)} overlapping shapes at a single pixel")

    # -- Render preview -------------------------------------------------------
    print("Rendering preview...")
    preview = render_preview(shapes, image_w, image_h, cv2, np)

    # -- Render heatmap (without colorbar, same size as preview) --------------
    print("Rendering heatmap...")
    heatmap_raw = render_heatmap_image(accumulator, cv2, np, args.colormap)
    heatmap_with_bar = add_colorbar(heatmap_raw, max_density, cv2, np)

    # -- Write outputs --------------------------------------------------------
    if not args.no_heatmap:
        path = out_dir / f"{stem}_heatmap.png"
        cv2.imwrite(str(path), heatmap_with_bar)
        print(f"  -> {path}")

    if not args.no_compare:
        comparison = render_comparison(preview, heatmap_with_bar, cv2, np)
        path = out_dir / f"{stem}_compare.png"
        cv2.imwrite(str(path), comparison)
        print(f"  -> {path}")

    if not args.no_overlay:
        overlay = render_overlay(preview, heatmap_raw, cv2, np, args.alpha)
        path = out_dir / f"{stem}_overlay.png"
        cv2.imwrite(str(path), overlay)
        print(f"  -> {path}")

    print("Done!")


if __name__ == "__main__":
    main()
