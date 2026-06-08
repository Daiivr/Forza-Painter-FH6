from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import BOTH, BOTTOM, END, LEFT, RIGHT, TOP, X, Y, Button, Canvas, Checkbutton, Entry, Frame, IntVar, Label, Listbox, PhotoImage, StringVar, Text, Tk, Toplevel, filedialog, messagebox, ttk

import psutil

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:
    DND_FILES = None
    TkinterDnD = None

from app_paths import ROOT, RESOURCE_ROOT

# Allow importing project-root helper packages such as scripts/.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from game_profiles import PROFILES
from geometry_json import RECTANGLE, ROTATED_ELLIPSE, load_normalized_geometry
from full_shape_preview import is_typecode_json, render_typecode_json, typecode_shape_count
from generator_backend import GENERATOR_EXE, GENERATOR_JSON_SCAN_SECONDS, GENERATOR_POLL_SLEEP_SECONDS, GENERATOR_PREVIEW_SCAN_SECONDS, USER_SETTINGS_DIR, best_geometry_jsons, build_generator_command, build_generator_env, generated_jsons, generated_preview_files, generator_preview_path, load_settings, preprocess_input_image, write_custom_settings, write_user_settings_preset
from import_readiness import layer_fit, readiness_checks
from region_painter.workflow import (
    finalize_first_pass,
    finalize_region_pass,
    get_status as region_get_status,
    prepare_first_pass,
    prepare_region_pass,
)
from version import APP_DISPLAY_NAME, __version__, app_title


from app_config import (
    APP_DIR,
    PROBE_DIR,
    SESSION_PATH,
    FULL_SHAPE_ROOT,
    MEMORY_SNAPSHOT_LIMIT_MB,
    PREVIEW_MAX,
    DETAILED_LOG_OUTPUT_LIMIT,
    DETAILED_LOG_MEMORY_LIMIT,
    FH6_AUTO_LOCATE_MAX_SECONDS,
    FH6_AUTO_LOCATE_TIMEOUT_SECONDS,
    UPDATE_VERSION_URL,
    UPDATE_CHANGELOG_URL,
    UPDATE_RELEASE_URL,
    MARKET_URL,
    UPDATE_CHECK_TIMEOUT_SECONDS,
    Theme,
)

from utils import load_cv2, load_pillow

from i18n import tr

LANGUAGES = {
    "English": "en",
    "Español": "es",
    "Português (Brasil)": "pt-br",
    "中文": "zh",
    "中文 (繁體)": "zh-tw",
    "한국어": "ko",
}

ETA_MAX_PROGRESS_SAMPLES = 400
ETA_WINDOW_SECONDS = 45.0
ETA_MAX_HISTORY_SECONDS = 180.0
ETA_MIN_WINDOW_SECONDS = 4.0
ETA_MIN_WINDOW_LAYERS = 25
PREVIEW_JSON_SUPERSAMPLE = 2
PREVIEW_RESIZE_DEBOUNCE_MS = 420
PREVIEW_SIZE_BUCKET = 48
LAYOUT_RESIZE_DEBOUNCE_MS = 140
LAYOUT_SIZE_BUCKET = 48
GENERATOR_LIVE_PREVIEW_SCAN_SECONDS = 0.05
GENERATOR_LIVE_PREVIEW_POLL_SLEEP_SECONDS = 0.05


# Removed: TEXT dictionary (now in i18n.py)


def ensure_dirs():
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    FULL_SHAPE_ROOT.mkdir(parents=True, exist_ok=True)


def set_windows_app_user_model_id():
    if platform.system() != "Windows":
        return
    try:
        import ctypes

        app_id = "bvzrays.forza-painter-fh6"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def app_is_admin():
    if platform.system() != "Windows":
        return True
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def create_app_root():
    if TkinterDnD is not None:
        try:
            return TkinterDnD.Tk()
        except Exception:
            pass
    return Tk()


def version_key(value):
    parts = []
    for part in re.findall(r"\d+", str(value)):
        parts.append(int(part))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def parse_version_source(source):
    match = re.search(r'__version__\s*=\s*"([^"]+)"', source or "")
    if not match:
        raise ValueError("remote version file did not contain __version__")
    return match.group(1).strip()


def fetch_text_url(url, timeout=UPDATE_CHECK_TIMEOUT_SECONDS):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"{APP_DISPLAY_NAME}/{__version__}",
            "Accept": "text/plain,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(256 * 1024)
    return data.decode("utf-8", errors="replace")


def extract_changelog_section(changelog, version):
    text = (changelog or "").strip()
    if not text:
        return ""
    heading = re.compile(rf"^(#{{1,6}})\s+v?{re.escape(str(version))}\b.*$", re.I | re.M)
    match = heading.search(text)
    if not match:
        return text[:6000]
    next_heading = re.compile(rf"^{re.escape(match.group(1))}\s+\S+", re.M)
    next_match = next_heading.search(text, match.end())
    end = next_match.start() if next_match else len(text)
    return text[match.start():end].strip()[:6000]


def helper_command(helper_name):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--helper", helper_name]
    return [sys.executable, APP_DIR / f"{helper_name}.py"]


def run_embedded_helper(helper_name, args):
    if helper_name == "fh6_probe":
        import fh6_probe

        previous_argv = sys.argv
        try:
            sys.argv = ["fh6_probe.py", *args]
            return fh6_probe.main()
        finally:
            sys.argv = previous_argv
    if helper_name == "fh6_typecode_probe":
        import fh6_typecode_probe

        previous_argv = sys.argv
        try:
            sys.argv = ["fh6_typecode_probe.py", *args]
            return fh6_typecode_probe.main()
        finally:
            sys.argv = previous_argv
    if helper_name == "fh6_typecode_export":
        import fh6_typecode_export

        previous_argv = sys.argv
        try:
            sys.argv = ["fh6_typecode_export.py", *args]
            return fh6_typecode_export.main()
        finally:
            sys.argv = previous_argv
    if helper_name == "fh6_typecode_import":
        import fh6_typecode_import

        previous_argv = sys.argv
        try:
            sys.argv = ["fh6_typecode_import.py", *args]
            return fh6_typecode_import.main()
        finally:
            sys.argv = previous_argv
    if helper_name == "fh6_typecode_trim":
        import fh6_typecode_trim

        previous_argv = sys.argv
        try:
            sys.argv = ["fh6_typecode_trim.py", *args]
            return fh6_typecode_trim.main()
        finally:
            sys.argv = previous_argv
    if helper_name == "main":
        import main as importer_main

        return importer_main.main(["main.py", *args])
    raise SystemExit(f"Unknown helper: {helper_name}")


def game_processes():
    names = {name.lower(): key for key, profile in PROFILES.items() for name in profile.process_names}
    found = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = proc.info.get("name") or ""
            key = names.get(name.lower())
            if key:
                found.append({
                    "pid": proc.info["pid"],
                    "name": name,
                    "profile": key,
                    "label": f"{name} pid {proc.info['pid']}",
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def parse_hex_or_empty(value):
    value = str(value or "").strip()
    return value or None


def load_session_location():
    if not SESSION_PATH.exists():
        return None
    try:
        return json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def clear_session_location():
    try:
        SESSION_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def session_pid_is_live(session, game):
    try:
        pid = int(session.get("pid", -1))
        proc = psutil.Process(pid)
        profile = PROFILES.get(game)
        return bool(profile and proc.name().lower() in [name.lower() for name in profile.process_names])
    except (psutil.Error, TypeError, ValueError):
        return False


def session_matches_current_import(session, game, pid, layer_count):
    if not session:
        return False
    if str(session.get("layer_count", "")) != str(layer_count):
        return False
    if not session_pid_is_live(session, game):
        return False
    try:
        session_pid = int(session.get("pid", -1))
        return not pid or int(pid) == session_pid
    except (TypeError, ValueError):
        return False


# load_cv2 and load_pillow are now imported from utils


def preview_size_tuple(max_size=None):
    if max_size is None:
        return PREVIEW_MAX, PREVIEW_MAX
    if isinstance(max_size, (tuple, list)):
        if len(max_size) >= 2:
            width, height = max_size[0], max_size[1]
        elif len(max_size) == 1:
            width = height = max_size[0]
        else:
            width = height = PREVIEW_MAX
    else:
        width = height = max_size
    try:
        width = int(width)
        height = int(height)
    except (TypeError, ValueError):
        width = height = PREVIEW_MAX
    return max(1, width), max(1, height)


def centered_region_group_offset(available_width: int, main_width: int, side_width: int = 0, gap: int = 1) -> int:
    """Center a main preview plus an optional side strip inside a canvas."""
    side_total = max(0, int(side_width))
    if side_total:
        side_total += max(0, int(gap))
    return max(2, int((max(1, int(available_width)) - max(1, int(main_width)) - side_total) / 2))


def preview_scale(width, height, max_size=None):
    max_w, max_h = preview_size_tuple(max_size)
    if width <= 0 or height <= 0:
        return 1.0
    return min(max_w / width, max_h / height, 1.0)


def resize_keep_aspect(image, max_size=None):
    loaded = load_cv2()
    if not loaded:
        return image
    cv2, _np = loaded
    height, width = image.shape[:2]
    scale = preview_scale(width, height, max_size)
    if scale < 1.0:
        resized_w = max(1, int(round(width * scale)))
        resized_h = max(1, int(round(height * scale)))
        image = cv2.resize(image, (resized_w, resized_h), interpolation=cv2.INTER_AREA)
    return image


def image_to_photo(image, max_size=None):
    loaded = load_cv2()
    if not loaded:
        return None
    cv2, _np = loaded
    image = resize_keep_aspect(image, max_size)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        return None
    return encoded.tobytes()


def pil_to_photo(image, max_size=None):
    loaded = load_pillow()
    if not loaded:
        return None
    Image, _ImageDraw = loaded
    image = image.convert("RGB")
    image.thumbnail(preview_size_tuple(max_size), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _hex_to_rgb(value, fallback=(7, 11, 19)):
    try:
        text = str(value).strip().lstrip("#")
        if len(text) == 3:
            text = "".join(ch * 2 for ch in text)
        return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))
    except Exception:
        return fallback


def _silent_imread(cv2, path):
    """cv2.imread but with native stderr muted, so a half-written PNG
    doesn't print 'libpng error: Read Error' to the console."""
    saved_fd = None
    devnull_fd = None
    try:
        try:
            saved_fd = os.dup(2)
            devnull_fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull_fd, 2)
        except Exception:
            saved_fd = None
        return cv2.imread(str(path), cv2.IMREAD_COLOR)
    finally:
        if saved_fd is not None:
            try:
                os.dup2(saved_fd, 2)
            except Exception:
                pass
            try:
                os.close(saved_fd)
            except Exception:
                pass
        if devnull_fd is not None:
            try:
                os.close(devnull_fd)
            except Exception:
                pass


def render_source_image(path, max_size=None):
    loaded = load_cv2()
    if loaded:
        cv2, _np = loaded
        image = _silent_imread(cv2, path)
        if image is not None:
            return image_to_photo(image, max_size)
    loaded = load_pillow()
    if not loaded:
        return None
    Image, _ImageDraw = loaded
    try:
        with Image.open(path) as image:
            return pil_to_photo(image, max_size)
    except Exception:
        return None


def render_source_image_fit(path, max_size=None):
    """Like render_source_image but scales UP as well as down to fill bounds.
    Preserves the PNG alpha channel so transparent areas show the widget's bg
    instead of getting flattened onto opaque black."""
    loaded = load_pillow()
    if not loaded:
        return render_source_image(path, max_size)
    Image, _ImageDraw = loaded
    try:
        with Image.open(path) as image:
            has_alpha = (
                image.mode in ("RGBA", "LA", "PA")
                or (image.mode == "P" and "transparency" in image.info)
            )
            image = image.convert("RGBA" if has_alpha else "RGB")
            bw, bh = preview_size_tuple(max_size)
            iw, ih = image.size
            if iw > 0 and ih > 0:
                scale = min(bw / iw, bh / ih)
                new_w = max(1, int(round(iw * scale)))
                new_h = max(1, int(round(ih * scale)))
                if (new_w, new_h) != (iw, ih):
                    image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()
    except Exception:
        return None


def render_source_shadow_image(path, max_size=None):
    loaded = load_pillow()
    if not loaded:
        return None
    Image, _ImageDraw = loaded
    try:
        from PIL import ImageEnhance
        with Image.open(path) as image:
            has_alpha = image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)
            image = image.convert("RGBA")
            bw, bh = preview_size_tuple(max_size)
            iw, ih = image.size
            if iw > 0 and ih > 0:
                scale = min(bw / iw, bh / ih)
                new_w = max(1, int(round(iw * scale)))
                new_h = max(1, int(round(ih * scale)))
                if (new_w, new_h) != (iw, ih):
                    image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
            bg = _hex_to_rgb(Theme.PREVIEW_BG)
            canvas = Image.new("RGBA", preview_size_tuple(max_size), (*bg, 255))
            if has_alpha:
                alpha = image.getchannel("A")
                silhouette = Image.new("RGBA", image.size, (36, 39, 40, 150))
                silhouette.putalpha(alpha.point(lambda value: int(value * 0.58)))
            else:
                silhouette = ImageEnhance.Brightness(image).enhance(0.18)
                silhouette = ImageEnhance.Color(silhouette).enhance(0.2)
            x = (canvas.width - image.width) // 2
            y = (canvas.height - image.height) // 2
            canvas.alpha_composite(silhouette, (x, y))
            return pil_to_photo(canvas, max_size)
    except Exception:
        return None


def render_geometry_json(path, max_size=None):
    typecode_preview = render_typecode_json(path, max_size)
    if typecode_preview:
        return typecode_preview
    pillow_preview = render_geometry_json_pillow(path, max_size)
    if pillow_preview:
        return pillow_preview
    loaded = load_cv2()
    if not loaded:
        return None
    cv2, np = loaded
    try:
        data = load_normalized_geometry(path)
        shapes = data["shapes"]
        image_w, image_h = [int(v) for v in shapes[0]["data"][2:]]
        bg_r, bg_g, bg_b, bg_a = [int(v) for v in shapes[0]["color"]]
        scale = preview_scale(image_w, image_h, max_size)
        preview_w = max(1, int(round(image_w * scale)))
        preview_h = max(1, int(round(image_h * scale)))
        preview = np.zeros((preview_h, preview_w, 3), np.uint8)
        if bg_a > 0:
            preview[:, :] = (bg_b, bg_g, bg_r)
        else:
            preview[:, :] = (38, 38, 38)
            tile = max(8, int(round(32 * scale)))
            for y in range(0, preview_h, tile):
                for x in range(0, preview_w, tile):
                    if ((x // tile) + (y // tile)) % 2 == 0:
                        preview[y:y + tile, x:x + tile] = (58, 58, 58)
        for shape in shapes[1:]:
            color = [int(v) for v in shape.get("color", [])]
            if len(color) == 4 and color[3] <= 0:
                continue
            r, g, b, _a = color
            shape_type = int(shape.get("type", 0))
            if shape_type == ROTATED_ELLIPSE:
                x, y, w, h, rot_deg = shape["data"]
                center = (int(round(float(x) * scale)), int(round(float(y) * scale)))
                axes = (max(1, int(round(float(h) * scale))), max(1, int(round(float(w) * scale))))
                preview = cv2.ellipse(preview, center, axes, -90 + float(rot_deg), 0.0, 360.0, (b, g, r), thickness=-1)
            elif shape_type == RECTANGLE:
                x, y, w, h = shape["data"]
                x = float(x)
                y = float(y)
                w = float(w)
                h = float(h)
                x0 = int(round((x - w / 2) * scale))
                y0 = int(round((y - h / 2) * scale))
                x1 = int(round((x + w / 2) * scale))
                y1 = int(round((y + h / 2) * scale))
                preview = cv2.rectangle(preview, (x0, y0), (x1, y1), (b, g, r), thickness=-1)
        return image_to_photo(preview, max_size)
    except Exception:
        return None


def render_geometry_json_pillow(path, max_size=None):
    loaded = load_pillow()
    if not loaded:
        return None
    Image, ImageDraw = loaded
    try:
        data = load_normalized_geometry(path)
        shapes = data["shapes"]
        image_w, image_h = [int(v) for v in shapes[0]["data"][2:]]
        bg_r, bg_g, bg_b, bg_a = [int(v) for v in shapes[0]["color"]]
        scale = preview_scale(image_w, image_h, max_size)
        preview_w = max(1, int(round(image_w * scale)))
        preview_h = max(1, int(round(image_h * scale)))
        render_scale = scale * PREVIEW_JSON_SUPERSAMPLE
        render_w = max(1, preview_w * PREVIEW_JSON_SUPERSAMPLE)
        render_h = max(1, preview_h * PREVIEW_JSON_SUPERSAMPLE)
        if bg_a > 0:
            preview = Image.new("RGB", (render_w, render_h), (bg_r, bg_g, bg_b))
        else:
            preview = Image.new("RGB", (render_w, render_h), (38, 38, 38))
            draw_bg = ImageDraw.Draw(preview)
            tile = max(8, int(round(32 * render_scale)))
            for y in range(0, render_h, tile):
                for x in range(0, render_w, tile):
                    if ((x // tile) + (y // tile)) % 2 == 0:
                        draw_bg.rectangle((x, y, min(render_w, x + tile), min(render_h, y + tile)), fill=(58, 58, 58))
        draw = ImageDraw.Draw(preview)
        for shape in shapes[1:]:
            color = [int(v) for v in shape.get("color", [])]
            if len(color) == 4 and color[3] <= 0:
                continue
            r, g, b, _a = color
            shape_type = int(shape.get("type", 0))
            if shape_type == RECTANGLE:
                x, y, w, h = [float(v) for v in shape["data"]]
                x0 = int(round((x - w / 2) * render_scale))
                y0 = int(round((y - h / 2) * render_scale))
                x1 = int(round((x + w / 2) * render_scale))
                y1 = int(round((y + h / 2) * render_scale))
                draw.rectangle((x0, y0, x1, y1), fill=(r, g, b))
            elif shape_type == ROTATED_ELLIPSE:
                x, y, w, h, rot_deg = [float(v) for v in shape["data"]]
                draw_preview_ellipse_pillow(preview, x, y, w, h, rot_deg, (r, g, b), render_scale)
        if PREVIEW_JSON_SUPERSAMPLE > 1:
            preview = preview.resize((preview_w, preview_h), Image.Resampling.LANCZOS)
        return pil_to_photo(preview)
    except Exception:
        return None


def draw_preview_ellipse_pillow(image, x, y, w, h, rot_deg, color, scale):
    # Match the historical OpenCV preview path used before the one-file EXE:
    # cv2.ellipse(..., axes=(h, w), angle=-90+rot).
    width, height = image.size
    cx = float(x) * scale
    cy = float(y) * scale
    rx = max(float(h) * scale, 1.0)
    ry = max(float(w) * scale, 1.0)
    theta = (-90.0 + float(rot_deg)) * (math.pi / 180.0)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    inv_rx2 = 1.0 / (rx * rx)
    inv_ry2 = 1.0 / (ry * ry)
    extent_x = math.sqrt(rx * rx * cos_t * cos_t + ry * ry * sin_t * sin_t)
    extent_y = math.sqrt(rx * rx * sin_t * sin_t + ry * ry * cos_t * cos_t)
    x_min = max(0, int(math.floor(cx - extent_x - 1)))
    x_max = min(width - 1, int(math.ceil(cx + extent_x + 1)))
    y_min = max(0, int(math.floor(cy - extent_y - 1)))
    y_max = min(height - 1, int(math.ceil(cy + extent_y + 1)))
    if x_min > x_max or y_min > y_max:
        return
    pixels = image.load()
    r, g, b = color
    for yy in range(y_min, y_max + 1):
        dy = (float(yy) + 0.5) - cy
        for xx in range(x_min, x_max + 1):
            dx = (float(xx) + 0.5) - cx
            xr = dx * cos_t + dy * sin_t
            yr = -dx * sin_t + dy * cos_t
            if xr * xr * inv_rx2 + yr * yr * inv_ry2 <= 1.0:
                pixels[xx, yy] = (r, g, b)


class ThemedDropdown(Frame):
    """Small app-styled dropdown."""

    def __init__(self, parent, values=None, textvariable=None, command=None, width=28):
        super().__init__(parent, bg=Theme.BORDER, cursor="hand2")
        self.values = list(values or [])
        self.variable = textvariable or StringVar()
        self.command = command
        self.dropdown = None
        self.dropdown_panel = None
        self.position_job = None
        self.width = width
        self._var_trace = self.variable.trace_add("write", self._sync_label)

        self.inner = Frame(self, bg=Theme.INPUT, cursor="hand2")
        self.inner.pack(fill=BOTH, expand=True, padx=1, pady=1)

        self.label = Label(
            self.inner,
            text=self.variable.get(),
            width=width,
            anchor="w",
            bg=Theme.INPUT,
            fg=Theme.TEXT,
            padx=10,
            pady=5,
            font=(Theme.FONT_FAMILY, 10),
            cursor="hand2",
        )
        self.label.pack(side=LEFT, fill=X, expand=True)

        self.arrow_wrap = Frame(self.inner, bg=Theme.BUTTON, width=30, cursor="hand2")
        self.arrow_wrap.pack(side=RIGHT, fill=Y)
        self.arrow_wrap.pack_propagate(False)
        self.arrow = Canvas(self.arrow_wrap, width=30, height=24, bg=Theme.BUTTON, highlightthickness=0, cursor="hand2")
        self.arrow.pack(fill=BOTH, expand=True)
        self._draw_arrow(Theme.MUTED)

        for widget in (self, self.inner, self.label, self.arrow_wrap, self.arrow):
            widget.bind("<ButtonPress-1>", self._press, add="+")
            widget.bind("<Enter>", self._on_enter, add="+")
            widget.bind("<Leave>", self._on_leave, add="+")
        self.bind_all("<ButtonRelease-1>", self._handle_global_release, add="+")

    def _sync_label(self, *_args):
        self.label.configure(text=self.variable.get())

    def __setitem__(self, key, value):
        if key == "values":
            self.set_values(value)
        else:
            self.configure(**{key: value})

    def __getitem__(self, key):
        if key == "values":
            return tuple(self.values)
        return self.cget(key)

    def get(self):
        return self.variable.get()

    def set(self, value):
        self.variable.set(value)

    def set_values(self, values):
        self.values = list(values or [])
        if self.dropdown is not None:
            self._close_dropdown()

    def _draw_arrow(self, color):
        self.arrow.delete("all")
        self.arrow.create_polygon(10, 9, 20, 9, 15, 15, fill=color, outline="")

    def _set_hover(self, active=False):
        border = Theme.ACCENT if active else Theme.BORDER_STRONG
        button = Theme.BUTTON_ACTIVE if active else Theme.BUTTON_HOVER
        arrow = Theme.ACCENT_SOFT if active else Theme.TEXT
        self.configure(bg=border)
        self.arrow_wrap.configure(bg=button)
        self.arrow.configure(bg=button)
        self._draw_arrow(arrow)

    def _set_resting(self):
        if self.dropdown is not None:
            return
        self.configure(bg=Theme.BORDER)
        self.arrow_wrap.configure(bg=Theme.BUTTON)
        self.arrow.configure(bg=Theme.BUTTON)
        self._draw_arrow(Theme.MUTED)

    def _on_enter(self, _event=None):
        self._set_hover(self.dropdown is not None)

    def _on_leave(self, _event=None):
        if self.dropdown is None:
            self._set_resting()

    def _press(self, _event=None):
        self._set_hover(active=True)
        return "break"

    def _toggle(self):
        if self.dropdown is None and not self.winfo_viewable():
            return
        if self.dropdown is not None:
            self._close_dropdown()
        else:
            self._open_dropdown()

    def _event_inside_self(self, event):
        if not self.winfo_viewable():
            return False
        try:
            x, y = event.x_root, event.y_root
            left = self.winfo_rootx()
            top = self.winfo_rooty()
            return left <= x < left + self.winfo_width() and top <= y < top + self.winfo_height()
        except Exception:
            return False

    def _event_inside_dropdown(self, event):
        top = self.dropdown
        if top is None:
            return False
        try:
            x, y = event.x_root, event.y_root
            left = top.winfo_rootx()
            top_y = top.winfo_rooty()
            return left <= x < left + top.winfo_width() and top_y <= y < top_y + top.winfo_height()
        except Exception:
            return False

    def _handle_global_release(self, event):
        if not self.winfo_viewable():
            if self.dropdown is not None:
                self._close_dropdown()
            return None
        if self._event_inside_dropdown(event):
            return None
        if self._event_inside_self(event):
            self._toggle()
            return "break"
        if self.dropdown is not None:
            self._close_dropdown()
        return None

    def _open_dropdown(self):
        if not self.values or not self.winfo_viewable():
            return
        self.update_idletasks()
        self._set_hover(active=True)
        top = Toplevel(self)
        self.dropdown = top
        top.withdraw()
        top.overrideredirect(True)
        top.configure(bg=Theme.ACCENT_DARK)
        top.transient(self.winfo_toplevel())
        try:
            owner = self.winfo_toplevel()
            top.attributes("-topmost", bool(owner.attributes("-topmost")))
        except Exception:
            pass

        panel = Frame(top, bg=Theme.PANEL_HEADER)
        self.dropdown_panel = panel
        panel.pack(fill=BOTH, expand=True, padx=1, pady=1)

        selected = self.variable.get()
        for value in self.values:
            is_selected = value == selected
            row = Label(
                panel,
                text=value,
                anchor="w",
                bg=Theme.ACCENT_DARK if is_selected else Theme.PANEL_HEADER,
                fg=Theme.TEXT_ON_ACCENT if is_selected else Theme.TEXT,
                padx=10,
                pady=4,
                font=(Theme.FONT_FAMILY, 10),
                cursor="hand2",
            )
            row.pack(fill=X)
            row.bind("<Enter>", lambda _e, item=row: self._highlight_row(item), add="+")
            row.bind("<Leave>", lambda _e, item=row, item_value=value: self._rest_row(item, item_value), add="+")
            row.bind("<Button-1>", lambda _e, item_value=value: self._select_value(item_value), add="+")

        self._position_dropdown()
        top.deiconify()
        top.lift()
        try:
            top.focus_force()
        except Exception:
            pass
        try:
            top.grab_set()
        except Exception:
            pass
        top.bind("<Escape>", lambda _e: self._close_dropdown(), add="+")
        self._track_dropdown_position()

    def _position_dropdown(self):
        top = self.dropdown
        panel = self.dropdown_panel
        if top is None or panel is None:
            return
        try:
            top.update_idletasks()
            width = max(self.winfo_width(), panel.winfo_reqwidth() + 2)
            height = panel.winfo_reqheight() + 2
            x = self.winfo_rootx()
            y = self.winfo_rooty() + self.winfo_height()
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            if y + height > screen_h:
                y = self.winfo_rooty() - height
            if x + width > screen_w:
                x = max(0, screen_w - width)
            y = max(0, y)
            top.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            self._close_dropdown()

    def _track_dropdown_position(self):
        if self.dropdown is None:
            self.position_job = None
            return
        if not self.winfo_viewable():
            self._close_dropdown()
            return
        self._position_dropdown()
        self.position_job = self.after(30, self._track_dropdown_position)

    def _highlight_row(self, row):
        row.configure(bg=Theme.BUTTON_ACTIVE, fg=Theme.TEXT_ON_ACCENT)

    def _rest_row(self, row, value):
        if value == self.variable.get():
            row.configure(bg=Theme.ACCENT_DARK, fg=Theme.TEXT_ON_ACCENT)
        else:
            row.configure(bg=Theme.PANEL_HEADER, fg=Theme.TEXT)

    def _select_value(self, value):
        self.variable.set(value)
        self._close_dropdown()
        self.event_generate("<<ComboboxSelected>>")
        if self.command:
            self.command(value)
        return "break"

    def _close_if_focus_left(self):
        top = self.dropdown
        if top is None:
            return
        focus = self.focus_get()
        if focus is not None and str(focus).startswith(str(top)):
            return
        self._close_dropdown()

    def _close_dropdown(self):
        top = self.dropdown
        self.dropdown = None
        self.dropdown_panel = None
        if self.position_job is not None:
            try:
                self.after_cancel(self.position_job)
            except Exception:
                pass
            self.position_job = None
        if top is not None:
            try:
                top.grab_release()
            except Exception:
                pass
            try:
                top.attributes("-topmost", False)
            except Exception:
                pass
            try:
                top.destroy()
            except Exception:
                pass
        self._set_resting()


class App:
    def __init__(self, initial_images):
        ensure_dirs()
        set_windows_app_user_model_id()
        self.root = create_app_root()
        self.root.title(app_title())
        self.root.geometry("1500x940")
        self.root.minsize(1280, 820)
        self.root.configure(bg=Theme.BG)
        self.lang = "en"
        self.queue = queue.Queue()
        self.shutdown_event = threading.Event()
        self.active_processes = set()
        self.process_lock = threading.Lock()
        self.generation_lock = threading.Lock()
        self.generation_running = False
        self.current_generator_proc = None
        self.eta_samples = deque(maxlen=ETA_MAX_PROGRESS_SAMPLES)
        self.eta_display_time = None
        self.eta_smoothed_seconds_per_layer = None
        self.eta_display_remaining = None
        self.eta_max_layer_seen = None
        self.eta_recycle_notice_active = False
        self.closed = False
        self.settings = load_settings()
        self.images = [Path(path) for path in initial_images if Path(path).exists()]
        self.json_files = []
        self.outputs = []
        self.batch_queue_state = {}
        self.processes = []
        self.photo = None
        self.use_custom_settings = StringVar(value="0")
        self.custom_stop_at = StringVar()
        self.custom_max_resolution = StringVar()
        self.custom_random_samples = StringVar()
        self.custom_mutated_samples = StringVar()
        self.custom_save_at = StringVar()
        self.custom_preprocess_mode = StringVar(value="none")
        self.translated = []
        self.detailed_log_lock = threading.Lock()
        self.detailed_log_lines = deque()
        self.detailed_log_chars = 0
        self.runtime_location_log_timestamp = None
        self.current_preview_request = None
        self.preview_resize_job = None
        self.preview_resize_bucket = None
        self.readiness_resize_job = None
        self.readiness_resize_bucket = None
        self.update_state = {"status": "checking"}
        self.update_dialog = None
        self.update_check_started = False
        self.status = StringVar(value=tr(self.lang, "ready"))
        self.progress_text = StringVar(value="")
        self.progress_percent = StringVar(value="0%")
        self.batch_queue_text = StringVar(value="")
        self.compatibility_text = StringVar(value="")
        self.import_log_status = StringVar(value="")
        self.import_modal_percent = StringVar(value="0%")
        self.selected_profile = StringVar()
        self.selected_game = StringVar(value="fh6")
        self.selected_pid = StringVar()
        self.layer_count = StringVar()
        self.full_shape_count = StringVar(value="3000")
        self.full_shape_json_path = StringVar()
        self.full_shape_last_output_dir = StringVar(value=str(FULL_SHAPE_ROOT))
        self.full_shape_last_report_dir = StringVar()
        self.full_shape_clear_unused = StringVar(value="1")
        self.snapshot_count = StringVar()
        self.current_count = StringVar()
        self.count_address = StringVar()
        self.table_address = StringVar()
        self.inspect_table_value = StringVar()
        self.runtime_folder = StringVar(value=str(ROOT))
        self.output_folder = StringVar(value="")
        self.advanced_visible = False
        # Region Paint state
        self.region_images: list[Path] = []
        self.region_image_label_var = StringVar(value="")
        self._region_preview_showing: str = ""
        self.region_selected_profile = StringVar()
        self.region_total_var = StringVar(value="2000")
        self.region_first_var = StringVar(value="1000")
        self.region_layers_var = StringVar(value="300")
        self.region_remaining_var = StringVar(value="2000")
        self.region_tool = StringVar(value="rect")
        self.region_shapes: list[dict] = []
        self.region_selected_index: int | None = None
        self.region_rotation_var = IntVar(value=0)
        self.region_rotation_display = StringVar(value="0°")
        self.region_brush_size = IntVar(value=15)
        self.region_mask: "Image.Image | None" = None
        self.region_canvas_image_ref = None
        self.region_canvas_overlay_ref = None
        self._region_cached_pil = None
        self._region_cached_pil_path = None
        self._region_cached_display_pil = None
        self._region_cached_display_size = None
        self._region_canvas_image_offset = (0, 0)
        self.region_drag_start = None
        self.region_drag_mode: str | None = None
        self._region_move_snapshot: list[float] | None = None
        self._region_resize_corner: int | None = None
        self._region_resize_anchor_x: float = 0
        self._region_resize_anchor_y: float = 0
        self._region_handle_ids: list[int] = []
        self.region_rubber_id = None
        self.region_poly_points: list[float] = []
        self.region_current_output_dir: str = ""
        self.region_workflow_running = False
        self.region_status = StringVar(value=tr(self.lang, "ready"))
        self.region_progress = StringVar(value=tr(self.lang, "region_progress_idle"))
        self._region_right_tab: str = "preview"
        self.region_heatmap_ref = None
        self.region_heatmap_bar_ref = None
        self._region_heatmap_bar_item_id = None
        self._region_heatmap_showing: str = ""
        self.brand_mark_photo = None
        self.brand_mark_icon = None
        self.brand_window_icons = []
        self.brand_logo_photo = None
        self.log_area = None
        self.log_area_visible = False
        self.active_log_scope = None
        self.tab_log_entries = {
            "generate": deque(maxlen=1200),
            "import": deque(maxlen=1200),
            "full_shape": deque(maxlen=1200),
            "region": deque(maxlen=1200),
            "general": deque(maxlen=1200),
        }
        self.tab_log_progress = {
            scope: {"value": 0, "text": ""} for scope in self.tab_log_entries
        }
        self.generate_log_modal = None
        self.generate_modal_log = None
        self.generate_modal_progress = None
        self.generate_modal_source_label = None
        self.generate_modal_preview_label = None
        self.generate_modal_source_image = None
        self.generate_modal_preview_image = None
        self.generate_modal_source_path = None
        self.generate_modal_preview_path = None
        self.generate_modal_preview_shadow_path = None
        self.generate_modal_status = StringVar(value="")
        self.generate_modal_percent = StringVar(value="0%")
        self.generate_progress_value = 0
        self.import_running = False
        self.full_shape_running = False
        self.import_log_modal = None
        self.import_log_modal_scope = None
        self.import_modal_log = None
        self.import_modal_progress = None
        self.context_menu = None
        self.context_menu_outside_binds = []
        self.market_modal = None
        self.market_list = None
        self.market_preview_label = None
        self.market_preview_item_id = None
        self.market_notice = StringVar(value="")
        self.market_search = StringVar()
        self.market_view = StringVar(value="browse")
        self.market_sort = StringVar()
        self.market_layer_min = StringVar()
        self.market_layer_max = StringVar()
        self.market_fit_template_only = StringVar(value="0")
        self.market_filter_job = None
        self.market_layer_placeholders = {}
        self.market_detail_title = StringVar(value="")
        self.market_detail_author = StringVar(value="")
        self.market_detail_layers = StringVar(value="")
        self.market_detail_stats = StringVar(value="")
        self.market_detail_tags = StringVar(value="")
        self.market_detail_description = StringVar(value="")
        self.market_items = []
        self.market_all_items = []
        self.market_view_buttons = {}
        self.market_download_button = None
        self.market_open_button = None
        self.market_notice_key = None
        self.market_notice_payload = {}
        self.quality_settings_modal = None
        self.quality_modal_description = None
        self.active_modal = None
        self.custom_fields = []
        self._nav_icon_cache = {}
        self._step_badge_cache = {}
        self._load_brand_images()
        self._load_preferences()
        self.status.set(tr(self.lang, "ready"))
        self._build()
        self.status.trace_add("write", lambda *_: self._on_status_changed())
        self.layer_count.trace_add("write", lambda *_: self._refresh_import_readiness())
        self.selected_pid.trace_add("write", lambda *_: self._refresh_import_readiness())
        self.use_custom_settings.trace_add("write", lambda *_: self._update_quality_summary())
        self.custom_stop_at.trace_add("write", lambda *_: self._update_quality_summary())
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # When the app is re-activated from the taskbar / alt-tab, any open
        # modal (borderless Toplevel) can be left behind the root. Re-raise it
        # so the user can always interact with the active modal.
        self.root.bind("<FocusIn>", self._on_root_activated, add="+")
        self.root.bind("<Map>", self._on_root_activated, add="+")
        self.refresh_processes()
        if self.settings:
            default_setting = self.settings[min(2, len(self.settings) - 1)]
            self.selected_profile.set(self._localized_profile_label(default_setting))
            self._update_setting_description()
        for image_path in list(self.images):
            self._load_existing_checkpoints_for_image(image_path)
        self._render_lists()
        self._log_runtime_location()
        self._poll_queue()
        self.root.after(1000, self.start_update_check)

    def _configure_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        family = Theme.FONT_FAMILY
        style.configure(
            ".",
            background=Theme.BG,
            foreground=Theme.TEXT,
            fieldbackground=Theme.INPUT,
            font=(family, 10),
        )
        style.configure("TFrame", background=Theme.BG)
        style.configure("TNotebook", background=Theme.BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("Primary.TNotebook", background=Theme.BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure(
            "Primary.TNotebook.Tab",
            padding=(22, 10),
            font=(family, 10, "bold"),
            background=Theme.PANEL,
            foreground=Theme.MUTED,
            borderwidth=0,
            focuscolor=Theme.BG,
        )
        style.map(
            "Primary.TNotebook.Tab",
            background=[("selected", Theme.PANEL_ALT), ("active", Theme.PANEL_ALT)],
            foreground=[("selected", Theme.ACCENT), ("active", Theme.TEXT)],
            expand=[("selected", (0, 0, 0, 0))],
        )
        style.configure(
            "TLabelframe",
            background=Theme.PANEL,
            foreground=Theme.TEXT,
            bordercolor=Theme.BORDER,
            lightcolor=Theme.BORDER,
            darkcolor=Theme.BORDER,
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "TLabelframe.Label",
            background=Theme.PANEL,
            foreground=Theme.ACCENT_SOFT,
            font=(family, 10, "bold"),
            padding=(6, 0),
        )
        style.configure(
            "TCombobox",
            fieldbackground=Theme.INPUT,
            background=Theme.BUTTON,
            foreground=Theme.TEXT,
            arrowcolor=Theme.MUTED,
            bordercolor=Theme.BORDER,
            lightcolor=Theme.BORDER,
            darkcolor=Theme.BORDER,
            padding=4,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", Theme.INPUT)],
            foreground=[("readonly", Theme.TEXT)],
            bordercolor=[("focus", Theme.ACCENT), ("hover", Theme.BORDER_STRONG)],
            lightcolor=[("focus", Theme.ACCENT)],
            darkcolor=[("focus", Theme.ACCENT)],
            arrowcolor=[("active", Theme.ACCENT), ("hover", Theme.TEXT)],
            selectbackground=[("readonly", Theme.ACCENT_DARK)],
            selectforeground=[("readonly", Theme.TEXT_ON_ACCENT)],
        )
        # `userDefault` priority outranks Windows-native widget defaults.
        for prop, value in (
            ("background", Theme.INPUT),
            ("foreground", Theme.TEXT),
            ("selectBackground", Theme.ACCENT_DARK),
            ("selectForeground", Theme.TEXT_ON_ACCENT),
            ("borderWidth", 0),
            ("highlightThickness", 0),
            ("relief", "flat"),
            ("activeStyle", "none"),
        ):
            self.root.option_add(f"*TCombobox*Listbox.{prop}", value, "userDefault")
        self.root.option_add("*TCombobox*Listbox.font", (family, 10), "userDefault")
        style.configure(
            "TScrollbar",
            background=Theme.BORDER,
            troughcolor=Theme.BG,
            bordercolor=Theme.BG,
            arrowcolor=Theme.BG,
            gripcount=0,
            relief="flat",
            borderwidth=0,
            width=10,
        )
        style.map(
            "TScrollbar",
            background=[("active", Theme.BORDER_STRONG), ("pressed", Theme.ACCENT_DARK)],
            arrowcolor=[("active", Theme.BG)],
        )
        # Indeterminate progress bar for the log header
        style.configure(
            "App.Horizontal.TProgressbar",
            background=Theme.ACCENT,
            troughcolor=Theme.INPUT,
            bordercolor=Theme.BORDER,
            lightcolor=Theme.ACCENT,
            darkcolor=Theme.ACCENT_DARK,
            thickness=4,
        )

    def _register_process(self, proc):
        with self.process_lock:
            self.active_processes.add(proc)

    def _popen_registered(self, cmd, **kwargs):
        # Hold process_lock across Popen + registration so on_close cannot miss
        # a child process that starts while the window is closing.
        with self.process_lock:
            if self.shutdown_event.is_set() or self.closed:
                return None
            proc = subprocess.Popen(cmd, **kwargs)
            self.active_processes.add(proc)
            return proc

    def _unregister_process(self, proc):
        with self.process_lock:
            self.active_processes.discard(proc)

    def _terminate_process(self, proc):
        if proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=5,
                )
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _terminate_active_processes(self):
        with self.process_lock:
            processes = list(self.active_processes)
        for proc in processes:
            self._terminate_process(proc)

    def on_close(self):
        self.closed = True
        self.shutdown_event.set()
        self._hide_themed_context_menu()
        self._deactivate_modal(self.active_modal)
        self._terminate_active_processes()
        self.root.destroy()

    def _parent_bg(self, parent):
        try:
            return parent.cget("bg")
        except Exception:
            return Theme.PANEL

    def _label(self, parent, key, **kwargs):
        kwargs.setdefault("bg", self._parent_bg(parent))
        kwargs.setdefault("fg", Theme.TEXT)
        widget = Label(parent, text=tr(self.lang, key), **kwargs)
        self.translated.append((widget, key, "text"))
        return widget

    def _layout_width_bucket(self, width):
        try:
            width = int(width)
        except (TypeError, ValueError):
            width = 0
        return max(0, width // LAYOUT_SIZE_BUCKET)

    def _schedule_wrap_update(self, parent, widget, state, min_wrap, margin, event=None):
        try:
            width = event.width if event is not None else parent.winfo_width()
        except Exception:
            width = 0
        bucket = self._layout_width_bucket(width)
        if bucket == state.get("bucket"):
            return
        state["bucket"] = bucket
        job = state.get("job")
        if job is not None:
            try:
                parent.after_cancel(job)
            except Exception:
                pass
        state["job"] = parent.after(
            LAYOUT_RESIZE_DEBOUNCE_MS,
            lambda w=width: self._apply_wrap_update(parent, widget, state, min_wrap, margin, w),
        )

    def _apply_wrap_update(self, parent, widget, state, min_wrap, margin, width=None):
        state["job"] = None
        try:
            if width is None:
                width = parent.winfo_width()
            wraplength = max(min_wrap, int(width) - margin)
            current = int(float(widget.cget("wraplength") or 0))
            if abs(current - wraplength) >= 8:
                widget.configure(wraplength=wraplength)
        except Exception:
            pass

    def _responsive_label(self, parent, key, min_wrap=180, margin=0, **kwargs):
        widget = self._label(parent, key, **kwargs)
        state = {"job": None, "bucket": None}
        parent.bind(
            "<Configure>",
            lambda event: self._schedule_wrap_update(parent, widget, state, min_wrap, margin, event),
            add="+",
        )
        widget.after_idle(lambda: self._apply_wrap_update(parent, widget, state, min_wrap, margin))
        return widget

    def _responsive_variable_label(self, parent, variable, min_wrap=180, margin=0, **kwargs):
        kwargs.setdefault("bg", self._parent_bg(parent))
        kwargs.setdefault("fg", Theme.TEXT)
        widget = Label(parent, textvariable=variable, **kwargs)
        state = {"job": None, "bucket": None}
        parent.bind(
            "<Configure>",
            lambda event: self._schedule_wrap_update(parent, widget, state, min_wrap, margin, event),
            add="+",
        )
        widget.after_idle(lambda: self._apply_wrap_update(parent, widget, state, min_wrap, margin))
        return widget

    def _entry_with_placeholder(self, entry, variable, placeholder_key):
        state = {"active": False}
        self.market_layer_placeholders[entry] = (placeholder_key, state)

        def show_placeholder():
            if str(variable.get() or ""):
                return
            state["active"] = True
            entry.config(fg=Theme.SUBTLE)
            entry.delete(0, END)
            entry.insert(0, tr(self.lang, placeholder_key))

        def hide_placeholder():
            if not state["active"]:
                return
            state["active"] = False
            entry.config(fg=Theme.TEXT)
            entry.delete(0, END)

        def on_focus_in(_event=None):
            hide_placeholder()

        def on_focus_out(_event=None):
            if not str(variable.get() or "").strip():
                show_placeholder()

        def on_key_release(_event=None):
            state["active"] = False
            entry.config(fg=Theme.TEXT)
            self._schedule_market_filter_apply()

        entry.bind("<FocusIn>", on_focus_in, add="+")
        entry.bind("<FocusOut>", on_focus_out, add="+")
        entry.bind("<KeyRelease>", on_key_release, add="+")
        entry.after_idle(show_placeholder)

    def _refresh_entry_placeholders(self):
        for entry, (placeholder_key, state) in list(getattr(self, "market_layer_placeholders", {}).items()):
            try:
                if state.get("active"):
                    entry.delete(0, END)
                    entry.insert(0, tr(self.lang, placeholder_key))
            except Exception:
                pass

    def _badge_width(self, value):
        text = str(value)
        return 24 if len(text) <= 1 else max(32, 12 + len(text) * 10)

    def _button(self, parent, key, command, **kwargs):
        kwargs.setdefault("bg", Theme.BUTTON)
        kwargs.setdefault("fg", Theme.TEXT)
        kwargs.setdefault("disabledforeground", Theme.MUTED)
        kwargs.setdefault("activebackground", Theme.BUTTON_ACTIVE)
        kwargs.setdefault("activeforeground", Theme.TEXT)
        kwargs.setdefault("relief", "flat")
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("highlightthickness", 1)
        kwargs.setdefault("highlightbackground", Theme.BORDER)
        kwargs.setdefault("highlightcolor", Theme.ACCENT)
        kwargs.setdefault("padx", 12)
        kwargs.setdefault("pady", 5)
        kwargs.setdefault("font", (Theme.FONT_FAMILY, 10))
        kwargs.setdefault("cursor", "hand2")
        widget = Button(parent, text=tr(self.lang, key), command=command, **kwargs)
        self._attach_button_hover(widget)
        self.translated.append((widget, key, "text"))
        return widget

    def _attach_button_hover(self, widget, hover_bg=None, base_bg=None, hover_border=None):
        resolved_base = base_bg if base_bg is not None else widget.cget("bg")
        resolved_hover = hover_bg if hover_bg is not None else Theme.BUTTON_HOVER
        resolved_border = hover_border if hover_border is not None else Theme.BORDER_STRONG

        def on_enter(_event=None):
            try:
                if str(widget.cget("state")) != "disabled":
                    widget.configure(bg=resolved_hover, highlightbackground=resolved_border)
            except Exception:
                pass

        def on_leave(_event=None):
            try:
                widget.configure(bg=resolved_base, highlightbackground=Theme.BORDER)
            except Exception:
                pass

        widget.bind("<Enter>", on_enter, add="+")
        widget.bind("<Leave>", on_leave, add="+")

    def _primary_button(self, parent, key, command, variant="success", **kwargs):
        if variant == "accent":
            base, hover, hover_border = Theme.ACCENT_DARK, Theme.ACCENT, Theme.ACCENT_SOFT
        else:
            base, hover, hover_border = Theme.SUCCESS_DARK, Theme.SUCCESS_HOVER, Theme.SUCCESS
        kwargs.setdefault("bg", base)
        kwargs.setdefault("fg", Theme.TEXT_ON_ACCENT)
        kwargs.setdefault("activebackground", hover)
        kwargs.setdefault("activeforeground", Theme.TEXT_ON_ACCENT)
        kwargs.setdefault("disabledforeground", "#c8d3e1")
        kwargs.setdefault("relief", "flat")
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("padx", 16)
        kwargs.setdefault("pady", 9)
        kwargs.setdefault("font", (Theme.FONT_FAMILY, 11, "bold"))
        kwargs.setdefault("cursor", "hand2")
        widget = Button(parent, text=tr(self.lang, key), command=command, **kwargs)
        self._attach_button_hover(widget, hover_bg=hover, base_bg=base, hover_border=hover_border)
        self.translated.append((widget, key, "text"))
        return widget

    def _danger_button(self, parent, key, command, **kwargs):
        kwargs.setdefault("bg", Theme.BUTTON)
        kwargs.setdefault("fg", Theme.DANGER)
        kwargs.setdefault("activebackground", Theme.BUTTON_ACTIVE)
        kwargs.setdefault("activeforeground", Theme.DANGER_HOVER)
        kwargs.setdefault("disabledforeground", Theme.MUTED)
        kwargs.setdefault("relief", "flat")
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("highlightthickness", 1)
        kwargs.setdefault("highlightbackground", Theme.BORDER)
        kwargs.setdefault("padx", 14)
        kwargs.setdefault("pady", 7)
        kwargs.setdefault("font", (Theme.FONT_FAMILY, 10, "bold"))
        kwargs.setdefault("cursor", "hand2")
        widget = Button(parent, text=tr(self.lang, key), command=command, **kwargs)
        self._attach_button_hover(widget, hover_bg=Theme.BUTTON_HOVER, base_bg=Theme.BUTTON, hover_border=Theme.DANGER)
        self.translated.append((widget, key, "text"))
        return widget

    def _section_header(self, parent, text, **kwargs):
        kwargs.setdefault("bg", self._parent_bg(parent))
        kwargs.setdefault("fg", Theme.SECTION)
        kwargs.setdefault("font", (Theme.FONT_FAMILY, 9, "bold"))
        kwargs.setdefault("anchor", "w")
        return Label(parent, text=text.upper(), **kwargs)

    def _mapped_label_color(self, color):
        value = str(color or "").lower()
        if value in ("black", "#000000", "#000", "systembuttontext", "systemwindowtext"):
            return Theme.TEXT
        if value in ("systemdisabledtext", "gray", "grey", "gray40", "grey40"):
            return Theme.MUTED
        if value in ("#555", "#555555", "gray50", "grey50"):
            return Theme.MUTED
        if value in ("#005a9e", "blue", "#4c9aff", "#7ab8ff"):
            return Theme.ACCENT_SOFT
        if value in ("#8a5300", "orange", "darkorange", "#f5b544", "#f2cc60"):
            return Theme.WARN
        return color if color else Theme.TEXT

    def _apply_dark_theme_recursive(self, widget):
        if isinstance(widget, ThemedDropdown):
            return
        if getattr(widget, "_keep_custom_theme", False):
            return
        try:
            if isinstance(widget, Frame):
                current = str(widget.cget("bg") or "").lower()
                themed = {
                    Theme.BG.lower(), Theme.PANEL.lower(), Theme.PANEL_ALT.lower(),
                    Theme.INPUT.lower(), Theme.BORDER.lower(), Theme.PREVIEW_BG.lower(),
                    Theme.BUTTON.lower(),
                }
                if current not in themed:
                    bg = Theme.BG if widget.master is self.root else Theme.PANEL
                    widget.configure(bg=bg)
            elif isinstance(widget, Label):
                if widget in (getattr(self, "preview_label", None), getattr(self, "import_preview_label", None)):
                    widget.configure(bg=Theme.INPUT, fg=Theme.TEXT)
                elif widget is getattr(self, "quality_selected_label", None):
                    widget.configure(bg=Theme.INPUT, fg=Theme.TEXT)
                elif widget is getattr(self, "update_indicator", None):
                    widget.configure(bg=Theme.PANEL)
                else:
                    widget.configure(bg=self._parent_bg(widget.master), fg=self._mapped_label_color(widget.cget("fg")))
            elif isinstance(widget, Button):
                current_bg = str(widget.cget("bg") or "").lower()
                styled_bgs = {
                    Theme.SUCCESS_DARK.lower(),
                    Theme.SUCCESS.lower(),
                    Theme.SUCCESS_HOVER.lower(),
                    Theme.ACCENT_DARK.lower(),
                    Theme.ACCENT.lower(),
                    Theme.DANGER.lower(),
                }
                if current_bg in styled_bgs or str(widget.cget("fg") or "").lower() == Theme.DANGER.lower():
                    pass
                else:
                    widget.configure(
                        bg=Theme.BUTTON,
                        fg=Theme.TEXT,
                        disabledforeground=Theme.MUTED,
                        activebackground=Theme.BUTTON_ACTIVE,
                        activeforeground=Theme.TEXT,
                        relief="flat",
                        bd=0,
                        highlightbackground=Theme.BORDER,
                        highlightcolor=Theme.ACCENT,
                    )
            elif isinstance(widget, Checkbutton):
                widget.configure(
                    bg=self._parent_bg(widget.master),
                    fg=Theme.TEXT,
                    disabledforeground=Theme.MUTED,
                    activebackground=self._parent_bg(widget.master),
                    activeforeground=Theme.TEXT,
                    selectcolor=Theme.INPUT,
                    relief="flat",
                    highlightbackground=Theme.BORDER,
                    highlightcolor=Theme.ACCENT,
                )
            elif isinstance(widget, Entry):
                widget.configure(
                    bg=Theme.INPUT,
                    fg=Theme.TEXT,
                    insertbackground=Theme.ACCENT,
                    disabledbackground=Theme.PANEL_ALT,
                    disabledforeground=Theme.MUTED,
                    readonlybackground=Theme.INPUT,
                    relief="flat",
                    borderwidth=0,
                    highlightthickness=1,
                    highlightbackground=Theme.BORDER,
                    highlightcolor=Theme.ACCENT,
                    font=(Theme.FONT_FAMILY, 10),
                )
            elif isinstance(widget, Listbox):
                widget.configure(
                    bg=Theme.INPUT,
                    fg=Theme.TEXT,
                    selectbackground=Theme.ACCENT_DARK,
                    selectforeground=Theme.TEXT_ON_ACCENT,
                    highlightthickness=1,
                    highlightbackground=Theme.BORDER,
                    relief="flat",
                    borderwidth=0,
                    activestyle="none",
                    font=(Theme.FONT_FAMILY, 10),
                )
            elif isinstance(widget, Text):
                widget.configure(
                    bg=Theme.INPUT,
                    fg=Theme.TEXT,
                    insertbackground=Theme.TEXT,
                    selectbackground=Theme.ACCENT_DARK,
                    selectforeground=Theme.TEXT_ON_ACCENT,
                    highlightthickness=1,
                    highlightbackground=Theme.BORDER,
                    relief="flat",
                    borderwidth=0,
                    padx=10,
                    pady=8,
                    font=(Theme.FONT_MONO, 9)
                    if widget in (getattr(self, "log", None), getattr(self, "import_modal_log", None))
                    else (Theme.FONT_FAMILY, 10),
                )
            elif isinstance(widget, Canvas):
                widget.configure(bg=Theme.BG, highlightthickness=0)
        except Exception:
            pass
        for child in widget.winfo_children():
            self._apply_dark_theme_recursive(child)

    def _build(self):
        self._configure_styles()
        self.current_section = "generate"
        self.nav_items = {}
        self.section_frames = {}

        # --- root: sidebar | (topbar + content + log)
        self._build_sidebar(self.root)

        right = Frame(self.root, bg=Theme.BG)
        right.pack(side=LEFT, fill=BOTH, expand=True)

        self._build_topbar(right)
        self._build_log(right)

        # main scrollable content (per-section)
        self.content_area = Frame(right, bg=Theme.BG)
        self.content_area.pack(fill=BOTH, expand=True, padx=24, pady=(8, 0))

        self.generate_tab = Frame(self.content_area, bg=Theme.BG)
        self.import_tab = Frame(self.content_area, bg=Theme.BG)
        self.full_shape_tab = Frame(self.content_area, bg=Theme.BG)
        self.region_paint_tab = Frame(self.content_area, bg=Theme.BG)
        self.tools_tab = Frame(self.content_area, bg=Theme.BG)
        self.tutorial_tab = Frame(self.content_area, bg=Theme.BG)
        self.section_frames = {
            "generate": self.generate_tab,
            "import": self.import_tab,
            "full_shape": self.full_shape_tab,
            "region": self.region_paint_tab,
            "tools": self.tools_tab,
            "tutorial": self.tutorial_tab,
        }

        self._build_generate_tab()
        self._build_import_tab()
        self._build_full_shape_tab()
        self._build_region_paint_tab()
        self._build_tools_tab()
        self._build_tutorial_tab()
        self._select_section("generate")
        self._apply_dark_theme_recursive(self.root)

    # ------------------------------------------------------------------ sidebar
    def _build_sidebar(self, parent):
        # Outer wrapper holds sidebar + right hairline border
        outer = Frame(parent, bg=Theme.PANEL)
        outer.pack(side=LEFT, fill=Y)
        sidebar = Frame(outer, bg=Theme.PANEL, width=240)
        sidebar.pack(side=LEFT, fill=Y)
        sidebar.pack_propagate(False)
        Frame(outer, bg=Theme.BORDER, width=1).pack(side=LEFT, fill=Y)

        # Brand block — use brand mark image if available, fall back to drawn mark
        brand = Frame(sidebar, bg=Theme.PANEL)
        brand.pack(fill=X, padx=20, pady=(24, 30))
        mark_row = Frame(brand, bg=Theme.PANEL)
        mark_row.pack(fill=X)
        if self.brand_mark_photo is not None:
            mark = Label(mark_row, image=self.brand_mark_photo, bg=Theme.PANEL, bd=0)
            mark.image = self.brand_mark_photo
            mark.pack(side=LEFT, padx=(0, 14))
        else:
            mark = Canvas(mark_row, width=48, height=48, bg=Theme.PANEL, highlightthickness=0)
            mark.create_rectangle(6, 6, 42, 42, outline=Theme.BORDER_STRONG, width=1)
            mark.create_polygon(10, 38, 38, 10, 32, 5, 5, 32, fill=Theme.ACCENT, outline="")
            mark.create_oval(30, 30, 44, 44, fill=Theme.SUCCESS, outline=Theme.PANEL, width=2)
            mark.pack(side=LEFT, padx=(0, 14))
        title_block = Frame(mark_row, bg=Theme.PANEL)
        title_block.pack(side=LEFT, fill=X, expand=True)
        Label(title_block, text="Forza-Painter", bg=Theme.PANEL, fg=Theme.TEXT,
              font=(Theme.FONT_FAMILY, 14, "bold"), anchor="w").pack(fill=X)
        meta_row = Frame(title_block, bg=Theme.PANEL)
        meta_row.pack(fill=X, pady=(3, 0))
        Label(
            meta_row, text="FH6", bg=Theme.PANEL, fg=Theme.MUTED,
            font=(Theme.FONT_FAMILY, 9), anchor="w",
        ).pack(side=LEFT)
        badge = Canvas(meta_row, width=70, height=22, bg=Theme.PANEL, highlightthickness=0)
        badge.pack(side=LEFT, padx=(8, 0))
        self._draw_version_badge(badge)

        # Nav section label
        self._label(sidebar, "workspace", bg=Theme.PANEL, fg=Theme.SUBTLE,
                    font=(Theme.FONT_FAMILY, 8, "bold"), anchor="w").pack(fill=X, padx=22, pady=(0, 8))

        nav = Frame(sidebar, bg=Theme.PANEL)
        nav.pack(fill=X, padx=12)
        for key, label_key, glyph in (
            ("generate", "generate_tab", "spark"),
            ("import", "import_tab", "download"),
            ("full_shape", "full_shape_tab", "upload"),
            ("region", "region_paint_tab", "spark"),
            ("tutorial", "tutorial_tab", "book"),
        ):
            self._nav_item(nav, key, label_key, glyph).pack(fill=X, pady=2)

        # Language picker pinned to bottom
        bottom = Frame(sidebar, bg=Theme.PANEL)
        bottom.pack(side=BOTTOM, fill=X, padx=22, pady=22)
        self._label(bottom, "language", bg=Theme.PANEL, fg=Theme.SUBTLE,
                    font=(Theme.FONT_FAMILY, 8, "bold"), anchor="w").pack(fill=X, pady=(0, 6))
        self.lang_combo = ThemedDropdown(bottom, values=list(LANGUAGES.keys()), width=18)
        # Reflect the saved language preference.
        display_name = next((name for name, code in LANGUAGES.items() if code == self.lang), "English")
        self.lang_combo.set(display_name)
        self.lang_combo.pack(fill=X)
        self.lang_combo.bind("<<ComboboxSelected>>", self._on_language)

    def _draw_version_badge(self, canvas):
        """Draw a compact neon version badge for the sidebar brand block."""
        canvas.delete("all")
        w, h = 70, 22
        canvas.create_rectangle(5, 5, w - 3, h - 3, fill=Theme.ACCENT_DARK, outline="")
        canvas.create_rectangle(3, 4, w - 5, h - 4, fill=Theme.BUTTON, outline=Theme.ACCENT_DARK, width=1)
        canvas.create_line(6, 5, w - 8, 5, fill=Theme.ACCENT_SOFT, width=1)
        canvas.create_oval(9, 8, 15, 14, fill=Theme.ACCENT, outline="")
        canvas.create_text(
            42, 11,
            text=f"v{__version__}",
            fill=Theme.TEXT,
            font=(Theme.FONT_FAMILY, 8, "bold"),
        )

    def _nav_item(self, parent, key, label_key, glyph):
        container = Frame(parent, bg=Theme.PANEL, cursor="hand2", height=42)
        container.pack_propagate(False)
        bar = Frame(container, bg=Theme.PANEL, width=3)
        bar.pack(side=LEFT, fill=Y)
        body = Frame(container, bg=Theme.PANEL)
        body.pack(side=LEFT, fill=BOTH, expand=True)

        img_muted = self._render_nav_icon(glyph, Theme.MUTED)
        img_text = self._render_nav_icon(glyph, Theme.TEXT)
        img_accent = self._render_nav_icon(glyph, Theme.ACCENT)

        if img_muted is not None:
            icon = Label(body, image=img_muted, bg=Theme.PANEL, bd=0)
            icon.image = img_muted
            icon.pack(side=LEFT, padx=(13, 11))
            icon_ids = None
        else:
            icon = Canvas(body, width=22, height=22, bg=Theme.PANEL, highlightthickness=0)
            icon_ids = self._draw_glyph(icon, glyph, Theme.MUTED)
            icon.pack(side=LEFT, padx=(13, 11))

        label = Label(body, text=tr(self.lang, label_key), bg=Theme.PANEL, fg=Theme.MUTED,
                      font=(Theme.FONT_FAMILY, 10, "bold"), anchor="w")
        label.pack(side=LEFT, fill=BOTH, expand=True)

        def set_icon(variant):
            if img_muted is None:
                color = {"muted": Theme.MUTED, "text": Theme.TEXT, "accent": Theme.ACCENT}[variant]
                self._set_glyph_color(icon, icon_ids, color)
                return
            img = {"muted": img_muted, "text": img_text, "accent": img_accent}[variant]
            icon.configure(image=img)
            icon.image = img

        def on_click(_event=None):
            self._select_section(key)

        for widget in (container, bar, body, icon, label):
            widget.bind("<Button-1>", on_click)

        def on_enter(_event=None):
            if self.current_section != key:
                for w in (container, body, label, icon):
                    w.configure(bg=Theme.PANEL_ALT)
                bar.configure(bg=Theme.PANEL_ALT)
                label.configure(fg=Theme.TEXT)
                set_icon("text")

        def on_leave(_event=None):
            if self.current_section != key:
                for w in (container, body, label, icon):
                    w.configure(bg=Theme.PANEL)
                bar.configure(bg=Theme.PANEL)
                label.configure(fg=Theme.MUTED)
                set_icon("muted")

        for widget in (container, bar, body, icon, label):
            widget.bind("<Enter>", on_enter)
            widget.bind("<Leave>", on_leave)

        self.nav_items[key] = {
            "container": container, "bar": bar, "body": body,
            "label": label, "icon": icon, "icon_ids": icon_ids, "label_key": label_key,
            "set_icon": set_icon,
        }
        return container

    def _draw_glyph(self, canvas, name, color):
        """Draws a compact vector glyph (no emoji). Returns item ids for color updates."""
        ids = []
        if name == "spark":
            # Curly braces with a center sparkle — reads as JSON at a glance.
            ids.append(canvas.create_line(
                8, 4, 6, 5, 6, 10, 4, 11, 6, 12, 6, 17, 8, 18,
                fill=color, width=2, capstyle="round", joinstyle="round", smooth=False))
            ids.append(canvas.create_line(
                14, 4, 16, 5, 16, 10, 18, 11, 16, 12, 16, 17, 14, 18,
                fill=color, width=2, capstyle="round", joinstyle="round", smooth=False))
            ids.append(canvas.create_oval(10, 10, 12, 12, fill=color, outline=color))
        elif name == "download":
            # Downward arrow landing into an open inbox/tray.
            ids.append(canvas.create_line(11, 3, 11, 12, fill=color, width=2, capstyle="round"))
            ids.append(canvas.create_line(
                7, 8, 11, 12, 15, 8,
                fill=color, width=2, capstyle="round", joinstyle="round"))
            ids.append(canvas.create_line(
                4, 14, 4, 18, 18, 18, 18, 14,
                fill=color, width=2, capstyle="round", joinstyle="round"))
            ids.append(canvas.create_line(4, 14, 8, 14, fill=color, width=2, capstyle="round"))
            ids.append(canvas.create_line(14, 14, 18, 14, fill=color, width=2, capstyle="round"))
        elif name == "upload":
            # Upward arrow leaving an open tray for Export.
            ids.append(canvas.create_line(11, 12, 11, 3, fill=color, width=2, capstyle="round"))
            ids.append(canvas.create_line(
                7, 7, 11, 3, 15, 7,
                fill=color, width=2, capstyle="round", joinstyle="round"))
            ids.append(canvas.create_line(
                4, 14, 4, 18, 18, 18, 18, 14,
                fill=color, width=2, capstyle="round", joinstyle="round"))
            ids.append(canvas.create_line(4, 14, 8, 14, fill=color, width=2, capstyle="round"))
            ids.append(canvas.create_line(14, 14, 18, 14, fill=color, width=2, capstyle="round"))
        elif name == "book":
            # Open book — two pages joined at a central spine.
            ids.append(canvas.create_line(
                4, 6, 4, 17, 11, 18, 18, 17, 18, 6, 11, 7, 4, 6,
                fill=color, width=2, capstyle="round", joinstyle="round"))
            ids.append(canvas.create_line(11, 7, 11, 18, fill=color, width=2, capstyle="round"))
        elif name == "play":
            # Triangle play icon for primary CTA
            ids.append(canvas.create_polygon(4, 3, 4, 15, 14, 9, fill=color, outline=""))
        return ids

    def _set_glyph_color(self, canvas, item_ids, color):
        for item_id in item_ids:
            try:
                fill = canvas.itemcget(item_id, "fill")
            except Exception:
                fill = None
            try:
                outline = canvas.itemcget(item_id, "outline")
            except Exception:
                outline = None
            if fill:
                try:
                    canvas.itemconfigure(item_id, fill=color)
                except Exception:
                    pass
            if outline:
                try:
                    canvas.itemconfigure(item_id, outline=color)
                except Exception:
                    pass

    # ---- High-DPI PIL icon renderers (antialiased via 4x supersampling) ----
    _ICON_SCALE = 4

    def _render_nav_icon(self, glyph, color):
        cache_key = (glyph, color)
        cached = self._nav_icon_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            from PIL import Image, ImageDraw, ImageTk
        except Exception:
            return None
        s = self._ICON_SCALE
        size = 22 * s
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        stroke = 2 * s

        def scaled(points):
            return [(x * s, y * s) for x, y in points]

        if glyph == "spark":
            d.line(scaled([(8, 4), (6, 5), (6, 10), (4, 11), (6, 12), (6, 17), (8, 18)]),
                   fill=color, width=stroke, joint="curve")
            d.line(scaled([(14, 4), (16, 5), (16, 10), (18, 11), (16, 12), (16, 17), (14, 18)]),
                   fill=color, width=stroke, joint="curve")
            d.ellipse([(10 * s, 10 * s), (12 * s, 12 * s)], fill=color)
        elif glyph == "download":
            d.line(scaled([(11, 3), (11, 12)]), fill=color, width=stroke)
            d.line(scaled([(7, 8), (11, 12), (15, 8)]), fill=color, width=stroke, joint="curve")
            d.line(scaled([(4, 14), (4, 18), (18, 18), (18, 14)]), fill=color, width=stroke, joint="curve")
            d.line(scaled([(4, 14), (8, 14)]), fill=color, width=stroke)
            d.line(scaled([(14, 14), (18, 14)]), fill=color, width=stroke)
        elif glyph == "upload":
            d.line(scaled([(11, 12), (11, 3)]), fill=color, width=stroke)
            d.line(scaled([(7, 7), (11, 3), (15, 7)]), fill=color, width=stroke, joint="curve")
            d.line(scaled([(4, 14), (4, 18), (18, 18), (18, 14)]), fill=color, width=stroke, joint="curve")
            d.line(scaled([(4, 14), (8, 14)]), fill=color, width=stroke)
            d.line(scaled([(14, 14), (18, 14)]), fill=color, width=stroke)
        elif glyph == "book":
            d.line(scaled([(4, 6), (4, 17), (11, 18), (18, 17), (18, 6), (11, 7), (4, 6)]),
                   fill=color, width=stroke, joint="curve")
            d.line(scaled([(11, 7), (11, 18)]), fill=color, width=stroke)
        else:
            return None

        img = img.resize((22, 22), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        self._nav_icon_cache[cache_key] = photo
        return photo

    def _render_step_badge(self, step):
        cache_key = str(step)
        cached = self._step_badge_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageTk
        except Exception:
            return None
        s = self._ICON_SCALE
        size = 24 * s
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([(0, 0), (size, size)], fill=Theme.ACCENT_DARK)
        inset = 2 * s
        d.ellipse([(inset, inset), (size - inset, size - inset)], fill=Theme.ACCENT)

        text = str(step)
        font = None
        for name in ("seguibold.ttf", "arialbd.ttf", "segoeuib.ttf"):
            try:
                font = ImageFont.truetype(name, 12 * s)
                break
            except Exception:
                continue
        if font is None:
            font = ImageFont.load_default()
        bbox = d.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = (size - tw) / 2 - bbox[0]
        ty = (size - th) / 2 - bbox[1]
        d.text((tx, ty), text, fill=Theme.TEXT_ON_ACCENT, font=font)

        img = img.resize((24, 24), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        self._step_badge_cache[cache_key] = photo
        return photo

    def _preferences_path(self):
        return PROBE_DIR.parent / "preferences.json"

    def _load_preferences(self):
        path = self._preferences_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        lang = data.get("lang")
        if isinstance(lang, str) and lang in {code for code in LANGUAGES.values()}:
            self.lang = lang
        output_folder = data.get("output_folder")
        if isinstance(output_folder, str):
            self.output_folder.set(output_folder)

    def _save_preferences(self):
        path = self._preferences_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {"lang": self.lang, "language_set_by_user": True}
            output_folder = self.output_folder.get().strip()
            if output_folder:
                data["output_folder"] = output_folder
            path.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass

    def _load_brand_images(self):
        """Load brand image assets if present. Falls back silently when missing."""
        mark_path = RESOURCE_ROOT / "assets" / "imgs" / "brand_mark.png"
        icon_path = RESOURCE_ROOT / "assets" / "imgs" / "brand_icon.png"
        logo_path = RESOURCE_ROOT / "assets" / "imgs" / "brand_logo.png"
        if not (mark_path.exists() or icon_path.exists() or logo_path.exists()):
            return
        try:
            from PIL import Image, ImageTk
        except Exception:
            return

        def fit(path, max_w, max_h):
            try:
                img = Image.open(path).convert("RGBA")
            except Exception:
                return None
            img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(img)

        if mark_path.exists():
            self.brand_mark_photo = fit(mark_path, 64, 64)
        if icon_path.exists():
            try:
                icon_source = Image.open(icon_path).convert("RGBA")
                self.brand_window_icons = []
                for size in (16, 24, 32, 48, 64, 128, 256):
                    icon_img = icon_source.copy()
                    icon_img.thumbnail((size, size), Image.Resampling.LANCZOS)
                    self.brand_window_icons.append(ImageTk.PhotoImage(icon_img))
                if self.brand_window_icons:
                    self.brand_mark_icon = self.brand_window_icons[-1]
                    self.root.iconphoto(True, *self.brand_window_icons)
            except Exception:
                pass
        if logo_path.exists():
            self.brand_logo_photo = fit(logo_path, 390, 84)

    def _refresh_status_dot(self):
        dot = getattr(self, "status_dot", None)
        if dot is None:
            return
        value = (self.status.get() or "").lower()
        running_terms = (tr("en", "running").lower(), tr(self.lang, "running").lower(),
                         tr("en", "importing").lower(), tr(self.lang, "importing").lower(),
                         tr("en", "locating").lower(), tr(self.lang, "locating").lower())
        fail_terms = (tr("en", "failed").lower(), tr(self.lang, "failed").lower(),
                      tr("en", "stopped").lower(), tr(self.lang, "stopped").lower())
        if any(term and term in value for term in fail_terms):
            outer, inner = Theme.DANGER, Theme.DANGER_HOVER
        elif any(term and term in value for term in running_terms):
            outer, inner = Theme.ACCENT_DARK, Theme.ACCENT
        else:
            outer, inner = Theme.SUCCESS_DARK, Theme.SUCCESS
        try:
            ids = dot.find_all()
            if len(ids) >= 2:
                dot.itemconfigure(ids[0], fill=outer)
                dot.itemconfigure(ids[1], fill=inner)
        except Exception:
            pass

    def _on_status_changed(self):
        self._refresh_status_dot()
        if self.import_log_modal is not None:
            self.import_log_status.set(self.status.get())

    # Maps the .ini filename stem (lowercased, with prefix stripped) to an i18n key.
    PROFILE_NAME_KEYS = {
        "keemstar fast - extremely fast": "profile_keemstar",
        "fast - get'er'done": "profile_fast",
        "balanced - good quality and speed": "profile_balanced",
        "slow - conserve shapes": "profile_slow",
        "super slow - best quality": "profile_super_slow",
        "i hate my gpu": "profile_i_hate_my_gpu",
    }

    def _profile_key(self, setting):
        """Return the i18n base key for a built-in profile, or None for user-supplied."""
        try:
            stem = setting["path"].stem
        except Exception:
            return None
        stripped = re.sub(r"^[a-z0-9]+[.)]\s*", "", stem, flags=re.IGNORECASE).strip().lower()
        return self.PROFILE_NAME_KEYS.get(stripped)

    def _localized_profile_label(self, setting):
        """Return the dropdown label, translated when the profile is a known built-in."""
        key = self._profile_key(setting)
        if key:
            return f"{setting['index']}. {tr(self.lang, key)}"
        return setting["label"]

    def _localized_profile_description(self, setting):
        key = self._profile_key(setting)
        if key:
            return tr(self.lang, f"{key}_desc")
        return setting.get("description", "")

    def _refresh_profile_combo(self):
        if not self.settings:
            return
        previous_path = None
        current = self._selected_setting()
        if current:
            try:
                previous_path = current["path"].resolve()
            except OSError:
                previous_path = current.get("path")
        values = [self._localized_profile_label(item) for item in self.settings]
        if hasattr(self, "profile_combo"):
            self.profile_combo["values"] = values
        if hasattr(self, "region_profile_combo"):
            self.region_profile_combo["values"] = values
        new_selection = None
        if previous_path:
            for item in self.settings:
                try:
                    if item["path"].resolve() == previous_path:
                        new_selection = self._localized_profile_label(item)
                        break
                except OSError:
                    pass
        if new_selection is None and values:
            new_selection = values[min(2, len(values) - 1)]
        self.selected_profile.set(new_selection or "")
        if hasattr(self, "region_selected_profile") and not self.region_selected_profile.get():
            self.region_selected_profile.set(new_selection or "")
        self._update_setting_description()

    def _style_combobox_popdown(self, combo):
        """Keep native combobox behavior stable and block wheel selection."""
        # Block scroll wheel from changing the selected value (Windows/Mac + Linux)
        combo.bind("<MouseWheel>", lambda _e: "break", add="+")
        combo.bind("<Button-4>", lambda _e: "break", add="+")
        combo.bind("<Button-5>", lambda _e: "break", add="+")

    def _apply_combobox_popdown_style(self, combo):
        try:
            popdown = combo.tk.call("ttk::combobox::PopdownWindow", combo)
        except Exception:
            return
        listbox = f"{popdown}.f.l"
        try:
            combo.tk.call(
                listbox, "configure",
                "-background", Theme.INPUT,
                "-foreground", Theme.TEXT,
                "-selectbackground", Theme.ACCENT_DARK,
                "-selectforeground", Theme.TEXT_ON_ACCENT,
                "-disabledforeground", Theme.MUTED,
                "-borderwidth", 0,
                "-highlightthickness", 0,
                "-relief", "flat",
                "-activestyle", "none",
                "-font", (Theme.FONT_FAMILY, 10),
            )
        except Exception:
            pass
        # Style the popdown frame border + scrollbar
        try:
            combo.tk.call(popdown, "configure", "-background", Theme.BORDER)
            combo.tk.call(f"{popdown}.f", "configure",
                          "-background", Theme.BORDER,
                          "-borderwidth", 1, "-relief", "solid")
            combo.tk.call(f"{popdown}.f.sb", "configure",
                          "-background", Theme.PANEL_ALT,
                          "-troughcolor", Theme.PANEL,
                          "-activebackground", Theme.BUTTON_HOVER,
                          "-borderwidth", 0,
                          "-highlightthickness", 0)
        except Exception:
            pass

    def _section_title_text(self):
        return tr(self.lang, {
            "generate": "generate_tab",
            "import": "import_tab",
            "full_shape": "full_shape_tab",
            "region": "region_paint_tab",
            "tools": "tools_tab",
            "tutorial": "tutorial_tab",
        }.get(getattr(self, "current_section", "generate"), "generate_tab"))

    def _section_subtitle_text(self):
        return tr(self.lang, {
            "generate": "scroll_hint",
            "import": "step_import_hint",
            "full_shape": "full_shape_intro",
            "region": "region_step_selection",
            "tutorial": "subtitle",
        }.get(getattr(self, "current_section", "generate"), "subtitle"))

    def _refresh_section_header_wrap(self):
        if not all(hasattr(self, name) for name in ("section_title_block", "section_subtitle", "section_subtitle_wrap_state")):
            return
        self._apply_wrap_update(
            self.section_title_block,
            self.section_subtitle,
            self.section_subtitle_wrap_state,
            360,
            12,
        )

    def _select_section(self, key):
        if key not in self.section_frames:
            return
        if key != "generate":
            self._hide_quality_settings_modal()
            if hasattr(self, "profile_combo"):
                self.profile_combo._close_dropdown()
        if key != "import":
            self._hide_market_modal()
        for section_key, frame in self.section_frames.items():
            try:
                frame.pack_forget()
            except Exception:
                pass
        self.section_frames[key].pack(fill=BOTH, expand=True)
        self.current_section = key
        self._set_log_area_visible(key in ("full_shape", "region"))
        if self.log_area_visible:
            self._populate_scoped_log_widget(self.log, self._visible_log_scope())
            self._refresh_visible_log_progress()
        if hasattr(self, "section_title"):
            self.section_title.config(text=self._section_title_text())
        if hasattr(self, "section_subtitle"):
            self.section_subtitle.config(text=self._section_subtitle_text())
        self._refresh_section_header_wrap()
        if hasattr(self, "market_button"):
            if key == "import":
                self.market_button.pack(side=RIGHT, padx=(12, 8), ipady=5)
            else:
                self.market_button.pack_forget()
        for nav_key, item in self.nav_items.items():
            selected = nav_key == key
            container = item["container"]
            bar = item["bar"]
            body = item["body"]
            label = item["label"]
            icon = item["icon"]
            if selected:
                container.configure(bg=Theme.PANEL_ALT)
                body.configure(bg=Theme.PANEL_ALT)
                label.configure(bg=Theme.PANEL_ALT, fg=Theme.ACCENT)
                bar.configure(bg=Theme.ACCENT)
                icon.configure(bg=Theme.PANEL_ALT)
                item["set_icon"]("accent")
            else:
                container.configure(bg=Theme.PANEL)
                body.configure(bg=Theme.PANEL)
                label.configure(bg=Theme.PANEL, fg=Theme.MUTED)
                bar.configure(bg=Theme.PANEL)
                icon.configure(bg=Theme.PANEL)
                item["set_icon"]("muted")
        self._schedule_preview_refresh()

    # -------------------------------------------------------------------- topbar
    def _build_topbar(self, parent):
        topbar = Frame(parent, bg=Theme.BG)
        topbar.pack(fill=X, padx=24, pady=(20, 12))
        topbar.grid_columnconfigure(0, weight=1, minsize=360)
        topbar.grid_columnconfigure(1, weight=0)
        topbar.grid_columnconfigure(2, weight=0)

        title_block = Frame(topbar, bg=Theme.BG)
        self.section_title_block = title_block
        title_block.grid(row=0, column=0, sticky="ew")
        self.section_title = Label(
            title_block, text=self._section_title_text(),
            bg=Theme.BG, fg=Theme.TEXT, font=(Theme.FONT_FAMILY, 22, "bold"), anchor="w",
        )
        self.section_title.pack(anchor="w")
        self.section_subtitle = Label(
            title_block, text=self._section_subtitle_text(),
            bg=Theme.BG, fg=Theme.MUTED, font=(Theme.FONT_FAMILY, 10), anchor="w", justify=LEFT,
        )
        self.section_subtitle.pack(fill=X, anchor="w", pady=(2, 0))
        self.section_subtitle_wrap_state = {"job": None, "bucket": None}
        title_block.bind(
            "<Configure>",
            lambda event: self._schedule_wrap_update(
                title_block, self.section_subtitle, self.section_subtitle_wrap_state, 360, 12, event
            ),
            add="+",
        )
        self.section_subtitle.after_idle(
            lambda: self._apply_wrap_update(
                title_block, self.section_subtitle, self.section_subtitle_wrap_state, 360, 12
            )
        )

        # Horizontal brand logo sits between the title block and the status pill
        if self.brand_logo_photo is not None:
            logo_label = Label(topbar, image=self.brand_logo_photo, bg=Theme.BG, bd=0)
            logo_label.image = self.brand_logo_photo
            logo_label.grid(row=0, column=1, sticky="e", padx=(18, 18))

        right_block = Frame(topbar, bg=Theme.BG)
        right_block.grid(row=0, column=2, sticky="e")

        self.update_indicator = Label(
            right_block, text="", width=2, bg=Theme.BG, fg=Theme.WARN,
            font=(Theme.FONT_FAMILY, 11, "bold"), cursor="hand2",
        )
        self.update_indicator.pack(side=RIGHT, padx=(12, 0))
        self.update_indicator.bind("<Button-1>", self.show_update_status)

        status_pill = Frame(right_block, bg=Theme.PANEL_ALT, highlightthickness=1,
                            highlightbackground=Theme.BORDER)
        status_pill.pack(side=RIGHT)
        dot_canvas = Canvas(status_pill, width=10, height=10, bg=Theme.PANEL_ALT, highlightthickness=0)
        # Outer glow ring + inner dot
        dot_canvas.create_oval(0, 0, 10, 10, fill=Theme.SUCCESS_DARK, outline="")
        dot_canvas.create_oval(2, 2, 8, 8, fill=Theme.SUCCESS, outline="")
        self.status_dot = dot_canvas
        dot_canvas.pack(side=LEFT, padx=(14, 8), pady=8)
        Label(status_pill, textvariable=self.status, bg=Theme.PANEL_ALT, fg=Theme.TEXT,
              font=(Theme.FONT_FAMILY, 10, "bold")).pack(side=LEFT, padx=(0, 16), pady=8)

        # process picker row
        proc_row = Frame(parent, bg=Theme.BG)
        proc_row.pack(fill=X, padx=24, pady=(0, 6))
        self._label(proc_row, "process", bg=Theme.BG, fg=Theme.SUBTLE,
                    font=(Theme.FONT_FAMILY, 8, "bold")).pack(side=LEFT, padx=(0, 12))
        self.process_combo = ttk.Combobox(proc_row, textvariable=self.selected_pid,
                                          state="readonly", width=44)
        self.process_combo.pack(side=LEFT, padx=(0, 8))
        self._style_combobox_popdown(self.process_combo)
        self._button(proc_row, "refresh", self.refresh_processes).pack(side=LEFT)
        self.market_button = self._primary_button(
            proc_row, "market", self.open_market_modal,
            variant="accent", padx=18, pady=6,
            font=(Theme.FONT_FAMILY, 10, "bold"),
        )

    # ---------------------------------------------------------------------- card
    def _card(self, parent, title_key, step=None, side_pack=None, eyebrow=None, header_action=None):
        """Returns the content frame inside an elevated card with tinted header + hairline."""
        wrapper = Frame(parent, bg=Theme.BG)
        if side_pack:
            wrapper.pack(**side_pack)
        else:
            wrapper.pack(fill=X, pady=(0, 16))
        border = Frame(wrapper, bg=Theme.BORDER)
        border.pack(fill=BOTH, expand=True)
        card = Frame(border, bg=Theme.PANEL)
        card.pack(fill=BOTH, expand=True, padx=1, pady=1)

        # Tinted header
        header = Frame(card, bg=Theme.PANEL_HEADER)
        header.pack(fill=X)
        header_inner = Frame(header, bg=Theme.PANEL_HEADER)
        header_inner.pack(fill=X, padx=22, pady=14)

        if step is not None:
            badge_img = self._render_step_badge(step)
            if badge_img is not None:
                badge = Label(header_inner, image=badge_img, bg=Theme.PANEL_HEADER, bd=0)
                badge.image = badge_img
                badge.pack(side=LEFT, padx=(0, 12))
            else:
                badge_w = self._badge_width(step)
                badge = Canvas(header_inner, width=badge_w, height=24, bg=Theme.PANEL_HEADER, highlightthickness=0)
                badge.create_oval(0, 0, badge_w, 24, fill=Theme.ACCENT_DARK, outline="")
                badge.create_oval(2, 2, badge_w - 2, 22, fill=Theme.ACCENT, outline="")
                badge.create_text(badge_w / 2, 13, text=str(step), fill=Theme.TEXT_ON_ACCENT,
                                  font=(Theme.FONT_FAMILY, 11, "bold"))
                badge.pack(side=LEFT, padx=(0, 12))

        title_block = Frame(header_inner, bg=Theme.PANEL_HEADER)
        title_block.pack(side=LEFT, fill=X, expand=True)
        if eyebrow:
            Label(title_block, text=eyebrow.upper(), bg=Theme.PANEL_HEADER, fg=Theme.SUBTLE,
                  font=(Theme.FONT_FAMILY, 8, "bold"), anchor="w").pack(fill=X)
        title_label = Label(title_block, text=tr(self.lang, title_key), bg=Theme.PANEL_HEADER,
                            fg=Theme.TEXT, font=(Theme.FONT_FAMILY, 12, "bold"), anchor="w", justify=LEFT)
        title_label.pack(fill=X)
        self.translated.append((title_label, title_key, "text"))
        title_wrap_state = {"job": None, "bucket": None}
        title_block.bind(
            "<Configure>",
            lambda event: self._schedule_wrap_update(title_block, title_label, title_wrap_state, 180, 4, event),
            add="+",
        )
        title_label.after_idle(
            lambda: self._apply_wrap_update(title_block, title_label, title_wrap_state, 180, 4)
        )
        if header_action:
            action_key, action_command = header_action
            self._button(
                header_inner,
                action_key,
                action_command,
                bg=Theme.PANEL_HEADER,
                padx=14,
                pady=4,
                font=(Theme.FONT_FAMILY, 9, "bold"),
            ).pack(side=RIGHT, padx=(12, 0))

        # Hairline divider between header and body
        Frame(card, bg=Theme.BORDER, height=1).pack(fill=X)

        body = Frame(card, bg=Theme.PANEL)
        body.pack(fill=BOTH, expand=True, padx=22, pady=(16, 18))
        return body

    def _build_generate_tab(self):
        # two-column layout: left = workflow cards (scrollable), right = preview card
        columns = Frame(self.generate_tab, bg=Theme.BG)
        columns.pack(fill=BOTH, expand=True, pady=(0, 8))

        left_col = Frame(columns, bg=Theme.BG)
        left_col.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 18))

        right_col = Frame(columns, bg=Theme.BG, width=520)
        right_col.pack(side=LEFT, fill=BOTH, expand=True)
        right_col.pack_propagate(False)

        # ----- left column: scroll wrapper -----
        scroll_holder = Frame(left_col, bg=Theme.BG)
        scroll_holder.pack(fill=BOTH, expand=True)
        left_canvas = Canvas(scroll_holder, bg=Theme.BG, highlightthickness=0)
        left_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        scroll_inner = Frame(left_canvas, bg=Theme.BG)
        scroll_window = left_canvas.create_window((0, 0), window=scroll_inner, anchor="nw")
        scroll_resize_state = {"region_job": None, "width_job": None, "width_bucket": None}

        def _sync_scroll(_event=None):
            job = scroll_resize_state.get("region_job")
            if job is not None:
                try:
                    left_canvas.after_cancel(job)
                except Exception:
                    pass

            def apply_region():
                scroll_resize_state["region_job"] = None
                try:
                    left_canvas.configure(scrollregion=left_canvas.bbox("all"))
                except Exception:
                    pass

            scroll_resize_state["region_job"] = left_canvas.after(LAYOUT_RESIZE_DEBOUNCE_MS, apply_region)

        def _match_width(event):
            width = event.width
            bucket = self._layout_width_bucket(width)
            if bucket == scroll_resize_state.get("width_bucket"):
                return
            scroll_resize_state["width_bucket"] = bucket
            job = scroll_resize_state.get("width_job")
            if job is not None:
                try:
                    left_canvas.after_cancel(job)
                except Exception:
                    pass

            def apply_width(w=width):
                scroll_resize_state["width_job"] = None
                try:
                    left_canvas.itemconfigure(scroll_window, width=w)
                except Exception:
                    pass

            scroll_resize_state["width_job"] = left_canvas.after(LAYOUT_RESIZE_DEBOUNCE_MS, apply_width)

        def _on_wheel(event):
            left_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        scroll_inner.bind("<Configure>", _sync_scroll)
        left_canvas.bind("<Configure>", _match_width)
        scroll_holder.bind("<Enter>", lambda _e: left_canvas.bind_all("<MouseWheel>", _on_wheel))
        scroll_holder.bind("<Leave>", lambda _e: left_canvas.unbind_all("<MouseWheel>"))

        # ----- card 1: images -----
        card1 = self._card(scroll_inner, "generate_step_image", step=1)
        head_row = Frame(card1, bg=Theme.PANEL)
        head_row.pack(fill=X, pady=(0, 10))
        self._label(head_row, "images", font=(Theme.FONT_FAMILY, 10, "bold")).pack(side=LEFT)
        self._button(head_row, "add_images", self.add_images).pack(side=RIGHT)
        self._button(head_row, "remove_image", self.remove_selected_image).pack(side=RIGHT, padx=8)
        list_wrap = Frame(card1, bg=Theme.BORDER)
        list_wrap.pack(fill=X)
        list_inner = Frame(list_wrap, bg=Theme.INPUT)
        list_inner.pack(fill=X, padx=1, pady=1)
        self.image_list = Listbox(list_inner, height=3, borderwidth=0, highlightthickness=0)
        self.image_list.pack(fill=X, padx=10, pady=8)
        self.image_list.bind("<<ListboxSelect>>", self._preview_selected_image)
        self._bind_list_context_menu(self.image_list, "images")
        self._register_drop_target(self.image_list, self._drop_images)
        queue_status = Label(
            card1, textvariable=self.batch_queue_text, anchor="w", justify=LEFT,
            wraplength=560, fg=Theme.ACCENT_SOFT, bg=Theme.PANEL,
            font=(Theme.FONT_FAMILY, 9, "bold"),
        )
        queue_status.pack(fill=X, pady=(8, 0))

        quality_border = Frame(scroll_inner, bg=Theme.BORDER)
        quality_border.pack(fill=X, pady=(0, 12))
        quality_panel = Frame(quality_border, bg=Theme.PANEL)
        quality_panel.pack(fill=X, padx=1, pady=1)
        quality_inner = Frame(quality_panel, bg=Theme.PANEL)
        quality_inner.pack(fill=X, padx=22, pady=16)
        quality_inner.grid_columnconfigure(0, weight=1, minsize=260)
        quality_inner.grid_columnconfigure(1, weight=0, minsize=200)
        quality_copy = Frame(quality_inner, bg=Theme.PANEL)
        quality_copy.grid(row=0, column=0, sticky="nsew")
        self.setting_description = Label(
            quality_copy, text="", anchor="w", justify=LEFT,
            wraplength=360, fg=Theme.MUTED, bg=Theme.PANEL,
            font=(Theme.FONT_FAMILY, 9),
        )
        self.setting_description.pack(fill=X)
        quality_copy.bind(
            "<Configure>",
            lambda event: self.setting_description.configure(wraplength=max(220, event.width - 12)),
            add="+",
        )
        self.quality_layers_label = Label(
            quality_copy, text="", anchor="w", justify=LEFT,
            fg=Theme.ACCENT_SOFT, bg=Theme.PANEL,
            font=(Theme.FONT_FAMILY, 9, "bold"),
        )
        self.quality_layers_label.pack(fill=X, pady=(8, 0))
        quality_button_box = Frame(quality_inner, bg=Theme.PANEL, width=200, height=56)
        quality_button_box.grid(row=0, column=1, sticky="e", padx=(16, 0))
        quality_button_box.grid_propagate(False)
        self._primary_button(
            quality_button_box, "quality_settings", self.open_quality_settings_modal, variant="accent",
            padx=10, pady=6, font=(Theme.FONT_FAMILY, 10, "bold"),
            wraplength=178, justify="center"
        ).pack(fill=BOTH, expand=True)

        # ----- card 4: generate CTA -----
        cta_card = self._card(scroll_inner, "generate_step_run", step=2, side_pack={"fill": X, "pady": (0, 12)})
        self._responsive_label(cta_card, "generate_step_run_hint", anchor="w", justify=LEFT,
                               min_wrap=260, margin=8, fg=Theme.MUTED,
                               font=(Theme.FONT_FAMILY, 9)).pack(fill=X, pady=(0, 12))
        self.generate_button = self._primary_button(cta_card, "start_generate", self.start_generate)
        self.generate_button.pack(fill=X, ipady=4)
        actions = Frame(cta_card, bg=Theme.PANEL)
        actions.pack(fill=X, pady=(10, 0))
        self.stop_generate_button = self._danger_button(
            actions, "stop_generate", self.stop_generate, state="disabled",
            padx=10, pady=5, font=(Theme.FONT_FAMILY, 9), wraplength=150, justify="center",
        )
        self.stop_generate_button.pack(side=LEFT, fill=X, expand=True, ipady=3)
        self.generate_monitor_button = self._button(
            actions, "generation_monitor", self._show_generate_log_modal,
            padx=10, pady=5, font=(Theme.FONT_FAMILY, 9), wraplength=150, justify="center",
        )
        self.generate_monitor_button.pack(side=LEFT, fill=X, expand=True, padx=(10, 0), ipady=3)
        open_output_btn = self._button(
            actions, "open_output", self.open_output_folder,
            padx=10, pady=5, font=(Theme.FONT_FAMILY, 9), wraplength=150, justify="center",
        )
        open_output_btn.pack(side=LEFT, fill=X, expand=True, padx=(10, 0), ipady=3)

        # Inline live progress — mirrors the Generation monitor modal so the
        # user can see "layer X/Y | ETA …" without keeping the modal open.
        inline_progress = Frame(cta_card, bg=Theme.PANEL)
        inline_progress.pack(fill=X, pady=(10, 0))
        self.generate_inline_progress = ttk.Progressbar(
            inline_progress, mode="determinate", maximum=100,
            style="App.Horizontal.TProgressbar",
        )
        self.generate_inline_progress.pack(fill=X)
        self.generate_inline_progress["value"] = self.generate_progress_value
        inline_status_row = Frame(cta_card, bg=Theme.PANEL)
        inline_status_row.pack(fill=X, pady=(4, 0))
        Label(inline_status_row, textvariable=self.generate_modal_percent,
              bg=Theme.PANEL, fg=Theme.ACCENT_SOFT,
              font=(Theme.FONT_FAMILY, 9, "bold"), anchor="w").pack(side=LEFT)
        Label(inline_status_row, textvariable=self.generate_modal_status,
              bg=Theme.PANEL, fg=Theme.MUTED,
              font=(Theme.FONT_FAMILY, 9), anchor="w", justify="left").pack(
            side=LEFT, padx=(8, 0), fill=X, expand=True
        )

        # ----- right column: preview card -----
        preview_card = self._card(right_col, "preview", side_pack={"fill": BOTH, "expand": True})
        self._responsive_label(preview_card, "preview_accuracy_note", anchor="w", justify=LEFT,
                               min_wrap=220, margin=8, fg=Theme.WARN,
                               font=(Theme.FONT_FAMILY, 8)).pack(fill=X, pady=(0, 8))
        preview_inner = Frame(preview_card, bg=Theme.BORDER)
        preview_inner.pack(fill=BOTH, expand=True)
        self.preview_label = Label(
            preview_inner, text=tr(self.lang, "preview_hint"),
            bg=Theme.PREVIEW_BG, fg=Theme.MUTED,
            font=(Theme.FONT_FAMILY, 10),
        )
        self.preview_label.pack(fill=BOTH, expand=True, padx=1, pady=1)
        self.preview_label.bind("<Configure>", self._schedule_preview_refresh)

    def open_quality_settings_modal(self):
        top = self.quality_settings_modal
        try:
            if top is not None and top.winfo_exists():
                top.deiconify()
                self._ensure_window_in_taskbar(top)
                self._activate_modal(top)
                return
        except Exception:
            pass

        top = Toplevel(self.root)
        self.quality_settings_modal = top
        top.withdraw()
        top.title(tr(self.lang, "quality_settings"))
        top.configure(bg=Theme.BORDER)
        try:
            top.overrideredirect(True)
        except Exception:
            pass
        top.geometry("760x650")
        top.minsize(680, 560)
        # No transient(self.root): transient makes Windows treat the window as
        # a tool window and hides it from the taskbar. We register an explicit
        # taskbar entry below instead.
        top.protocol("WM_DELETE_WINDOW", self._hide_quality_settings_modal)

        shell = self._modal_shell(top)
        content_shell = Frame(shell, bg=Theme.BG)
        content_shell.pack(fill=BOTH, expand=True, padx=18, pady=16)

        header = Frame(content_shell, bg=Theme.BG)
        header.pack(fill=X, pady=(0, 12))
        title_label = self._label(header, "quality_settings", anchor="w",
                                  font=(Theme.FONT_FAMILY, 14, "bold"))
        title_label.pack(side=LEFT)
        self._button(header, "close", self._hide_quality_settings_modal).pack(side=RIGHT)

        drag_offset = {"x": 0, "y": 0}

        def start_drag(event):
            drag_offset["x"] = event.x_root - top.winfo_x()
            drag_offset["y"] = event.y_root - top.winfo_y()

        def drag_modal(event):
            top.geometry(f"+{event.x_root - drag_offset['x']}+{event.y_root - drag_offset['y']}")

        for drag_widget in (header, title_label):
            drag_widget.bind("<ButtonPress-1>", start_drag, add="+")
            drag_widget.bind("<B1-Motion>", drag_modal, add="+")
        self._bind_modal_drag(top, header)

        body_border = Frame(content_shell, bg=Theme.BORDER)
        body_border.pack(fill=BOTH, expand=True)
        body = Frame(body_border, bg=Theme.PANEL)
        body.pack(fill=BOTH, expand=True, padx=1, pady=1)

        content = Frame(body, bg=Theme.PANEL)
        content.pack(fill=BOTH, expand=True, padx=22, pady=18)

        q_row = Frame(content, bg=Theme.PANEL)
        q_row.pack(fill=X)
        self._label(q_row, "quality", font=(Theme.FONT_FAMILY, 10, "bold")).pack(side=LEFT)
        self.profile_combo = ThemedDropdown(
            q_row,
            values=[self._localized_profile_label(item) for item in self.settings],
            textvariable=self.selected_profile,
        )
        self.profile_combo.pack(side=LEFT, fill=X, expand=True, padx=(12, 0))
        self.profile_combo.bind("<<ComboboxSelected>>", self._update_setting_description)

        preset_actions = Frame(content, bg=Theme.PANEL)
        preset_actions.pack(fill=X, pady=(12, 10))
        self._button(preset_actions, "import_preset", self.import_preset).pack(side=LEFT)
        self._button(preset_actions, "open_preset_folder", self.open_preset_folder).pack(side=LEFT, padx=8)

        self.quality_modal_description = Label(
            content, text="", anchor="w", justify=LEFT,
            wraplength=680, fg=Theme.MUTED, bg=Theme.PANEL,
            font=(Theme.FONT_FAMILY, 9),
        )
        self.quality_modal_description.pack(fill=X, pady=(0, 14))

        Frame(content, bg=Theme.BORDER, height=1).pack(fill=X, pady=(0, 14))
        self._label(content, "custom_panel_title", anchor="w",
                    font=(Theme.FONT_FAMILY, 12, "bold")).pack(fill=X, pady=(0, 8))
        self._label(content, "custom_panel_hint", anchor="w", justify=LEFT,
                    wraplength=680, fg=Theme.MUTED, font=(Theme.FONT_FAMILY, 9)).pack(fill=X, pady=(0, 12))

        custom_toggle = Checkbutton(
            content, text=tr(self.lang, "custom_settings"),
            variable=self.use_custom_settings, onvalue="1", offvalue="0",
            command=self._sync_custom_state,
            font=(Theme.FONT_FAMILY, 10, "bold"),
        )
        custom_toggle.pack(anchor="w", pady=(0, 12))
        self.translated.append((custom_toggle, "custom_settings", "text"))

        custom_grid = Frame(content, bg=Theme.PANEL)
        custom_grid.pack(fill=X)
        self.custom_fields = []
        custom_specs = [
            ("custom_layers", self.custom_stop_at),
            ("custom_resolution", self.custom_max_resolution),
            ("custom_random", self.custom_random_samples),
            ("custom_mutated", self.custom_mutated_samples),
            ("custom_save_at", self.custom_save_at),
        ]
        for row_index, (key, variable) in enumerate(custom_specs):
            label = self._label(custom_grid, key, anchor="w", fg=Theme.MUTED,
                                font=(Theme.FONT_FAMILY, 9))
            label.grid(row=row_index, column=0, sticky="w", pady=5, padx=(0, 14))
            entry = Entry(custom_grid, textvariable=variable)
            entry.grid(row=row_index, column=1, sticky="ew", pady=5, ipady=4)
            self.custom_fields.append(entry)
        custom_grid.columnconfigure(1, weight=1)
        preprocess_widget = self._field(
            custom_grid, "preprocess_mode", self.custom_preprocess_mode,
            row=len(custom_specs), values=["none", "luma_band"], readonly=True,
        )
        self.custom_fields.append(preprocess_widget)

        custom_actions = Frame(content, bg=Theme.PANEL)
        custom_actions.pack(fill=X, pady=(14, 0))
        self._button(custom_actions, "save_custom_preset", self.save_custom_preset).pack(side=LEFT)

        self._apply_dark_theme_recursive(top)
        self._refresh_profile_combo()
        self._sync_custom_state()
        self._update_setting_description()
        self._center_toplevel(top, 760, 650)
        top.deiconify()
        self._ensure_window_in_taskbar(top)
        self._activate_modal(top)
        top.focus_force()

    def _activate_modal(self, top):
        self.active_modal = top
        self._raise_modal(top)
        if os.name == "nt":
            try:
                self.root.attributes("-disabled", True)
            except Exception:
                pass
        try:
            top.grab_set()
        except Exception:
            pass

    def _raise_modal(self, top):
        try:
            top.lift()
            top.focus_force()
        except Exception:
            pass
        try:
            top.attributes("-topmost", True)
            top.after(250, lambda: self._clear_topmost(top))
        except Exception:
            pass

    def _clear_topmost(self, top):
        try:
            if top is not None and top.winfo_exists():
                top.attributes("-topmost", False)
        except Exception:
            pass

    def _modal_shell(self, top):
        outer = Frame(top, bg=Theme.ACCENT, highlightthickness=1, highlightbackground=Theme.ACCENT_SOFT)
        outer.pack(fill=BOTH, expand=True)
        inner = Frame(outer, bg=Theme.BG)
        inner.pack(fill=BOTH, expand=True, padx=2, pady=2)
        shell = Frame(inner, bg=Theme.BG)
        shell.pack(fill=BOTH, expand=True, padx=1, pady=1)
        return shell

    def _on_root_activated(self, event):
        """If the user re-activates the main window (clicked taskbar icon,
        alt-tabbed back, etc.) while a modal is open, raise the modal so it
        doesn't get stuck behind the root and lock the UI."""
        # Only react when the event is on the root itself, not bubbling from a child.
        if event.widget is not self.root:
            return
        top = getattr(self, "active_modal", None)
        if top is None:
            return
        try:
            if not top.winfo_exists():
                return
        except Exception:
            return
        try:
            top.lift()
            top.focus_force()
        except Exception:
            pass

    def _deactivate_modal(self, top=None):
        try:
            if top is not None:
                top.grab_release()
        except Exception:
            pass
        if top is None or self.active_modal is top:
            if os.name == "nt":
                try:
                    self.root.attributes("-disabled", False)
                except Exception:
                    pass
            self.active_modal = None

    def _bind_modal_drag(self, top, *handles):
        drag_offset = {"x": 0, "y": 0}

        def start_drag(event):
            drag_offset["x"] = event.x_root - top.winfo_x()
            drag_offset["y"] = event.y_root - top.winfo_y()

        def drag_modal(event):
            top.geometry(f"+{event.x_root - drag_offset['x']}+{event.y_root - drag_offset['y']}")

        def bind_recursive(widget):
            if isinstance(widget, (Button, Entry, Text, Listbox, Checkbutton, ThemedDropdown)):
                return
            widget.bind("<ButtonPress-1>", start_drag, add="+")
            widget.bind("<B1-Motion>", drag_modal, add="+")
            for child in widget.winfo_children():
                bind_recursive(child)

        for handle in handles:
            bind_recursive(handle)

    def _center_toplevel(self, top, width, height):
        try:
            self.root.update_idletasks()
            root_x = self.root.winfo_rootx()
            root_y = self.root.winfo_rooty()
            root_w = self.root.winfo_width()
            root_h = self.root.winfo_height()
            x = root_x + max(0, (root_w - width) // 2)
            y = root_y + max(0, (root_h - height) // 2)
            top.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            top.geometry(f"{width}x{height}")

    def _hide_quality_settings_modal(self):
        try:
            if hasattr(self, "profile_combo"):
                self.profile_combo._close_dropdown()
        except Exception:
            pass
        top = self.quality_settings_modal
        if top is None:
            return
        try:
            if top.winfo_exists():
                self._deactivate_modal(top)
                top.withdraw()
        except Exception:
            self._deactivate_modal(top)
            self.quality_settings_modal = None
            self.quality_modal_description = None
        # Re-activate root so the next input lands cleanly (mirrors the alert).
        try:
            self.root.lift()
            self.root.focus_force()
            self.root.update_idletasks()
        except Exception:
            pass

    def _build_region_paint_tab(self):
        """Build the Region Paint tab with the app's native card system."""
        columns = Frame(self.region_paint_tab, bg=Theme.BG)
        columns.pack(fill=BOTH, expand=True, pady=(0, 8))

        left = Frame(columns, bg=Theme.BG, width=430)
        left.pack(side=LEFT, fill=BOTH, padx=(0, 18))
        left.pack_propagate(False)

        right = Frame(columns, bg=Theme.BG)
        right.pack(side=LEFT, fill=BOTH, expand=True)

        left_footer = Frame(left, bg=Theme.BG)
        left_footer.pack(side=BOTTOM, fill=X)

        left_scroll = Canvas(left, bg=Theme.BG, highlightthickness=0)
        left_scroll.pack(side=TOP, fill=BOTH, expand=True)
        left_inner = Frame(left_scroll, bg=Theme.BG)
        left_window = left_scroll.create_window((0, 0), window=left_inner, anchor="nw")
        scroll_state = {"region_job": None, "width_job": None, "width_bucket": None}

        def _sync_region_scroll(_event=None):
            job = scroll_state.get("region_job")
            if job is not None:
                try:
                    left_scroll.after_cancel(job)
                except Exception:
                    pass

            def apply_region():
                scroll_state["region_job"] = None
                try:
                    left_scroll.configure(scrollregion=left_scroll.bbox("all"))
                except Exception:
                    pass

            scroll_state["region_job"] = left_scroll.after(LAYOUT_RESIZE_DEBOUNCE_MS, apply_region)

        def _match_region_width(event):
            width = max(1, int(event.width))
            bucket = self._layout_width_bucket(width)
            if bucket == scroll_state.get("width_bucket"):
                return
            scroll_state["width_bucket"] = bucket
            job = scroll_state.get("width_job")
            if job is not None:
                try:
                    left_scroll.after_cancel(job)
                except Exception:
                    pass

            def apply_width(w=width):
                scroll_state["width_job"] = None
                try:
                    left_scroll.itemconfigure(left_window, width=w)
                except Exception:
                    pass

            scroll_state["width_job"] = left_scroll.after(LAYOUT_RESIZE_DEBOUNCE_MS, apply_width)

        def _region_wheel(event):
            left_scroll.yview_scroll(int(-1 * (event.delta / 120)), "units")

        left_inner.bind("<Configure>", _sync_region_scroll)
        left_scroll.bind("<Configure>", _match_region_width)
        left_scroll.bind("<Enter>", lambda _e: left_scroll.bind_all("<MouseWheel>", _region_wheel))
        left_scroll.bind("<Leave>", lambda _e: left_scroll.unbind_all("<MouseWheel>"))

        setup = self._card(left_inner, "region_setup_title", step=1, side_pack={"fill": X, "pady": (0, 12)})
        row = Frame(setup, bg=Theme.PANEL)
        row.pack(fill=X)
        self._label(row, "images", font=(Theme.FONT_FAMILY, 10, "bold")).pack(side=LEFT)
        self._button(row, "add_images", self.region_add_image).pack(side=RIGHT)
        self.region_image_label = Label(
            setup, textvariable=self.region_image_label_var, anchor="w", justify=LEFT,
            bg=Theme.PANEL, fg=Theme.MUTED, font=(Theme.FONT_FAMILY, 9),
        )
        self.region_image_label.pack(fill=X, pady=(10, 12))

        profile_row = Frame(setup, bg=Theme.PANEL)
        profile_row.pack(fill=X)
        self._label(profile_row, "quality", font=(Theme.FONT_FAMILY, 10, "bold")).pack(side=LEFT, padx=(0, 10))
        self.region_profile_combo = ThemedDropdown(
            profile_row,
            values=[self._localized_profile_label(item) for item in self.settings],
            textvariable=self.region_selected_profile,
            width=31,
        )
        self.region_profile_combo.pack(side=LEFT, fill=X, expand=True)
        self.region_profile_combo.bind("<<ComboboxSelected>>", self._region_update_profile_description)
        if self.settings:
            self.region_selected_profile.set(self._localized_profile_label(self.settings[min(2, len(self.settings) - 1)]))
        self.region_profile_description = Label(
            setup, text="", anchor="w", justify=LEFT, wraplength=360,
            bg=Theme.PANEL, fg=Theme.SUBTLE, font=(Theme.FONT_FAMILY, 9),
        )
        self.region_profile_description.pack(fill=X, pady=(8, 0))

        budget = self._card(left_inner, "region_budget_title", step=2, side_pack={"fill": X, "pady": (0, 12)})
        budget_grid = Frame(budget, bg=Theme.PANEL)
        budget_grid.pack(fill=X)
        budget_grid.columnconfigure(1, weight=1)
        for ri, (key, var) in enumerate([
            ("region_total_layers", self.region_total_var),
            ("region_first_pass_layers", self.region_first_var),
            ("region_region_layers", self.region_layers_var),
        ]):
            self._label(
                budget_grid, key, anchor="w", font=(Theme.FONT_FAMILY, 9, "bold"), fg=Theme.MUTED,
            ).grid(row=ri, column=0, sticky="w", pady=4, padx=(0, 12))
            Entry(
                budget_grid, textvariable=var, width=10, justify="right",
                font=(Theme.FONT_FAMILY, 10, "bold"),
            ).grid(row=ri, column=1, sticky="e", pady=4, ipady=3)
        remaining = Frame(budget, bg=Theme.PANEL_ALT, highlightthickness=1, highlightbackground=Theme.BORDER)
        remaining.pack(fill=X, pady=(12, 0))
        self._label(
            remaining, "region_remaining", bg=Theme.PANEL_ALT, fg=Theme.MUTED,
            font=(Theme.FONT_FAMILY, 9, "bold"),
        ).pack(side=LEFT, padx=12, pady=8)
        Label(
            remaining, textvariable=self.region_remaining_var, fg=Theme.ACCENT_SOFT,
            bg=Theme.PANEL_ALT, font=(Theme.FONT_FAMILY, 12, "bold"),
        ).pack(side=RIGHT, padx=12, pady=8)

        selection = self._card(left_inner, "region_selection_title", step=3, side_pack={"fill": X, "pady": (0, 12)})
        tool_row = Frame(selection, bg=Theme.PANEL)
        tool_row.pack(fill=X)
        self.region_tool_buttons = {}
        for key, value in (("region_tool_rect", "rect"), ("region_tool_ellipse", "ellipse")):
            btn = self._button(tool_row, key, lambda v=value: self._region_set_tool(v))
            btn.pack(side=LEFT, fill=X, expand=True, padx=(0, 8 if value == "rect" else 0))
            self.region_tool_buttons[value] = btn
        self._button(tool_row, "region_tool_clear", self._region_clear_mask).pack(side=LEFT, padx=(10, 0))

        rot_row = Frame(selection, bg=Theme.PANEL)
        rot_row.pack(fill=X, pady=(12, 0))
        rot_label = Label(
            rot_row, text=tr(self.lang, "region_rotation"), bg=Theme.PANEL,
            fg=Theme.MUTED, font=(Theme.FONT_FAMILY, 9, "bold"),
        )
        rot_label.pack(side=LEFT, padx=(0, 10))
        self.translated.append((rot_label, "region_rotation", "text"))
        self.region_rotation_slider = ttk.Scale(
            rot_row, from_=-180, to=180, orient="horizontal",
            variable=self.region_rotation_var, state="disabled",
            command=self._region_on_rotation_changed,
        )
        self.region_rotation_slider.pack(side=LEFT, fill=X, expand=True)
        self.region_rotation_label = Entry(
            rot_row, textvariable=self.region_rotation_display,
            width=6, justify="right", state="disabled",
            font=(Theme.FONT_FAMILY, 10, "bold"),
        )
        self.region_rotation_label.pack(side=LEFT, padx=(10, 0), ipady=3)
        self.region_rotation_label.bind("<Return>", self._region_rotation_entry_apply)
        self.region_rotation_label.bind("<FocusOut>", self._region_rotation_entry_apply)

        history = self._card(left_inner, "region_pass_history", side_pack={"fill": X, "pady": (0, 12)})
        list_wrap = Frame(history, bg=Theme.BORDER)
        list_wrap.pack(fill=X)
        list_inner = Frame(list_wrap, bg=Theme.INPUT)
        list_inner.pack(fill=X, padx=1, pady=1)
        self.region_pass_list = Listbox(list_inner, height=3, borderwidth=0, highlightthickness=0)
        self.region_pass_list.pack(fill=X, padx=10, pady=8)

        actions_card = self._card(left_footer, "region_step_actions", step=4, side_pack={"fill": X, "pady": (12, 0)})
        self.region_first_pass_btn = self._primary_button(
            actions_card, "region_start_first_pass", self._region_start_first_pass, variant="accent"
        )
        self.region_first_pass_btn.pack(fill=X, ipady=3)
        action_row = Frame(actions_card, bg=Theme.PANEL)
        action_row.pack(fill=X, pady=(10, 0))
        self.region_paint_btn = self._button(action_row, "region_paint_region", self._region_start_pass, state="disabled")
        self.region_paint_btn.pack(side=LEFT, fill=X, expand=True)
        self.region_stop_btn = self._button(action_row, "region_stop", self._region_stop, state="disabled")
        self.region_stop_btn.pack(side=LEFT, padx=(10, 0))

        status_row = Frame(actions_card, bg=Theme.PANEL)
        status_row.pack(fill=X, pady=(12, 0))
        Label(
            status_row, textvariable=self.region_status, fg=Theme.TEXT,
            bg=Theme.PANEL, anchor="w", font=(Theme.FONT_FAMILY, 9, "bold"),
        ).pack(side=LEFT)
        Label(
            status_row, textvariable=self.region_progress, fg=Theme.ACCENT_SOFT,
            bg=Theme.PANEL, anchor="e", font=(Theme.FONT_FAMILY, 9),
        ).pack(side=RIGHT)

        result_row = Frame(actions_card, bg=Theme.PANEL)
        result_row.pack(fill=X, pady=(10, 0))
        self.region_open_folder_btn = self._button(
            result_row, "region_open_result_folder", self._region_open_result_folder, state="disabled"
        )
        self.region_open_folder_btn.pack(side=LEFT, fill=X, expand=True)
        self.region_save_json_btn = self._button(
            result_row, "region_save_result_json", self._region_save_result_json, state="disabled"
        )
        self.region_save_json_btn.pack(side=LEFT, fill=X, expand=True, padx=(10, 0))

        preview_card = self._card(right, "region_canvas_title", side_pack={"fill": BOTH, "expand": True})
        header = Frame(preview_card, bg=Theme.PANEL)
        header.pack(fill=X, pady=(0, 10))
        self._label(header, "region_original_label", anchor="w", fg=Theme.MUTED,
                    font=(Theme.FONT_FAMILY, 9, "bold")).pack(side=LEFT, fill=X, expand=True)
        tab_row = Frame(header, bg=Theme.PANEL)
        tab_row.pack(side=LEFT)
        self.region_tab_preview_btn = Label(
            tab_row, text=tr(self.lang, "region_preview_tab"), anchor="center",
            bg=Theme.ACCENT_DARK, fg=Theme.TEXT_ON_ACCENT, padx=14, pady=5,
            font=(Theme.FONT_FAMILY, 9, "bold"), cursor="hand2",
        )
        self.region_tab_preview_btn._keep_custom_theme = True
        self.region_tab_preview_btn.pack(side=LEFT)
        self.region_tab_preview_btn.bind("<Button-1>", lambda _e: self._region_switch_tab("preview"))
        self.region_tab_heatmap_btn = Label(
            tab_row, text=tr(self.lang, "region_heatmap_tab"), anchor="center",
            bg=Theme.BUTTON, fg=Theme.MUTED, padx=14, pady=5,
            font=(Theme.FONT_FAMILY, 9, "bold"), cursor="arrow",
        )
        self.region_tab_heatmap_btn._keep_custom_theme = True
        self.region_tab_heatmap_btn.pack(side=LEFT, padx=(6, 0))
        self.region_tab_heatmap_btn.bind("<Button-1>", lambda _e: self._region_switch_tab("heatmap"))
        self.translated.append((self.region_tab_preview_btn, "region_preview_tab", "text"))
        self.translated.append((self.region_tab_heatmap_btn, "region_heatmap_tab", "text"))

        canvas_row = Frame(preview_card, bg=Theme.PANEL)
        canvas_row.pack(fill=BOTH, expand=True)
        canvas_row.columnconfigure(0, weight=1, uniform="region_preview")
        canvas_row.columnconfigure(1, weight=1, uniform="region_preview")
        canvas_row.rowconfigure(0, weight=1)

        left_viewport = Frame(canvas_row, bg=Theme.BORDER_STRONG)
        left_viewport.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left_canvas_inner = Frame(left_viewport, bg=Theme.PREVIEW_BG)
        left_canvas_inner.pack(fill=BOTH, expand=True, padx=1, pady=1)
        self.region_canvas_left = Canvas(
            left_canvas_inner, bg=Theme.PREVIEW_BG, highlightthickness=0, cursor="cross"
        )
        self.region_canvas_left._keep_custom_theme = True
        self.region_canvas_left.pack(fill=BOTH, expand=True)

        right_display = Frame(canvas_row, bg=Theme.PANEL)
        right_display.grid(row=0, column=1, sticky="nsew")
        self.region_right_display = right_display
        right_display.columnconfigure(0, weight=1)
        right_display.rowconfigure(0, weight=1)

        right_viewport = Frame(right_display, bg=Theme.BORDER_STRONG)
        right_viewport.grid(row=0, column=0, sticky="nsew")
        right_canvas_inner = Frame(right_viewport, bg=Theme.PREVIEW_BG)
        right_canvas_inner.pack(fill=BOTH, expand=True, padx=1, pady=1)
        self.region_canvas_right = Canvas(
            right_canvas_inner, bg=Theme.PREVIEW_BG, highlightthickness=0
        )
        self.region_canvas_right._keep_custom_theme = True
        self.region_canvas_right.pack(fill=BOTH, expand=True)
        self.region_heatmap_bar_canvas = Canvas(
            right_display, bg=Theme.PANEL, highlightthickness=0
        )
        self.region_heatmap_bar_canvas._keep_custom_theme = True
        self.region_heatmap_bar_canvas.place_forget()
        # Backwards compatibility alias — selection tools use left canvas
        self.region_canvas = self.region_canvas_left

        # Canvas bindings for selection tools
        self.region_canvas.bind("<Button-1>", self._region_canvas_press)
        self.region_canvas.bind("<B1-Motion>", self._region_canvas_drag)
        self.region_canvas.bind("<ButtonRelease-1>", self._region_canvas_release)
        self.region_canvas.bind("<Double-Button-1>", self._region_canvas_double_click)
        self.region_canvas.bind("<Motion>", self._region_canvas_motion)
        self.region_canvas.bind("<Configure>", self._region_canvas_configure)
        self.region_canvas_right.bind("<Configure>", self._region_canvas_configure)
        # Scroll wheel to rotate selected shape
        self.region_canvas.bind("<MouseWheel>", self._region_on_mousewheel)
        self.region_canvas.bind("<Button-4>", self._region_on_mousewheel)
        self.region_canvas.bind("<Button-5>", self._region_on_mousewheel)

        self._region_update_image_label()
        self._region_set_tool(self.region_tool.get())
        if self.settings:
            self._region_update_profile_description()  # sync stopAt → total budget
        self._region_update_button_states()

    # ==================================================================
    # Region Paint — Canvas configure (persist preview on resize)
    # ==================================================================

    def _region_canvas_configure(self, _event=None):
        """Redraw the image/preview when canvases resize."""
        self._region_cached_display_pil = None  # invalidate display cache on resize
        self._region_cached_display_size = None
        if self.region_workflow_running:
            return
        # Redraw original image on left canvas if an image is selected
        if self.region_images:
            if getattr(self, "_region_configure_image_job", None):
                self.region_canvas_left.after_cancel(self._region_configure_image_job)
            self._region_configure_image_job = self.region_canvas_left.after(
                200, lambda: self._region_display_image(self.region_images[0])
            )
        # Redraw right canvas based on active tab
        if self._region_right_tab == "heatmap":
            heatmap = getattr(self, "_region_heatmap_showing", None)
            if heatmap and Path(heatmap).exists():
                if getattr(self, "_region_configure_heatmap_job", None):
                    self.region_canvas_right.after_cancel(self._region_configure_heatmap_job)
                self._region_configure_heatmap_job = self.region_canvas_right.after(
                    200, lambda: self._region_display_heatmap(Path(heatmap))
                )
        else:
            preview = getattr(self, "_region_preview_showing", None)
            if preview and Path(preview).exists():
                if getattr(self, "_region_configure_preview_job", None):
                    self.region_canvas_right.after_cancel(self._region_configure_preview_job)
                self._region_configure_preview_job = self.region_canvas_right.after(
                    200, lambda: self._region_display_preview(Path(preview))
                )

    # ==================================================================
    # Region Paint — Image management
    # ==================================================================

    def region_add_image(self):
        paths = filedialog.askopenfilenames(
            title=tr(self.lang, "add_images"),
            filetypes=[(tr(self.lang, "image_files"), "*.png *.jpg *.jpeg *.bmp"), (tr(self.lang, "all_files"), "*.*")],
        )
        if not paths:
            return
        # Only keep the last selected image — replace any existing image.
        pp = Path(paths[-1])
        if not pp.exists():
            return
        self.region_images.clear()
        self.region_images.append(pp)
        self._region_clear_mask()
        self._region_update_image_label()
        self._region_display_image(pp)

    def _region_update_image_label(self):
        """Update the image label to show the current image path."""
        if self.region_images:
            self.region_image_label_var.set(self.region_images[0].name)
        else:
            self.region_image_label_var.set(tr(self.lang, "region_no_image_loaded"))
        self._region_update_button_states()

    def _region_display_image(self, image_path: Path):
        """Load and display an image on the LEFT region canvas."""
        try:
            from PIL import Image, ImageTk
            # Cache the full-res image so redraws avoid disk I/O.
            if self._region_cached_pil is None or self._region_cached_pil_path != image_path:
                self._region_cached_pil = Image.open(image_path).convert("RGBA")
                self._region_cached_pil_path = image_path
                self._region_cached_display_pil = None  # invalidate display cache
                self._region_cached_display_size = None
            img = self._region_cached_pil.copy()
            cw = self.region_canvas_left.winfo_width() or 300
            ch = self.region_canvas_left.winfo_height() or 500
            new_w, new_h = self._region_shared_display_size(img.width, img.height)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            offset_x = max(2, int((cw - new_w) / 2))
            offset_y = max(2, int((ch - new_h) / 2))
            self._region_canvas_image_offset = (offset_x, offset_y)
            self.region_canvas_image_ref = ImageTk.PhotoImage(img)
            self.region_canvas_left.delete("all")
            self.region_canvas_left.create_image(offset_x, offset_y, anchor="nw", image=self.region_canvas_image_ref)
            self._region_draw_image_frame(self.region_canvas_left, offset_x, offset_y, new_w, new_h)
            self._region_redraw_overlay()
        except Exception as e:
            self.log_line(tr(self.lang, "region_image_load_failed").format(error=e), scope="region")

    # ==================================================================
    # Region Paint — Canvas mouse handlers
    # ==================================================================

    def _region_set_tool(self, tool: str):
        self.region_tool.set(tool)
        self.region_canvas.configure(cursor="cross")
        for value, button in getattr(self, "region_tool_buttons", {}).items():
            try:
                if value == tool:
                    button.configure(bg=Theme.ACCENT_DARK, fg=Theme.TEXT_ON_ACCENT, highlightbackground=Theme.ACCENT)
                else:
                    button.configure(bg=Theme.BUTTON, fg=Theme.TEXT, highlightbackground=Theme.BORDER)
            except Exception:
                pass

    def _region_get_canvas_scale(self) -> float:
        """Compute scale factor: canvas-display-pixels / working-pixels."""
        if not self.region_images:
            return 1.0
        try:
            if self._region_cached_pil is not None:
                w, h = self._region_cached_pil.size
            else:
                from PIL import Image
                img = Image.open(self.region_images[0])
                w, h = img.size
            if self._region_cached_display_size:
                display_w, display_h = self._region_cached_display_size
            else:
                display_w, display_h = self._region_shared_display_size(w, h)
            ratio = min(display_w / max(1, w), display_h / max(1, h))
            return ratio  # canvas_pixels / working_pixels
        except Exception:
            return 1.0

    def _region_shared_display_size(self, image_w: int, image_h: int, reserve_right: int = 0) -> tuple[int, int]:
        """Use one shared render size so original, preview, and heatmap match."""
        image_w = max(1, int(image_w))
        image_h = max(1, int(image_h))
        left_w = self.region_canvas_left.winfo_width() if hasattr(self, "region_canvas_left") else 0
        left_h = self.region_canvas_left.winfo_height() if hasattr(self, "region_canvas_left") else 0
        right_w = self.region_canvas_right.winfo_width() if hasattr(self, "region_canvas_right") else left_w
        right_h = self.region_canvas_right.winfo_height() if hasattr(self, "region_canvas_right") else left_h
        available_w = min(
            max(1, (left_w or 300) - 4),
            max(1, (right_w or left_w or 300) - 4 - max(0, int(reserve_right))),
        )
        available_h = min(max(1, (left_h or 500) - 4), max(1, (right_h or left_h or 500) - 4))
        ratio = min(
            available_w / image_w,
            available_h / image_h,
            600 / max(image_w, image_h),
        )
        return (max(1, int(image_w * ratio)), max(1, int(image_h * ratio)))

    def _region_image_bounds(self) -> tuple[float, float, float, float] | None:
        """Current image rectangle in left-canvas coordinates."""
        if not self.region_images:
            return None
        try:
            display_size = getattr(self, "_region_cached_display_size", None)
            if not display_size:
                if self._region_cached_pil is not None:
                    w, h = self._region_cached_pil.size
                else:
                    from PIL import Image
                    img = Image.open(self.region_images[0])
                    w, h = img.size
                cw = self.region_canvas.winfo_width() or 600
                ch = self.region_canvas.winfo_height() or 500
                display_size = self._region_shared_display_size(w, h)
            offset_x, offset_y = getattr(self, "_region_canvas_image_offset", (2, 2))
            display_w, display_h = display_size
            return (float(offset_x), float(offset_y), float(offset_x + display_w), float(offset_y + display_h))
        except Exception:
            return None

    def _region_point_in_image_bounds(self, x: float, y: float) -> bool:
        bounds = self._region_image_bounds()
        if not bounds:
            return False
        left, top, right, bottom = bounds
        return left <= x <= right and top <= y <= bottom

    def _region_clamp_canvas_point(self, x: float, y: float) -> tuple[float, float]:
        bounds = self._region_image_bounds()
        if not bounds:
            return x, y
        left, top, right, bottom = bounds
        return (max(left, min(float(x), right)), max(top, min(float(y), bottom)))

    def _region_clamp_bbox_to_image(self, x1: float, y1: float, x2: float, y2: float) -> list[float]:
        bounds = self._region_image_bounds()
        if not bounds:
            return [x1, y1, x2, y2]
        left, top, right, bottom = bounds
        x1, x2 = sorted((max(left, min(float(x1), right)), max(left, min(float(x2), right))))
        y1, y2 = sorted((max(top, min(float(y1), bottom)), max(top, min(float(y2), bottom))))
        min_size = 6.0
        if x2 - x1 < min_size:
            mid = (x1 + x2) / 2.0
            x1 = max(left, min(mid - min_size / 2.0, right - min_size))
            x2 = min(right, x1 + min_size)
        if y2 - y1 < min_size:
            mid = (y1 + y2) / 2.0
            y1 = max(top, min(mid - min_size / 2.0, bottom - min_size))
            y2 = min(bottom, y1 + min_size)
        return [x1, y1, x2, y2]

    def _region_constrain_bbox_to_image(self, x1: float, y1: float, x2: float, y2: float) -> list[float]:
        bounds = self._region_image_bounds()
        if not bounds:
            return [x1, y1, x2, y2]
        left, top, right, bottom = bounds
        x1, x2 = sorted((float(x1), float(x2)))
        y1, y2 = sorted((float(y1), float(y2)))
        width = x2 - x1
        height = y2 - y1
        if width >= right - left:
            x1, x2 = left, right
        elif x1 < left:
            x2 += left - x1
            x1 = left
        elif x2 > right:
            x1 -= x2 - right
            x2 = right
        if height >= bottom - top:
            y1, y2 = top, bottom
        elif y1 < top:
            y2 += top - y1
            y1 = top
        elif y2 > bottom:
            y1 -= y2 - bottom
            y2 = bottom
        return [x1, y1, x2, y2]

    def _region_draw_image_frame(self, canvas: Canvas, x: int, y: int, width: int, height: int):
        """Draw a restrained viewport frame around a rendered preview image."""
        right = x + width
        bottom = y + height
        canvas.create_rectangle(
            x - 1, y - 1, right + 1, bottom + 1,
            outline=Theme.BORDER_STRONG, width=1, tags=("image_viewport_frame",),
        )
        canvas.create_rectangle(
            x, y, right, bottom,
            outline=Theme.PANEL_ALT, width=1, tags=("image_viewport_frame",),
        )

    def _region_hit_test(self, x: float, y: float) -> int | None:
        """Return the index of the shape at canvas point (x, y), or None.
        Tests shapes in reverse order so the top-most shape wins."""
        import math
        for i in range(len(self.region_shapes) - 1, -1, -1):
            shape = self.region_shapes[i]
            tool = shape.get("tool", "")
            coords = shape.get("coords", [])
            if tool not in ("rect", "ellipse") or len(coords) < 4:
                continue
            cx = (coords[0] + coords[2]) / 2.0
            cy = (coords[1] + coords[3]) / 2.0
            hw = abs(coords[2] - coords[0]) / 2.0
            hh = abs(coords[3] - coords[1]) / 2.0
            rotation = shape.get("rotation", 0)
            # Transform point into shape-local (unrotated) space
            if rotation != 0:
                rad = math.radians(-rotation)
                cos_r, sin_r = math.cos(rad), math.sin(rad)
                dx = x - cx
                dy = y - cy
                lx = dx * cos_r - dy * sin_r
                ly = dx * sin_r + dy * cos_r
            else:
                lx = x - cx
                ly = y - cy
            if tool == "rect":
                if abs(lx) <= hw and abs(ly) <= hh:
                    return i
            else:  # ellipse
                if hw > 0 and hh > 0:
                    if (lx * lx) / (hw * hw) + (ly * ly) / (hh * hh) <= 1.0:
                        return i
        return None

    def _region_hit_test_handle(self, x: float, y: float) -> bool:
        """Return True if point (x, y) is on the rotation handle of the selected shape."""
        if self.region_selected_index is None or self.region_selected_index >= len(self.region_shapes):
            return False
        shape = self.region_shapes[self.region_selected_index]
        coords = shape.get("coords", [])
        if len(coords) < 4:
            return False
        import math
        cx = (coords[0] + coords[2]) / 2.0
        cy = (coords[1] + coords[3]) / 2.0
        hh = abs(coords[3] - coords[1]) / 2.0
        rotation = shape.get("rotation", 0)
        rad = math.radians(rotation)
        handle_offset = 14
        hx = cx + math.sin(rad) * (hh + handle_offset)
        hy = cy - math.cos(rad) * (hh + handle_offset)
        hx, hy = self._region_clamp_canvas_point(hx, hy)
        return math.hypot(x - hx, y - hy) <= 8

    def _region_hit_test_resize(self, x: float, y: float) -> int | None:
        """Return corner index (0=tl, 1=tr, 2=bl, 3=br) if (x,y) is on a resize handle."""
        if self.region_selected_index is None or self.region_selected_index >= len(self.region_shapes):
            return None
        shape = self.region_shapes[self.region_selected_index]
        coords = shape.get("coords", [])
        if len(coords) < 4:
            return None
        import math
        cx = (coords[0] + coords[2]) / 2.0
        cy = (coords[1] + coords[3]) / 2.0
        rotation = shape.get("rotation", 0)
        rad = math.radians(rotation)
        corners = [
            (coords[0], coords[1]),
            (coords[2], coords[1]),
            (coords[0], coords[3]),
            (coords[2], coords[3]),
        ]
        for i, (ux, uy) in enumerate(corners):
            dx = ux - cx
            dy = uy - cy
            rx = cx + dx * math.cos(rad) - dy * math.sin(rad)
            ry = cy + dx * math.sin(rad) + dy * math.cos(rad)
            if abs(x - rx) <= 5 and abs(y - ry) <= 5:
                return i
        return None

    def _region_canvas_press(self, event):
        tool = self.region_tool.get()
        if tool not in ("rect", "ellipse"):
            return

        # 1. If a shape is selected, check for rotation-handle click
        if self.region_selected_index is not None:
            if self._region_hit_test_handle(event.x, event.y):
                self.region_drag_mode = "rotate"
                self.region_drag_start = (event.x, event.y)
                return

            # 1b. Check for resize-handle click
            corner = self._region_hit_test_resize(event.x, event.y)
            if corner is not None:
                self.region_drag_mode = "resize"
                self.region_drag_start = (event.x, event.y)
                self._region_resize_corner = corner
                # Store opposite corner as anchor
                shape = self.region_shapes[self.region_selected_index]
                coords = shape["coords"]
                opposite = {0: 3, 1: 2, 2: 1, 3: 0}[corner]
                opp_corners = [
                    (coords[0], coords[1]),
                    (coords[2], coords[1]),
                    (coords[0], coords[3]),
                    (coords[2], coords[3]),
                ]
                self._region_resize_anchor_x = opp_corners[opposite][0]
                self._region_resize_anchor_y = opp_corners[opposite][1]
                return

        # 2. Check if clicking on any shape body
        hit = self._region_hit_test(event.x, event.y)
        if hit is not None:
            # Select and prepare for move
            self.region_selected_index = hit
            shape = self.region_shapes[hit]
            angle = shape.get("rotation", 0)
            self.region_rotation_var.set(angle)
            self.region_rotation_display.set(f"{angle}°")
            self.region_rotation_slider.config(state="normal")
            self.region_rotation_label.config(state="normal")
            self.region_drag_mode = "move"
            self.region_drag_start = (event.x, event.y)
            self._region_move_snapshot = list(shape["coords"])
            self._region_redraw_overlay()
            return

        # 3. Click on empty space — deselect and prepare for new-shape drag
        self.region_selected_index = None
        self.region_rotation_var.set(0)
        self.region_rotation_display.set("0°")
        self.region_rotation_slider.config(state="disabled")
        self.region_rotation_label.config(state="disabled")
        if not self._region_point_in_image_bounds(event.x, event.y):
            self.region_drag_mode = None
            self.region_drag_start = None
            self._region_redraw_overlay()
            return
        self.region_drag_mode = "draw"
        self.region_drag_start = self._region_clamp_canvas_point(event.x, event.y)
        self._region_redraw_overlay()

    def _region_canvas_drag(self, event):
        import math

        if self.region_drag_mode == "move":
            if self.region_selected_index is not None and self._region_move_snapshot:
                dx = event.x - self.region_drag_start[0]
                dy = event.y - self.region_drag_start[1]
                orig = self._region_move_snapshot
                self.region_shapes[self.region_selected_index]["coords"] = self._region_constrain_bbox_to_image(
                    orig[0] + dx, orig[1] + dy,
                    orig[2] + dx, orig[3] + dy,
                )
                self._region_redraw_overlay()
            return

        if self.region_drag_mode == "rotate":
            if self.region_selected_index is not None:
                shape = self.region_shapes[self.region_selected_index]
                coords = shape["coords"]
                cx = (coords[0] + coords[2]) / 2.0
                cy = (coords[1] + coords[3]) / 2.0
                angle = math.degrees(math.atan2(event.x - cx, -(event.y - cy)))
                angle = round(angle)
                # Normalize to [-180, 180]
                angle = ((angle + 180) % 360) - 180
                shape["rotation"] = angle
                self.region_rotation_var.set(angle)
                self.region_rotation_display.set(f"{angle}°")
                self._region_redraw_overlay()
            return

        if self.region_drag_mode == "resize":
            if self.region_selected_index is not None and self._region_resize_corner is not None:
                shape = self.region_shapes[self.region_selected_index]
                coords = shape["coords"]
                cx = (coords[0] + coords[2]) / 2.0
                cy = (coords[1] + coords[3]) / 2.0
                rotation = shape.get("rotation", 0)
                # Unrotate mouse position into shape-local space
                if rotation != 0:
                    rad_inv = math.radians(-rotation)
                    px, py = self._region_clamp_canvas_point(event.x, event.y)
                    dx = px - cx
                    dy = py - cy
                    lx = cx + dx * math.cos(rad_inv) - dy * math.sin(rad_inv)
                    ly = cy + dx * math.sin(rad_inv) + dy * math.cos(rad_inv)
                else:
                    lx, ly = self._region_clamp_canvas_point(event.x, event.y)
                anchor_x = self._region_resize_anchor_x
                anchor_y = self._region_resize_anchor_y
                corner = self._region_resize_corner
                if corner == 0:      # tl
                    x1, y1, x2, y2 = lx, ly, anchor_x, anchor_y
                elif corner == 1:    # tr
                    x1, y1, x2, y2 = anchor_x, ly, lx, anchor_y
                elif corner == 2:    # bl
                    x1, y1, x2, y2 = lx, anchor_y, anchor_x, ly
                else:                # br
                    x1, y1, x2, y2 = anchor_x, anchor_y, lx, ly
                shape["coords"] = self._region_clamp_bbox_to_image(x1, y1, x2, y2)
                self._region_redraw_overlay()
            return

        # Draw mode — existing rubberband logic
        tool = self.region_tool.get()
        if self.region_drag_start:
            x1, y1 = self.region_drag_start
            x2, y2 = self._region_clamp_canvas_point(event.x, event.y)
            if self.region_rubber_id:
                self.region_canvas.delete(self.region_rubber_id)
            if tool == "rect":
                self.region_rubber_id = self.region_canvas.create_rectangle(
                    x1, y1, x2, y2, outline="#ff4444", dash=(4, 2))
            elif tool == "ellipse":
                self.region_rubber_id = self.region_canvas.create_oval(
                    x1, y1, x2, y2, outline="#ff4444", dash=(4, 2))

    def _region_canvas_release(self, event):
        if self.region_drag_mode == "move":
            self.region_drag_mode = None
            self._region_move_snapshot = None
            self.region_drag_start = None
            self._region_redraw_overlay()
            self._region_update_button_states()
            return

        if self.region_drag_mode == "rotate":
            self.region_drag_mode = None
            self.region_drag_start = None
            self._region_redraw_overlay()
            self._region_update_button_states()
            return

        if self.region_drag_mode == "resize":
            self.region_drag_mode = None
            self.region_drag_start = None
            self._region_resize_corner = None
            self._region_redraw_overlay()
            self._region_update_button_states()
            return

        # Draw mode — existing logic
        tool = self.region_tool.get()
        if tool in ("rect", "ellipse") and self.region_drag_start:
            x1, y1 = self.region_drag_start
            x2, y2 = self._region_clamp_canvas_point(event.x, event.y)
            coords = self._region_clamp_bbox_to_image(x1, y1, x2, y2)
            if abs(x2 - x1) > 3 and abs(y2 - y1) > 3:
                self.region_shapes.append({"tool": tool, "coords": coords, "rotation": 0})
            if self.region_rubber_id:
                self.region_canvas.delete(self.region_rubber_id)
                self.region_rubber_id = None
            self.region_drag_start = None
            self._region_redraw_overlay()
        self.region_drag_mode = None
        self._region_update_button_states()

    def _region_canvas_double_click(self, event):
        pass  # no-op (polygon removed)

    def _region_canvas_motion(self, _event):
        """Cursor preview (no-op for now, could show crosshair)."""
        pass

    def _region_redraw_overlay(self):
        """Redraw the red semi-transparent mask overlay on the canvas.
        Supports rotated shapes via OpenCV; falls back to PIL for axis-aligned."""
        try:
            from PIL import Image, ImageDraw, ImageTk
        except Exception:
            return
        if not self.region_images:
            return
        image_path = self.region_images[0]
        # Use cached full-res image to avoid disk I/O.
        if self._region_cached_pil is None or self._region_cached_pil_path != image_path:
            self._region_cached_pil = Image.open(image_path).convert("RGBA")
            self._region_cached_pil_path = image_path
        img = self._region_cached_pil
        cw = self.region_canvas.winfo_width() or 600
        ch = self.region_canvas.winfo_height() or 500
        new_w, new_h = self._region_shared_display_size(img.width, img.height)
        offset_x = max(2, int((cw - new_w) / 2))
        offset_y = max(2, int((ch - new_h) / 2))
        self._region_canvas_image_offset = (offset_x, offset_y)
        # Use cached display-size image when canvas size hasn't changed
        cur_display = (new_w, new_h)
        if self._region_cached_display_pil is not None and self._region_cached_display_size == cur_display:
            img = self._region_cached_display_pil
        else:
            img = img.copy().resize((new_w, new_h), Image.LANCZOS)
            self._region_cached_display_pil = img
            self._region_cached_display_size = cur_display
        # Draw red overlay for each shape
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        # Try OpenCV for rotated shapes
        loaded = load_cv2()
        if loaded:
            cv2, np = loaded
            # Build overlay as numpy (H, W, 4) uint8
            overlay_np = np.zeros((new_h, new_w, 4), dtype=np.uint8)
            for i, shape in enumerate(self.region_shapes):
                tool = shape.get("tool", "")
                coords = shape.get("coords", [])
                if tool not in ("rect", "ellipse") or len(coords) < 4:
                    continue
                x1, y1, x2, y2 = [float(c) for c in coords[:4]]
                x1 -= offset_x
                x2 -= offset_x
                y1 -= offset_y
                y2 -= offset_y
                if x1 > x2:
                    x1, x2 = x2, x1
                if y1 > y2:
                    y1, y2 = y2, y1
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
                hw = (x2 - x1) / 2.0
                hh = (y2 - y1) / 2.0
                rotation = shape.get("rotation", 0)
                is_selected = (i == self.region_selected_index)
                fill_color = (0, 0, 255, 100) if is_selected else (0, 0, 255, 80)  # BGR+A in cv2 space → RGBA later
                # Note: cv2 uses BGR order, we'll convert later
                if tool == "rect":
                    if rotation != 0:
                        # Rotated rectangle via boxPoints
                        rect = ((cx, cy), (hw * 2, hh * 2), rotation)
                        box = cv2.boxPoints(rect).astype(np.int32)
                        cv2.fillPoly(overlay_np, [box], fill_color)
                    else:
                        cv2.rectangle(overlay_np,
                                      (int(x1), int(y1)), (int(x2), int(y2)),
                                      fill_color, thickness=-1)
                else:  # ellipse
                    # cv2.ellipse: axes are (semi-major, semi-minor), use (hw, hh)
                    cv2.ellipse(overlay_np,
                                (int(cx), int(cy)), (int(hw), int(hh)),
                                rotation, 0.0, 360.0,
                                fill_color, thickness=-1)
                # Draw selection highlight outline
                if is_selected:
                    outline_color = (255, 255, 0, 255)  # Yellow outline
                    if tool == "rect" and rotation != 0:
                        rect = ((cx, cy), (hw * 2, hh * 2), rotation)
                        box = cv2.boxPoints(rect).astype(np.int32)
                        cv2.polylines(overlay_np, [box], isClosed=True, color=outline_color, thickness=2)
                    elif tool == "rect":
                        cv2.rectangle(overlay_np, (int(x1), int(y1)), (int(x2), int(y2)),
                                      outline_color, thickness=2)
                    else:
                        cv2.ellipse(overlay_np, (int(cx), int(cy)), (int(hw), int(hh)),
                                    rotation, 0.0, 360.0, outline_color, thickness=2)
            # Convert numpy (BGRA) → PIL RGBA overlay
            # cv2 uses BGRA, PIL uses RGBA: swap R and B channels
            overlay_np_rgba = np.zeros_like(overlay_np)
            overlay_np_rgba[:, :, 0] = overlay_np[:, :, 2]  # R ← B
            overlay_np_rgba[:, :, 1] = overlay_np[:, :, 1]  # G ← G
            overlay_np_rgba[:, :, 2] = overlay_np[:, :, 0]  # B ← R
            overlay_np_rgba[:, :, 3] = overlay_np[:, :, 3]  # A ← A
            overlay = Image.fromarray(overlay_np_rgba, "RGBA")
        else:
            # Fallback: PIL axis-aligned only (legacy path)
            draw = ImageDraw.Draw(overlay)
            for i, shape in enumerate(self.region_shapes):
                tool = shape.get("tool", "")
                coords = shape.get("coords", [])
                if tool in ("rect", "ellipse") and len(coords) >= 4:
                    x1, y1, x2, y2 = [int(c) for c in coords[:4]]
                    x1 -= offset_x
                    x2 -= offset_x
                    y1 -= offset_y
                    y2 -= offset_y
                    x1, x2 = sorted([x1, x2])
                    y1, y2 = sorted([y1, y2])
                    is_selected = (i == self.region_selected_index)
                    fill = (255, 0, 0, 100) if is_selected else (255, 0, 0, 80)
                    if tool == "rect":
                        draw.rectangle([x1, y1, x2, y2], fill=fill)
                    else:
                        draw.ellipse([x1, y1, x2, y2], fill=fill)
                    if is_selected:
                        draw.rectangle([x1, y1, x2, y2], outline=(255, 255, 0), width=2)
        img = Image.alpha_composite(img, overlay)
        self.region_canvas_image_ref = ImageTk.PhotoImage(img)
        self.region_canvas.delete("all")
        self.region_canvas.create_image(offset_x, offset_y, anchor="nw", image=self.region_canvas_image_ref)
        self._region_draw_image_frame(self.region_canvas, offset_x, offset_y, new_w, new_h)

        # Draw rotation handle on canvas for the selected shape
        if self.region_selected_index is not None and self.region_selected_index < len(self.region_shapes):
            shape = self.region_shapes[self.region_selected_index]
            coords = shape.get("coords", [])
            if len(coords) >= 4:
                import math
                cx = (coords[0] + coords[2]) / 2.0
                cy = (coords[1] + coords[3]) / 2.0
                hh = abs(coords[3] - coords[1]) / 2.0
                rotation = shape.get("rotation", 0)
                rad = math.radians(rotation)
                handle_offset = 14
                hx = cx + math.sin(rad) * (hh + handle_offset)
                hy = cy - math.cos(rad) * (hh + handle_offset)
                hx, hy = self._region_clamp_canvas_point(hx, hy)
                r = 5
                lid = self.region_canvas.create_line(cx, cy, hx, hy, fill="#ffff00", width=1, tags=("rot_handle",))
                cid = self.region_canvas.create_oval(hx - r, hy - r, hx + r, hy + r,
                                                     fill="#ffff00", outline="#000000", width=1,
                                                     tags=("rot_handle",))
                self._region_handle_ids = [lid, cid]

                # Draw corner resize handles
                hw = abs(coords[2] - coords[0]) / 2.0
                # 4 unrotated corners: tl, tr, bl, br
                corners = [
                    (coords[0], coords[1]),
                    (coords[2], coords[1]),
                    (coords[0], coords[3]),
                    (coords[2], coords[3]),
                ]
                corner_tags = ("rsz_tl", "rsz_tr", "rsz_bl", "rsz_br")
                rs = 3  # half-size of resize handle square
                for i, (ux, uy) in enumerate(corners):
                    # Rotate around center
                    dx = ux - cx
                    dy = uy - cy
                    rx = cx + dx * math.cos(rad) - dy * math.sin(rad)
                    ry = cy + dx * math.sin(rad) + dy * math.cos(rad)
                    rx, ry = self._region_clamp_canvas_point(rx, ry)
                    rid = self.region_canvas.create_rectangle(
                        rx - rs, ry - rs, rx + rs, ry + rs,
                        fill="#ffffff", outline="#000000", width=1,
                        tags=("resize_handle", corner_tags[i]),
                    )
                    self._region_handle_ids.append(rid)

    def _region_on_rotation_changed(self, _value=None):
        """Callback when the rotation slider is moved."""
        if self.region_drag_mode == "rotate":
            return  # Canvas handle manages rotation; avoid double update
        if self.region_selected_index is not None and self.region_selected_index < len(self.region_shapes):
            angle = int(float(self.region_rotation_var.get()))
            self.region_shapes[self.region_selected_index]["rotation"] = angle
            self.region_rotation_display.set(f"{angle}°")
            self._region_redraw_overlay()

    def _region_rotation_entry_apply(self, _event=None):
        """Apply the angle typed into the rotation entry box."""
        if self.region_selected_index is None:
            return
        try:
            raw = self.region_rotation_display.get().replace("°", "").strip()
            angle = int(float(raw))
            angle = max(-180, min(180, angle))
            self.region_rotation_var.set(angle)
        except (ValueError, TypeError):
            # Restore to current shape's rotation on invalid input
            current = self.region_shapes[self.region_selected_index].get("rotation", 0)
            self.region_rotation_display.set(f"{current}°")

    def _region_on_mousewheel(self, event):
        """Scroll wheel rotates the selected shape by +/-5 degrees."""
        if self.region_selected_index is None:
            return
        if self.region_selected_index >= len(self.region_shapes):
            return
        # Determine direction: Windows uses event.delta, Linux uses event.num
        if hasattr(event, "delta"):
            delta = 1 if event.delta > 0 else -1
        elif hasattr(event, "num"):
            delta = 1 if event.num == 4 else -1
        else:
            return
        shape = self.region_shapes[self.region_selected_index]
        current = shape.get("rotation", 0)
        step = 1 if (event.state & 0x0001) else 5  # Shift held = fine (1 deg), else coarse (5 deg)
        new_angle = current + delta * step
        # Clamp to [-180, 180]
        new_angle = max(-180, min(180, new_angle))
        shape["rotation"] = new_angle
        self.region_rotation_var.set(new_angle)
        self.region_rotation_display.set(f"{new_angle}°")
        self._region_redraw_overlay()

    def _region_clear_mask(self):
        self.region_shapes.clear()
        self.region_poly_points.clear()
        self.region_drag_start = None
        self.region_drag_mode = None
        self._region_move_snapshot = None
        self._region_resize_corner = None
        self.region_selected_index = None
        self.region_rotation_var.set(0)
        self.region_rotation_display.set("0°")
        self.region_rotation_slider.config(state="disabled")
        self.region_rotation_label.config(state="disabled")
        if self.region_rubber_id:
            self.region_canvas.delete(self.region_rubber_id)
            self.region_rubber_id = None
        self.region_mask = None
        if self.region_images:
            self._region_display_image(self.region_images[0])
        self._region_update_button_states()

    def _region_generate_mask(self) -> "Image.Image | None":
        """Convert canvas shapes to a PIL 'L' mask at working resolution."""
        if not self.region_shapes or not self.region_images:
            return None
        try:
            from PIL import Image
            img = Image.open(self.region_images[0])
            w, h = img.size
            scale = self._region_get_canvas_scale()
            offset_x, offset_y = getattr(self, "_region_canvas_image_offset", (0, 0))
            adjusted_shapes = []
            for shape in self.region_shapes:
                adjusted = dict(shape)
                coords = shape.get("coords", [])
                if len(coords) >= 4:
                    adjusted["coords"] = [
                        float(coords[0]) - offset_x,
                        float(coords[1]) - offset_y,
                        float(coords[2]) - offset_x,
                        float(coords[3]) - offset_y,
                        *coords[4:],
                    ]
                adjusted_shapes.append(adjusted)
            from region_painter.image_processor import mask_from_canvas_shapes
            mask = mask_from_canvas_shapes(adjusted_shapes, w, h, scale)
            return mask
        except Exception as e:
            self.log_line(tr(self.lang, "region_mask_failed_detail").format(error=e), scope="region")
            return None

    # ==================================================================
    # Region Paint — Button state management
    # ==================================================================

    def _region_update_button_states(self):
        has_image = bool(self.region_images)
        has_shapes = bool(self.region_shapes)
        running = self.region_workflow_running
        has_output = bool(self.region_current_output_dir)
        self.region_first_pass_btn.config(state="normal" if has_image and not running else "disabled")
        self.region_paint_btn.config(state="normal" if has_image and has_shapes and not running else "disabled")
        self.region_stop_btn.config(state="normal" if running else "disabled")
        self.region_open_folder_btn.config(state="normal" if has_output and not running else "disabled")
        self.region_save_json_btn.config(state="normal" if has_output and not running else "disabled")
        # Heatmap tab: only clickable when a heatmap exists
        has_heatmap = bool(self._region_heatmap_showing)
        if self.region_tab_heatmap_btn:
            if has_heatmap or self._region_right_tab == "heatmap":
                self.region_tab_heatmap_btn.config(cursor="hand2")
            else:
                self.region_tab_heatmap_btn.config(cursor="arrow")
        # Update remaining from saved state if available, else from entries
        if self.region_current_output_dir:
            try:
                status = region_get_status(self.region_current_output_dir)
                self.region_remaining_var.set(str(status.get("remaining", 0)))
                return
            except Exception:
                pass
        try:
            total = int(self.region_total_var.get() or 0)
            self.region_remaining_var.set(str(total))
        except ValueError:
            self.region_remaining_var.set("0")

    def _localize_region_line(self, raw):
        text = str(raw or "")
        match = re.match(r"Generated layer\s+(\d+)/(\d+)", text)
        if match:
            current, total = match.groups()
            return f"{tr(self.lang, 'log_layer')} {current}/{total}"
        match = re.match(r"Saved JSON checkpoint\s+(\d+)/(\d+)", text)
        if match:
            current, total = match.groups()
            return f"{tr(self.lang, 'log_checkpoint')} {current}/{total}"
        match = re.match(r"Total budget:\s*(\d+),\s*first pass:\s*(\d+)", text)
        if match:
            total, first = match.groups()
            return tr(self.lang, "region_first_pass_budget").format(total=total, first=first)
        match = re.match(r"Region pass:\s*(\d+) layers \(pruned=(\d+), stopAt=(\d+), remaining=(\d+)\)", text)
        if match:
            layers, pruned, stop_at, remaining = match.groups()
            return tr(self.lang, "region_pass_budget").format(
                layers=layers,
                pruned=pruned,
                stop_at=stop_at,
                remaining=remaining,
            )
        match = re.match(r"(?:Generator exited with code|Exit code)\s+(-?\d+)", text)
        if match:
            return tr(self.lang, "region_generator_exit").format(code=match.group(1))
        if text == "Shutdown":
            return tr(self.lang, "region_shutdown")
        if text == "Stopped":
            return tr(self.lang, "stopped")
        if text == "First pass has not been completed.":
            return tr(self.lang, "region_no_first_pass")
        if text == "No JSON output found after generation.":
            return tr(self.lang, "region_no_json_output")
        if text == "No remaining budget for region pass.":
            return tr(self.lang, "region_no_remaining_budget")
        if text == "No accumulated shapes found.":
            return tr(self.lang, "region_no_accumulated_shapes")
        if text == "Invalid background shape.":
            return tr(self.lang, "region_invalid_background_shape")
        match = re.match(r"Generator exe not found:\s*(.+)", text)
        if match:
            return tr(self.lang, "region_generator_missing").format(path=match.group(1))
        match = re.match(r"Failed to apply selection mask:\s*(.+)", text)
        if match:
            return tr(self.lang, "region_mask_apply_failed").format(error=match.group(1))
        match = re.match(r"Original settings INI not found:\s*(.+)", text)
        if match:
            return tr(self.lang, "region_settings_missing").format(path=match.group(1))
        match = re.match(r"Base JSON not found:\s*(.+)", text)
        if match:
            return tr(self.lang, "region_base_json_missing").format(path=match.group(1))
        return text

    def _region_update_profile_description(self, _event=None):
        """Show the selected profile's description and sync total budget."""
        label = self.region_selected_profile.get()
        item = next(
            (s for s in self.settings if s["label"] == label or self._localized_profile_label(s) == label),
            None,
        )
        desc = self._localized_profile_description(item) if item else ""
        self.region_profile_description.config(text=desc)
        # Sync total budget from profile's stopAt
        if item:
            stop_at = item.get("values", {}).get("stopAt", "")
            if stop_at:
                self.region_total_var.set(stop_at)
                self._region_update_button_states()

    # ==================================================================
    # Region Paint — Result actions
    # ==================================================================

    def _region_open_result_folder(self):
        """Open the current output directory in the file manager."""
        if not self.region_current_output_dir:
            return
        folder = Path(self.region_current_output_dir)
        if folder.exists():
            os.startfile(folder)
            self.log_line(tr(self.lang, "region_result_folder_opened").format(path=folder), scope="region")

    def _region_save_result_json(self):
        """Save base.json from the current output directory to a user-chosen location."""
        if not self.region_current_output_dir:
            return
        base_json = Path(self.region_current_output_dir) / "base.json"
        if not base_json.exists():
            self.log_line(tr(self.lang, "region_no_result_json"), scope="region")
            return
        output = filedialog.asksaveasfilename(
            title=tr(self.lang, "region_save_result_title"),
            initialdir=str(Path(self.region_current_output_dir)),
            initialfile="base.json",
            defaultextension=".json",
            filetypes=[(tr(self.lang, "geometry_json_file"), "*.json"), (tr(self.lang, "all_files"), "*.*")],
        )
        if not output:
            return
        try:
            shutil.copy2(base_json, output)
            self.log_line(tr(self.lang, "region_result_saved").format(path=output), scope="region")
        except OSError as exc:
            self.log_line(tr(self.lang, "region_result_save_failed").format(error=exc), scope="region")

    # ==================================================================
    # Region Paint — Worker threads
    # ==================================================================

    def _region_start_first_pass(self):
        self.active_log_scope = "region"
        if not self.region_images:
            self.log_line(tr(self.lang, "region_no_image"), scope="region")
            return
        image_path = self.region_images[0]
        profile_label = self.region_selected_profile.get()
        setting = next(
            (s for s in self.settings if s["label"] == profile_label or self._localized_profile_label(s) == profile_label),
            None,
        )
        if setting is None and self.settings:
            setting = self.settings[0]
        if setting is None:
            self.log_line(tr(self.lang, "log_no_quality_profile"), scope="region")
            return
        try:
            total_budget = int(self.region_total_var.get() or 2000)
            first_layers = int(self.region_first_var.get() or 1000)
        except ValueError:
            self.log_line(tr(self.lang, "region_invalid_budget"), scope="region")
            return
        output_dir = ROOT / "runtime" / "region-painter" / f"{image_path.stem}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self.region_current_output_dir = str(output_dir)
        self.region_workflow_running = True
        # state.json does not exist yet — show total budget as remaining
        self.region_remaining_var.set(str(total_budget))
        self._region_update_button_states()
        self.region_status.set(tr(self.lang, "running"))
        self.region_progress.set(tr(self.lang, "region_starting_first_pass"))
        self._set_scoped_log_progress("region", 0, tr(self.lang, "region_starting_first_pass"))
        self.log_line(tr(self.lang, "region_first_pass_starting"), scope="region")
        threading.Thread(
            target=self._region_first_pass_worker,
            args=(image_path, setting, first_layers, output_dir),
            daemon=True,
        ).start()

    def _region_first_pass_worker(self, image_path: Path, setting, first_layers: int, output_dir: Path):
        """Worker thread: prepare, run exe (streaming), finalize."""
        def on_progress(msg):
            self.queue.put(("region_log", msg))
            self.queue.put(("region_progress", msg))
        try:
            prep = prepare_first_pass(
                image_path=str(image_path),
                settings_path=str(setting["path"]),
                first_layers=first_layers,
                output_dir=str(output_dir),
                on_progress=on_progress,
            )
            if "error" in prep:
                self.queue.put(("region_done", {"ok": False, "error": prep["error"]}))
                return

            # --- Run exe with streaming (same pattern as _generate_worker) ---
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            proc = self._popen_registered(
                prep["cmd"],
                cwd=str(output_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
                env=build_generator_env(),
            )
            if proc is None:
                self.queue.put(("region_done", {"ok": False, "error": tr(self.lang, "region_shutdown")}))
                return
            with self.generation_lock:
                self.current_generator_proc = proc

            output_queue = queue.Queue()

            def _reader():
                try:
                    for raw_line in proc.stdout:
                        output_queue.put(raw_line)
                finally:
                    output_queue.put(None)

            reader = threading.Thread(target=_reader, daemon=True)
            reader.start()

            # --- Poll for exe preview files (same pattern as _generate_worker) ---
            last_preview_mtime = None
            next_preview_scan = 0.0

            try:
                while proc.poll() is None:
                    if self.shutdown_event.is_set():
                        self._terminate_process(proc)
                        self.queue.put(("region_done", {"ok": False, "error": tr(self.lang, "stopped")}))
                        return
                    while True:
                        try:
                            raw = output_queue.get_nowait()
                        except queue.Empty:
                            break
                        if raw is None:
                            continue
                        stripped = raw.strip()
                        if stripped:
                            friendly = self.friendly_generator_line(stripped)
                            if friendly:
                                self.queue.put(("region_log", friendly))
                    # --- Preview polling ---
                    now = time.monotonic()
                    if now >= next_preview_scan:
                        next_preview_scan = now + GENERATOR_PREVIEW_SCAN_SECONDS
                        preview_files = sorted(
                            output_dir.glob("_exe_preview*.png"),
                            key=lambda p: p.stat().st_mtime, reverse=True,
                        )
                        if preview_files:
                            newest = preview_files[0]
                            try:
                                mtime = newest.stat().st_mtime
                            except OSError:
                                mtime = None
                            if mtime is not None and mtime != last_preview_mtime:
                                last_preview_mtime = mtime
                                self.queue.put(("region_preview", str(newest)))
                    time.sleep(GENERATOR_POLL_SLEEP_SECONDS)
                reader.join(timeout=1)
            finally:
                self._unregister_process(proc)
                with self.generation_lock:
                    if self.current_generator_proc is proc:
                        self.current_generator_proc = None

            if proc.returncode != 0:
                message = tr(self.lang, "region_generator_exit").format(code=proc.returncode)
                self.queue.put(("region_log", message))
                self.queue.put(("region_done", {"ok": False, "error": message}))
                return

            result = finalize_first_pass(prep)
            result["preview_path"] = prep.get("preview_png", "")
            # Generate heatmap from the resulting geometry JSON
            try:
                base_json = Path(prep["base_json"])
                if base_json.exists():
                    heatmap_png = base_json.parent / "heatmap.png"
                    from scripts.heatmap import generate_standalone_heatmap
                    generate_standalone_heatmap(base_json, heatmap_png)
                    result["heatmap_path"] = str(heatmap_png)
            except Exception as hm_err:
                self.queue.put(("region_log", tr(self.lang, "region_heatmap_skipped").format(error=hm_err)))
            self.queue.put(("region_done", result))
        except Exception as e:
            self.queue.put(("region_status", tr(self.lang, "failed")))
            self.queue.put(("region_log", tr(self.lang, "region_first_pass_error").format(error=e)))
            self.queue.put(("region_done", {"ok": False, "error": str(e)}))

    def _region_start_pass(self):
        self.active_log_scope = "region"
        if self.region_workflow_running:
            self.log_line(tr(self.lang, "region_already_running"), scope="region")
            return
        if not self.region_shapes:
            self.log_line(tr(self.lang, "region_no_mask"), scope="region")
            return
        mask = self._region_generate_mask()
        if mask is None:
            self.log_line(tr(self.lang, "region_mask_failed"), scope="region")
            return
        try:
            region_layers = int(self.region_layers_var.get() or 300)
        except ValueError:
            self.log_line(tr(self.lang, "region_invalid_region_layers"), scope="region")
            return
        output_dir = Path(self.region_current_output_dir)
        self.region_workflow_running = True
        self._region_update_button_states()
        self.region_status.set(tr(self.lang, "running"))
        self.region_progress.set(tr(self.lang, "region_starting_region_pass"))
        self._set_scoped_log_progress("region", 0, tr(self.lang, "region_starting_region_pass"))
        self.log_line(tr(self.lang, "region_pass_starting"), scope="region")
        threading.Thread(
            target=self._region_pass_worker,
            args=(output_dir, region_layers, mask),
            daemon=True,
        ).start()

    def _region_pass_worker(self, output_dir: Path, region_layers: int, mask):
        """Worker thread: prepare, run exe (streaming), finalize."""
        def on_progress(msg):
            self.queue.put(("region_log", msg))
            self.queue.put(("region_progress", msg))
        try:
            prep = prepare_region_pass(
                output_dir=str(output_dir),
                region_layers=region_layers,
                selection_mask=mask,
                on_progress=on_progress,
            )
            if "error" in prep:
                self.queue.put(("region_done", {"ok": False, "error": prep["error"]}))
                return

            # --- Run exe with streaming (same pattern as _generate_worker) ---
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            proc = self._popen_registered(
                prep["cmd"],
                cwd=str(output_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
                env=build_generator_env(),
            )
            if proc is None:
                self.queue.put(("region_done", {"ok": False, "error": tr(self.lang, "region_shutdown")}))
                return
            with self.generation_lock:
                self.current_generator_proc = proc

            output_queue = queue.Queue()

            def _reader():
                try:
                    for raw_line in proc.stdout:
                        output_queue.put(raw_line)
                finally:
                    output_queue.put(None)

            reader = threading.Thread(target=_reader, daemon=True)
            reader.start()

            # --- Poll for exe preview files ---
            last_preview_mtime = None
            next_preview_scan = 0.0

            try:
                while proc.poll() is None:
                    if self.shutdown_event.is_set():
                        self._terminate_process(proc)
                        self.queue.put(("region_done", {"ok": False, "error": tr(self.lang, "stopped")}))
                        return
                    while True:
                        try:
                            raw = output_queue.get_nowait()
                        except queue.Empty:
                            break
                        if raw is None:
                            continue
                        stripped = raw.strip()
                        if stripped:
                            friendly = self.friendly_generator_line(stripped)
                            if friendly:
                                self.queue.put(("region_log", friendly))
                    # --- Preview polling ---
                    now = time.monotonic()
                    if now >= next_preview_scan:
                        next_preview_scan = now + GENERATOR_PREVIEW_SCAN_SECONDS
                        preview_files = sorted(
                            output_dir.glob("_exe_preview*.png"),
                            key=lambda p: p.stat().st_mtime, reverse=True,
                        )
                        if preview_files:
                            newest = preview_files[0]
                            try:
                                mtime = newest.stat().st_mtime
                            except OSError:
                                mtime = None
                            if mtime is not None and mtime != last_preview_mtime:
                                last_preview_mtime = mtime
                                self.queue.put(("region_preview", str(newest)))
                    time.sleep(GENERATOR_POLL_SLEEP_SECONDS)
                reader.join(timeout=1)
            finally:
                self._unregister_process(proc)
                with self.generation_lock:
                    if self.current_generator_proc is proc:
                        self.current_generator_proc = None

            if proc.returncode != 0:
                message = tr(self.lang, "region_generator_exit").format(code=proc.returncode)
                self.queue.put(("region_log", message))
                self.queue.put(("region_done", {"ok": False, "error": message}))
                return

            result = finalize_region_pass(prep)
            result["preview_path"] = prep.get("preview_png", "")
            # Generate heatmap from the resulting geometry JSON
            try:
                base_json = Path(prep["base_json"])
                if base_json.exists():
                    heatmap_png = base_json.parent / "heatmap.png"
                    from scripts.heatmap import generate_standalone_heatmap
                    generate_standalone_heatmap(base_json, heatmap_png)
                    result["heatmap_path"] = str(heatmap_png)
            except Exception as hm_err:
                self.queue.put(("region_log", tr(self.lang, "region_heatmap_skipped").format(error=hm_err)))
            self.queue.put(("region_done", result))
        except Exception as e:
            self.queue.put(("region_status", tr(self.lang, "failed")))
            self.queue.put(("region_log", tr(self.lang, "region_pass_error").format(error=e)))
            self.queue.put(("region_done", {"ok": False, "error": str(e)}))

    def _region_stop(self):
        self.shutdown_event.set()
        self.region_status.set(tr(self.lang, "stopped"))
        self.region_workflow_running = False
        self._region_update_button_states()

    def _region_display_preview(self, preview_path: Path):
        """Display a rendered preview image on the RIGHT region canvas."""
        try:
            from PIL import Image, ImageTk
            if hasattr(self, "region_heatmap_bar_canvas"):
                self.region_heatmap_bar_canvas.delete("all")
                self._region_heatmap_bar_item_id = None
                self.region_heatmap_bar_canvas.place_forget()
            img = Image.open(preview_path).convert("RGBA")
            cw = self.region_canvas_right.winfo_width() or 300
            ch = self.region_canvas_right.winfo_height() or 500
            if self._region_cached_display_size:
                new_w, new_h = self._region_cached_display_size
            else:
                new_w, new_h = self._region_shared_display_size(img.width, img.height)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            offset_x = max(2, int((cw - new_w) / 2))
            offset_y = max(2, int((ch - new_h) / 2))
            self.region_preview_ref = ImageTk.PhotoImage(img)
            self.region_canvas_right.delete("all")
            self.region_canvas_right.create_image(offset_x, offset_y, anchor="nw", image=self.region_preview_ref)
            self._region_draw_image_frame(self.region_canvas_right, offset_x, offset_y, new_w, new_h)
            self._region_preview_showing = str(preview_path)
        except Exception:
            pass

    def _region_display_heatmap(self, heatmap_path: Path):
        """Display a heatmap image on the RIGHT region canvas."""
        try:
            from PIL import Image, ImageTk
            img = Image.open(heatmap_path).convert("RGB")
            heatmap_img = img
            bar_img = None
            if self._region_cached_pil is not None:
                source_w, source_h = self._region_cached_pil.size
                if img.height == source_h and img.width > source_w:
                    heatmap_img = img.crop((0, 0, source_w, img.height))
                    bar_img = img.crop((source_w, 0, img.width, img.height))
            if bar_img is None and img.width > 120:
                # scripts.heatmap.add_colorbar appends a fixed 100 px scale panel.
                bar_source_w = min(100, img.width - 1)
                heatmap_img = img.crop((0, 0, img.width - bar_source_w, img.height))
                bar_img = img.crop((img.width - bar_source_w, 0, img.width, img.height))
            cw = self.region_canvas_right.winfo_width() or 300
            ch = self.region_canvas_right.winfo_height() or 500
            reserved_bar_width = 76 if bar_img is not None else 0
            new_w, new_h = self._region_shared_display_size(
                heatmap_img.width, heatmap_img.height, reserve_right=reserved_bar_width
            )
            heatmap_img = heatmap_img.resize((new_w, new_h), Image.LANCZOS)
            bar_ref = None
            bar_w = 0
            bar_h = 0
            if bar_img is not None and bar_img.width > 0:
                natural_bar_w = max(1, int(bar_img.width * (new_h / max(1, bar_img.height))))
                bar_w = min(natural_bar_w, 72)
                bar_h = new_h
                bar_img = bar_img.resize((bar_w, bar_h), Image.LANCZOS)
                bar_ref = ImageTk.PhotoImage(bar_img)
            bar_gap = 1 if bar_ref is not None else 0
            if bar_ref is not None:
                offset_x = centered_region_group_offset(cw, new_w, bar_w, bar_gap)
            else:
                offset_x = centered_region_group_offset(cw, new_w)
            offset_y = max(2, int((ch - new_h) / 2))
            self.region_heatmap_ref = ImageTk.PhotoImage(heatmap_img)
            self.region_heatmap_bar_ref = bar_ref
            self.region_canvas_right.delete("all")
            self.region_canvas_right.create_image(offset_x, offset_y, anchor="nw", image=self.region_heatmap_ref)
            self._region_draw_image_frame(self.region_canvas_right, offset_x, offset_y, new_w, new_h)
            if bar_ref is not None and hasattr(self, "region_heatmap_bar_canvas"):
                self.region_heatmap_bar_canvas.delete("all")
                self._region_heatmap_bar_item_id = None
                self.region_heatmap_bar_canvas.place_forget()
                self.region_canvas_right.create_image(
                    offset_x + new_w + bar_gap, offset_y, anchor="nw", image=bar_ref
                )
            elif hasattr(self, "region_heatmap_bar_canvas"):
                self.region_heatmap_bar_canvas.delete("all")
                self._region_heatmap_bar_item_id = None
                self.region_heatmap_bar_canvas.place_forget()
            self._region_heatmap_showing = str(heatmap_path)
        except Exception:
            pass

    def _region_switch_tab(self, tab_name: str):
        """Switch the right-canvas tab between 'preview' and 'heatmap'."""
        if self.region_workflow_running:
            return
        if tab_name == self._region_right_tab:
            return
        # Prevent switching to heatmap if no heatmap exists yet
        if tab_name == "heatmap" and not self._region_heatmap_showing:
            return
        self._region_right_tab = tab_name
        # Update button styles
        if tab_name == "preview":
            self.region_tab_preview_btn.config(bg=Theme.ACCENT_DARK, fg=Theme.TEXT_ON_ACCENT)
            self.region_tab_heatmap_btn.config(
                bg=Theme.BUTTON,
                fg=Theme.TEXT if self._region_heatmap_showing else Theme.MUTED,
            )
            preview = getattr(self, "_region_preview_showing", None)
            if preview and Path(preview).exists():
                self._region_display_preview(Path(preview))
            else:
                if hasattr(self, "region_heatmap_bar_canvas"):
                    self.region_heatmap_bar_canvas.delete("all")
                    self._region_heatmap_bar_item_id = None
                    self.region_heatmap_bar_canvas.place_forget()
                self.region_canvas_right.delete("all")
        else:  # heatmap
            self.region_tab_heatmap_btn.config(bg=Theme.ACCENT_DARK, fg=Theme.TEXT_ON_ACCENT)
            self.region_tab_preview_btn.config(bg=Theme.BUTTON, fg=Theme.TEXT)
            heatmap = getattr(self, "_region_heatmap_showing", None)
            if heatmap and Path(heatmap).exists():
                self._region_display_heatmap(Path(heatmap))
            else:
                if hasattr(self, "region_heatmap_bar_canvas"):
                    self.region_heatmap_bar_canvas.delete("all")
                    self._region_heatmap_bar_item_id = None
                    self.region_heatmap_bar_canvas.place_forget()
                self.region_canvas_right.delete("all")

    def _build_import_tab(self):
        columns = Frame(self.import_tab, bg=Theme.BG)
        columns.pack(fill=BOTH, expand=True, pady=(0, 8))
        left = Frame(columns, bg=Theme.BG)
        left.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 18))
        right = Frame(columns, bg=Theme.BG, width=520)
        right.pack(side=LEFT, fill=BOTH, expand=True)
        right.pack_propagate(False)

        # ----- card 1: game + template setup -----
        card1 = self._card(left, "step_setup", step=1, side_pack={"fill": X, "pady": (0, 12)})
        self._responsive_label(card1, "step_game_hint", anchor="w", justify=LEFT,
                               min_wrap=280, margin=8, fg=Theme.MUTED,
                               font=(Theme.FONT_FAMILY, 9)).pack(fill=X, pady=(0, 8))
        game_row = Frame(card1, bg=Theme.PANEL)
        game_row.pack(fill=X)
        self._label(game_row, "game_profile", font=(Theme.FONT_FAMILY, 10, "bold")).pack(side=LEFT)
        self.import_game_combo = ttk.Combobox(game_row, values=list(PROFILES.keys()),
                                              textvariable=self.selected_game,
                                              state="readonly", width=8)
        self.import_game_combo.pack(side=LEFT, padx=10)
        self._style_combobox_popdown(self.import_game_combo)
        self._label(game_row, "pid", font=(Theme.FONT_FAMILY, 10, "bold")).pack(side=LEFT)
        self.import_pid_entry = Entry(game_row, textvariable=self.selected_pid, width=28)
        self.import_pid_entry.pack(side=LEFT, padx=10, ipady=4)
        self._button(game_row, "refresh", self.refresh_processes).pack(side=LEFT)

        Frame(card1, bg=Theme.BORDER, height=1).pack(fill=X, pady=(12, 10))
        self._responsive_label(card1, "step_template_hint", anchor="w", justify=LEFT,
                               min_wrap=280, margin=8, fg=Theme.MUTED,
                               font=(Theme.FONT_FAMILY, 9)).pack(fill=X, pady=(0, 8))
        template_row = Frame(card1, bg=Theme.PANEL)
        template_row.pack(fill=X)
        self._label(template_row, "layer_count", font=(Theme.FONT_FAMILY, 10, "bold")).pack(side=LEFT)
        self.layer_count_entry = Entry(template_row, textvariable=self.layer_count,
                                       width=14, font=(Theme.FONT_FAMILY, 14, "bold"))
        self.layer_count_entry.pack(side=LEFT, padx=12, ipady=6)
        self.readiness_frame = Frame(card1, bg=Theme.PANEL)
        self.readiness_frame.pack(fill=X, pady=(14, 0))
        self.readiness_labels = []
        for index in range(6):
            label = Label(
                self.readiness_frame, text="", anchor="w", justify=LEFT,
                bg=Theme.PANEL, fg=Theme.MUTED, font=(Theme.FONT_FAMILY, 9, "bold"),
            )
            row = index // 2
            column = index % 2
            label.grid(row=row, column=column, sticky="ew", padx=(0, 12), pady=3)
            self.readiness_frame.columnconfigure(column, weight=1)
            self.readiness_labels.append(label)
        self.readiness_frame.bind("<Configure>", self._resize_readiness_labels, add="+")

        # ----- card 3: JSON files -----
        card3 = self._card(
            left,
            "step_json",
            step=2,
            side_pack={"fill": BOTH, "expand": True, "pady": (0, 8)},
            header_action=("open_logs", self.open_import_logs),
        )
        self._responsive_label(card3, "step_json_hint", anchor="w", justify=LEFT,
                               min_wrap=260, margin=8, fg=Theme.MUTED,
                               font=(Theme.FONT_FAMILY, 9)).pack(fill=X, pady=(0, 10))
        row = Frame(card3, bg=Theme.PANEL)
        row.pack(fill=X)
        self._label(row, "json_files", font=(Theme.FONT_FAMILY, 10, "bold")).pack(side=LEFT)
        self._button(row, "add_json", self.add_json).pack(side=RIGHT)
        self._button(row, "remove_json", self.remove_selected_json).pack(side=RIGHT, padx=(8, 8))
        self._button(row, "use_outputs", self.use_generated_outputs).pack(side=RIGHT, padx=8)
        list_wrap = Frame(card3, bg=Theme.BORDER)
        list_wrap.pack(fill=BOTH, expand=True, pady=(10, 0))
        list_inner = Frame(list_wrap, bg=Theme.INPUT)
        list_inner.pack(fill=BOTH, expand=True, padx=1, pady=1)
        self.json_list = Listbox(list_inner, height=5, borderwidth=0, highlightthickness=0)
        self.json_list.pack(fill=BOTH, expand=True, padx=10, pady=8)
        self.json_list.bind("<<ListboxSelect>>", self._preview_selected_json)
        self._bind_list_context_menu(self.json_list, "json")
        self._register_drop_target(self.json_list, self._drop_json_files)
        self._update_json_empty_state()
        compat_label = self._responsive_variable_label(
            card3, self.compatibility_text, anchor="w", justify=LEFT,
            min_wrap=260, margin=8, bg=Theme.PANEL, fg=Theme.ACCENT_SOFT,
            font=(Theme.FONT_FAMILY, 9, "bold"),
        )
        compat_label.pack(fill=X, pady=(8, 0))

        # ----- right column: import CTA + preview -----
        card4 = self._card(right, "step_import", step=3)
        self._responsive_label(card4, "step_import_hint", anchor="w", justify=LEFT,
                               min_wrap=220, margin=8, fg=Theme.MUTED,
                               font=(Theme.FONT_FAMILY, 9)).pack(fill=X, pady=(0, 6))
        self._responsive_label(card4, "easy_import_hint", anchor="w", justify=LEFT,
                               min_wrap=220, margin=8, fg=Theme.SUBTLE,
                               font=(Theme.FONT_FAMILY, 9)).pack(fill=X, pady=2)
        self._responsive_label(card4, "admin_note", anchor="w", justify=LEFT,
                               min_wrap=220, margin=8, fg=Theme.WARN,
                               font=(Theme.FONT_FAMILY, 9)).pack(fill=X, pady=(2, 12))
        actions = Frame(card4, bg=Theme.PANEL)
        actions.pack(fill=X)
        self._primary_button(actions, "import_json", self.start_import, variant="accent").pack(
            side=LEFT, fill=X, expand=True, ipady=4)
        self.advanced_button = self._button(actions, "show_advanced", self.toggle_advanced)
        self.advanced_button.pack(side=LEFT, padx=(10, 0), ipady=4)

        self.advanced_frame = Frame(card4, bg=Theme.PANEL)
        self.advanced_frame_inner_built = False
        self._build_advanced_options(self.advanced_frame)

        preview_card = self._card(right, "import_preview", side_pack={"fill": BOTH, "expand": True})
        self._responsive_label(preview_card, "preview_accuracy_note", anchor="w", justify=LEFT,
                               min_wrap=220, margin=8, fg=Theme.WARN,
                               font=(Theme.FONT_FAMILY, 8)).pack(fill=X, pady=(0, 8))
        preview_inner = Frame(preview_card, bg=Theme.BORDER)
        preview_inner.pack(fill=BOTH, expand=True)
        self.import_preview_label = Label(
            preview_inner, text=tr(self.lang, "preview_hint"),
            bg=Theme.PREVIEW_BG, fg=Theme.MUTED, font=(Theme.FONT_FAMILY, 10),
        )
        self.import_preview_label.pack(fill=BOTH, expand=True, padx=1, pady=1)
        self.import_preview_label.bind("<Configure>", self._schedule_preview_refresh)

    def open_import_logs(self):
        self.active_log_scope = "import"
        self.import_log_status.set(self.status.get())
        self._show_import_log_modal("import")
        self._set_import_modal_running(self.import_running)

    def _build_full_shape_tab(self):
        columns = Frame(self.full_shape_tab, bg=Theme.BG)
        columns.pack(fill=BOTH, expand=True, pady=(0, 8))
        left = Frame(columns, bg=Theme.BG)
        right = Frame(columns, bg=Theme.BG, width=520)
        columns.grid_rowconfigure(0, weight=1)
        columns.grid_columnconfigure(0, weight=1, minsize=500)
        columns.grid_columnconfigure(1, weight=0, minsize=520)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
        right.grid(row=0, column=1, sticky="nsew")
        right.pack_propagate(False)

        intro_card = self._card(left, "full_shape_intro_title", side_pack={"fill": X, "pady": (0, 12)})
        self._responsive_label(
            intro_card, "full_shape_intro", anchor="w", justify=LEFT,
            min_wrap=280, margin=8, fg=Theme.WARN, font=(Theme.FONT_FAMILY, 9),
        ).pack(fill=X)

        session = self._card(left, "full_shape_session_title", step=1, side_pack={"fill": X, "pady": (0, 12)})
        self._responsive_label(
            session, "full_shape_session_hint", anchor="w", justify=LEFT,
            min_wrap=280, margin=8, fg=Theme.MUTED, font=(Theme.FONT_FAMILY, 9),
        ).pack(fill=X, pady=(0, 14))
        setup_grid = Frame(session, bg=Theme.PANEL)
        setup_grid.pack(fill=X)
        self._label(setup_grid, "layer_count", font=(Theme.FONT_FAMILY, 10, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        Entry(setup_grid, textvariable=self.full_shape_count, width=12, font=(Theme.FONT_FAMILY, 12)).grid(row=0, column=1, sticky="w", pady=4, ipady=4)
        self._button(setup_grid, "refresh", self.refresh_processes).grid(row=0, column=2, sticky="w", padx=(12, 0), pady=4)
        setup_grid.columnconfigure(3, weight=1)

        export_box = self._card(left, "full_shape_export_title", side_pack={"fill": X, "pady": (0, 12)})
        self._responsive_label(
            export_box, "full_shape_export_hint", anchor="w", justify=LEFT,
            min_wrap=280, margin=8, fg=Theme.MUTED, font=(Theme.FONT_FAMILY, 9),
        ).pack(fill=X, pady=(0, 12))
        export_actions = Frame(export_box, bg=Theme.PANEL)
        export_actions.pack(fill=X)
        self._primary_button(
            export_actions, "full_shape_export_button", self.start_full_shape_export,
            variant="accent", wraplength=520, justify="center",
        ).pack(
            side=LEFT, fill=X, expand=True, ipady=4
        )
        self._button(
            export_actions, "full_shape_open_folder", self.open_full_shape_folder,
            wraplength=160, justify="center",
        ).pack(
            side=LEFT, padx=(10, 0), ipady=4
        )

        notes_card = self._card(right, "full_shape_notes_title", side_pack={"fill": BOTH, "expand": True})
        notes = Text(notes_card, wrap="word", height=12, borderwidth=0, highlightthickness=0)
        notes.pack(fill=BOTH, expand=True, padx=0, pady=0)
        notes.insert(END, tr(self.lang, "full_shape_notes"))
        notes.config(state="disabled")
        self.full_shape_notes = notes

        report_row = Frame(notes_card, bg=Theme.PANEL)
        report_row.pack(fill=X, pady=(10, 0))
        self.full_shape_report_button = self._button(report_row, "export_full_shape_report", self.export_full_shape_report, state="disabled")
        self.full_shape_report_button.pack(fill=X)

    def _build_advanced_options(self, parent):
        Frame(parent, bg=Theme.BORDER, height=1).pack(fill=X, pady=(10, 8))
        Label(parent, text=tr(self.lang, "advanced_options").upper(), bg=Theme.PANEL,
              fg=Theme.SUBTLE, font=(Theme.FONT_FAMILY, 8, "bold"), anchor="w").pack(fill=X, pady=(0, 6))
        grid = Frame(parent, bg=Theme.PANEL)
        grid.pack(fill=X)
        self._field(grid, "manual_count", self.count_address, row=0)
        self._field(grid, "manual_table", self.table_address, row=1)
        btn = self._button(grid, "auto_locate", self.start_auto_locate)
        btn.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _build_tools_tab(self):
        form = Frame(self.tools_tab)
        form.pack(fill=X, padx=10, pady=10)
        self._field(form, "layer_count", self.layer_count, row=0)
        self._field(form, "snapshot_count", self.snapshot_count, row=1)
        self._field(form, "current_count", self.current_count, row=2)
        self._field(form, "table_address", self.inspect_table_value, row=3)
        runtime_entry = self._field(form, "runtime_folder", self.runtime_folder, row=4)
        runtime_entry.config(state="readonly")
        actions = Frame(self.tools_tab)
        actions.pack(fill=X, padx=10, pady=8)
        self._button(actions, "diagnose", self.start_diagnose).pack(side=LEFT)
        self._button(actions, "auto_locate", self.start_auto_locate).pack(side=LEFT, padx=6)
        self._button(actions, "save_snapshot", self.start_save_snapshot).pack(side=LEFT, padx=6)
        self._button(actions, "compare_snapshot", self.start_compare_snapshot).pack(side=LEFT, padx=6)
        self._button(actions, "inspect_table", self.start_inspect_table).pack(side=LEFT, padx=6)
        self._button(actions, "open_runtime_folder", self.open_runtime_folder).pack(side=LEFT, padx=6)

    def _build_tutorial_tab(self):
        tutorial_card = self._card(self.tutorial_tab, "tutorial_tab",
                                   side_pack={"fill": BOTH, "expand": True, "pady": (0, 8)})
        border = Frame(tutorial_card, bg=Theme.BORDER)
        border.pack(fill=BOTH, expand=True)
        self.tutorial_text = Text(border, wrap="word", borderwidth=0, highlightthickness=0)
        self.tutorial_text.pack(fill=BOTH, expand=True, padx=1, pady=1)
        self._update_tutorial()

    def _build_log(self, parent):
        log_area = Frame(parent, bg=Theme.BG)
        log_area.pack(side=BOTTOM, fill=X)
        self.log_area = log_area
        self.log_area_visible = True
        Frame(log_area, bg=Theme.BORDER, height=1).pack(fill=X, padx=24, pady=(8, 0))
        wrap = Frame(log_area, bg=Theme.BG)
        wrap.pack(fill=X, padx=24, pady=(10, 18))

        header = Frame(wrap, bg=Theme.BG)
        header.pack(fill=X)
        dots = Canvas(header, width=46, height=14, bg=Theme.BG, highlightthickness=0)
        dots.create_oval(2, 3, 12, 13, fill=Theme.DANGER, outline="")
        dots.create_oval(18, 3, 28, 13, fill=Theme.WARN, outline="")
        dots.create_oval(34, 3, 44, 13, fill=Theme.SUCCESS, outline="")
        dots.pack(side=LEFT, padx=(0, 10))
        self._label(header, "logs", anchor="w",
                    font=(Theme.FONT_FAMILY, 10, "bold")).pack(side=LEFT)
        self._button(header, "export_logs", self.export_detailed_log).pack(side=RIGHT)

        # Progress block: label + bar + text
        prog_block = Frame(header, bg=Theme.BG)
        prog_block.pack(side=LEFT, fill=X, expand=True, padx=(24, 12))
        self._label(prog_block, "progress", anchor="w",
                    font=(Theme.FONT_FAMILY, 8, "bold"), fg=Theme.SUBTLE).pack(side=LEFT, padx=(0, 8))
        Label(prog_block, textvariable=self.progress_percent, anchor="e",
              fg=Theme.ACCENT_SOFT, bg=Theme.BG,
              font=(Theme.FONT_FAMILY, 9, "bold"), width=4).pack(side=LEFT, padx=(0, 6))
        self.progress_bar = ttk.Progressbar(prog_block, mode="determinate", maximum=100,
                                            style="App.Horizontal.TProgressbar", length=160)
        self.progress_bar.pack(side=LEFT, padx=(0, 10))
        Label(prog_block, textvariable=self.progress_text, anchor="w",
              fg=Theme.ACCENT_SOFT, bg=Theme.BG,
              font=(Theme.FONT_FAMILY, 9)).pack(side=LEFT, fill=X, expand=True)

        border = Frame(wrap, bg=Theme.BORDER)
        border.pack(fill=X, pady=(10, 0))
        self.log = Text(border, height=6, borderwidth=0, highlightthickness=0)
        self.log.pack(fill=BOTH, expand=True, padx=1, pady=1)
        self._configure_log_text(self.log)

    def _set_log_area_visible(self, visible):
        if self.log_area is None:
            return
        if visible == self.log_area_visible:
            return
        if visible:
            self.log_area.pack(side=BOTTOM, fill=X)
        else:
            self.log_area.pack_forget()
        self.log_area_visible = visible

    def _configure_log_text(self, widget):
        widget.tag_configure("timestamp", foreground=Theme.SUBTLE)
        widget.tag_configure("info", foreground=Theme.TEXT)
        widget.tag_configure("warn", foreground=Theme.WARN)
        widget.tag_configure("error", foreground=Theme.DANGER)

    def _play_alert_sound(self):
        """Play the OS warning chime. Windows uses MessageBeep so the user gets
        the same audio cue as a native messagebox; other platforms fall back to
        the Tk bell."""
        try:
            if os.name == "nt":
                import winsound
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                return
        except Exception:
            pass
        try:
            self.root.bell()
        except Exception:
            pass

    def _ensure_window_in_taskbar(self, top):
        """Force a borderless Tk Toplevel to appear in the Windows taskbar.
        Tk windows with overrideredirect=True (or transient parents) get the
        WS_EX_TOOLWINDOW style by default, which hides them from the taskbar.
        Swap to WS_EX_APPWINDOW so the user can click the entry to bring the
        modal back to the front from any other app."""
        if os.name != "nt":
            return
        try:
            import ctypes
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            top.update_idletasks()
            hwnd_str = top.wm_frame()
            hwnd = int(hwnd_str, 16) if hwnd_str else top.winfo_id()
            user32 = ctypes.windll.user32
            exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            new_exstyle = (exstyle & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
            if new_exstyle != exstyle:
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_exstyle)
                # Hide/show forces Windows to re-register the taskbar entry.
                top.withdraw()
                top.after(10, top.deiconify)
        except Exception:
            pass

    def _show_themed_alert(self, title, message):
        """Borderless themed warning dialog. Uses grab_set for modal blocking
        (without the `-disabled` root toggle) so taskbar activation events
        still reach root and our _on_root_activated handler can raise the
        modal back to the front. Also registers a dedicated taskbar entry so
        the user can always click their way back to the alert."""
        self._play_alert_sound()
        top = Toplevel(self.root)
        top.withdraw()
        try:
            top.title(title)
        except Exception:
            pass
        top.configure(bg=Theme.BORDER)
        try:
            top.overrideredirect(True)
        except Exception:
            pass
        # Intentionally NOT calling top.transient(self.root) here: transient
        # makes Windows treat the window as a tool window and hides it from
        # the taskbar. We want an independent taskbar entry instead.

        shell = self._modal_shell(top)

        # Window-chrome header (matches the import log modal style).
        header = Frame(shell, bg=Theme.PANEL_HEADER)
        header.pack(fill=X)
        header_inner = Frame(header, bg=Theme.PANEL_HEADER)
        header_inner.pack(fill=X, padx=18, pady=12)
        dots = Canvas(header_inner, width=46, height=14, bg=Theme.PANEL_HEADER, highlightthickness=0)
        dots.create_oval(2, 3, 12, 13, fill=Theme.DANGER, outline="")
        dots.create_oval(18, 3, 28, 13, fill=Theme.WARN, outline="")
        dots.create_oval(34, 3, 44, 13, fill=Theme.SUCCESS, outline="")
        dots.pack(side=LEFT, padx=(0, 12))
        title_label = Label(header_inner, text=title, bg=Theme.PANEL_HEADER,
                            fg=Theme.TEXT, font=(Theme.FONT_FAMILY, 11, "bold"), anchor="w")
        title_label.pack(side=LEFT)
        Frame(shell, bg=Theme.BORDER, height=1).pack(fill=X)

        # Body: warning glyph + message.
        body = Frame(shell, bg=Theme.PANEL)
        body.pack(fill=BOTH, expand=True)
        body_inner = Frame(body, bg=Theme.PANEL)
        body_inner.pack(fill=BOTH, expand=True, padx=24, pady=22)
        icon = Canvas(body_inner, width=40, height=40, bg=Theme.PANEL, highlightthickness=0)
        icon.create_polygon(20, 4, 38, 36, 2, 36, fill="", outline=Theme.WARN, width=2)
        icon.create_text(20, 24, text="!", fill=Theme.WARN, font=(Theme.FONT_FAMILY, 16, "bold"))
        icon.pack(side=LEFT, padx=(0, 18), anchor="n")
        msg = Label(body_inner, text=message, bg=Theme.PANEL, fg=Theme.TEXT,
                    font=(Theme.FONT_FAMILY, 10), wraplength=380, justify=LEFT, anchor="w")
        msg.pack(side=LEFT, fill=BOTH, expand=True)

        # Footer with primary action.
        Frame(shell, bg=Theme.BORDER, height=1).pack(fill=X)
        footer = Frame(shell, bg=Theme.PANEL)
        footer.pack(fill=X)
        footer_inner = Frame(footer, bg=Theme.PANEL)
        footer_inner.pack(fill=X, padx=18, pady=12)

        def close(_event=None):
            self._deactivate_modal(top)
            try:
                top.destroy()
            except Exception:
                pass

        ok_btn = Button(footer_inner, text=tr(self.lang, "close"),
                        bg=Theme.ACCENT, fg=Theme.TEXT_ON_ACCENT,
                        activebackground=Theme.ACCENT_DARK, activeforeground=Theme.TEXT_ON_ACCENT,
                        relief="flat", borderwidth=0, padx=24, pady=6,
                        font=(Theme.FONT_FAMILY, 10, "bold"),
                        command=close, cursor="hand2")
        ok_btn.pack(side=RIGHT)
        self._attach_button_hover(ok_btn, hover_bg=Theme.ACCENT_DARK,
                                  base_bg=Theme.ACCENT, hover_border=Theme.ACCENT_DARK)

        top.protocol("WM_DELETE_WINDOW", close)
        self._bind_modal_drag(top, header)

        top.update_idletasks()
        w = max(460, top.winfo_reqwidth())
        h = max(200, top.winfo_reqheight())
        self._center_toplevel(top, w, h)
        top.deiconify()
        # Raise once above the root and register a taskbar entry so the user
        # can navigate back to the alert without making it global always-on-top.
        self._ensure_window_in_taskbar(top)
        # Full modal blocking: grab_set + disable the root window so no Tk
        # widget OR the OS-level window chrome can be interacted with until
        # the alert is dismissed. _activate_modal also tracks active_modal,
        # which lets _on_root_activated raise the alert when the user clicks
        # the main app's taskbar icon.
        self._activate_modal(top)
        ok_btn.focus_set()
        top.bind("<Return>", close)
        top.bind("<Escape>", close)
        try:
            self.root.wait_window(top)
        except Exception:
            pass
        # After dismissal, Windows leaves the (just re-enabled) root briefly
        # inactive. Force-activate it so the next keystroke lands in whichever
        # widget has focus instead of disappearing into the void.
        try:
            self.root.lift()
            self.root.focus_force()
            self.root.update_idletasks()
        except Exception:
            pass

    def _show_import_log_modal(self, scope=None):
        if scope in self.tab_log_entries:
            modal_scope = scope
        else:
            modal_scope = "full_shape" if self._active_log_scope() == "full_shape" or getattr(self, "current_section", None) == "full_shape" else "import"
        if self.import_log_modal is not None:
            try:
                if self.import_log_modal.winfo_exists():
                    self.import_log_modal.deiconify()
                    self._ensure_window_in_taskbar(self.import_log_modal)
                    self._activate_modal(self.import_log_modal)
                    self.import_log_modal_scope = modal_scope
                    self._populate_scoped_log_widget(self.import_modal_log, modal_scope)
                    self._refresh_import_modal_progress()
                    return
            except Exception:
                pass

        top = Toplevel(self.root)
        self.import_log_modal = top
        self.import_log_modal_scope = modal_scope
        top.withdraw()
        top.title(tr(self.lang, "logs"))
        top.configure(bg=Theme.BORDER)
        try:
            top.overrideredirect(True)
        except Exception:
            pass
        top.geometry("920x380")
        top.minsize(720, 300)
        # No transient(self.root): transient makes Windows treat the window as
        # a tool window and hides it from the taskbar. We register an explicit
        # taskbar entry below instead.

        def close_modal():
            self._deactivate_modal(top)
            self.import_log_modal = None
            self.import_log_modal_scope = None
            self.import_modal_log = None
            self.import_modal_progress = None
            try:
                top.destroy()
            except Exception:
                pass
            # Re-activate root so the next input lands cleanly.
            try:
                self.root.lift()
                self.root.focus_force()
                self.root.update_idletasks()
            except Exception:
                pass

        top.protocol("WM_DELETE_WINDOW", close_modal)
        shell = self._modal_shell(top)
        content_shell = Frame(shell, bg=Theme.BG)
        content_shell.pack(fill=BOTH, expand=True, padx=18, pady=16)

        header = Frame(content_shell, bg=Theme.BG)
        header.pack(fill=X)
        dots = Canvas(header, width=46, height=14, bg=Theme.BG, highlightthickness=0)
        dots.create_oval(2, 3, 12, 13, fill=Theme.DANGER, outline="")
        dots.create_oval(18, 3, 28, 13, fill=Theme.WARN, outline="")
        dots.create_oval(34, 3, 44, 13, fill=Theme.SUCCESS, outline="")
        dots.pack(side=LEFT, padx=(0, 10))
        title_label = self._label(header, "logs", anchor="w",
                                  font=(Theme.FONT_FAMILY, 11, "bold"))
        title_label.pack(side=LEFT)
        self._button(header, "close", close_modal).pack(side=RIGHT)
        self._button(header, "export_logs", self.export_detailed_log).pack(side=RIGHT, padx=(0, 8))

        drag_offset = {"x": 0, "y": 0}

        def start_drag(event):
            drag_offset["x"] = event.x_root - top.winfo_x()
            drag_offset["y"] = event.y_root - top.winfo_y()

        def drag_modal(event):
            top.geometry(f"+{event.x_root - drag_offset['x']}+{event.y_root - drag_offset['y']}")

        for drag_widget in (header, dots, title_label):
            drag_widget.bind("<ButtonPress-1>", start_drag, add="+")
            drag_widget.bind("<B1-Motion>", drag_modal, add="+")

        prog_block = Frame(header, bg=Theme.BG)
        prog_block.pack(side=LEFT, fill=X, expand=True, padx=(24, 12))
        self._label(prog_block, "progress", anchor="w",
                    font=(Theme.FONT_FAMILY, 8, "bold"), fg=Theme.SUBTLE).pack(side=LEFT, padx=(0, 8))
        Label(prog_block, textvariable=self.import_modal_percent, anchor="e",
              fg=Theme.ACCENT_SOFT, bg=Theme.BG,
              font=(Theme.FONT_FAMILY, 9, "bold"), width=4).pack(side=LEFT, padx=(0, 6))
        self.import_modal_progress = ttk.Progressbar(
            prog_block, mode="determinate", maximum=100, style="App.Horizontal.TProgressbar", length=180
        )
        self.import_modal_progress.pack(side=LEFT, padx=(0, 10))
        self.import_log_status.set(self.status.get())
        Label(prog_block, textvariable=self.import_log_status, anchor="w",
              fg=Theme.ACCENT_SOFT, bg=Theme.BG,
              font=(Theme.FONT_FAMILY, 9)).pack(side=LEFT, fill=X, expand=True)
        self._bind_modal_drag(top, header)

        border = Frame(content_shell, bg=Theme.BORDER)
        border.pack(fill=BOTH, expand=True, pady=(12, 0))
        self.import_modal_log = Text(border, borderwidth=0, highlightthickness=0)
        self.import_modal_log.pack(fill=BOTH, expand=True, padx=1, pady=1)
        self._configure_log_text(self.import_modal_log)
        self._populate_scoped_log_widget(self.import_modal_log, modal_scope)
        self._refresh_import_modal_progress()
        self._apply_dark_theme_recursive(top)
        self._center_toplevel(top, 920, 380)
        top.deiconify()
        self._ensure_window_in_taskbar(top)
        self._activate_modal(top)

    def _set_import_modal_running(self, running):
        progress = self.import_modal_progress
        if progress is None:
            return
        try:
            progress.configure(mode="determinate", maximum=100)
            self._refresh_import_modal_progress()
        except Exception:
            pass

    def _apply_import_modal_progress(self, value=None, text=None):
        progress_value = 0
        if value is not None:
            try:
                progress_value = max(0, min(100, int(round(float(value)))))
            except (TypeError, ValueError):
                progress_value = 0
        self.import_modal_percent.set(f"{progress_value}%")
        if text is not None:
            clean_text = re.sub(r"^\s*\d{1,3}%\s*[·|-]\s*", "", str(text)).strip()
            self.import_log_status.set(clean_text or "")
        progress = self.import_modal_progress
        if progress is not None:
            try:
                progress.configure(mode="determinate", maximum=100)
                progress["value"] = progress_value
            except Exception:
                pass

    def _refresh_import_modal_progress(self):
        scope = getattr(self, "import_log_modal_scope", None) or "import"
        progress = self.tab_log_progress.get(scope, {"value": 0, "text": self.status.get()})
        text = progress.get("text") or self.status.get()
        self._apply_import_modal_progress(progress.get("value", 0), text)

    def _append_import_modal_log(self, timestamp, text, tag):
        widget = self.import_modal_log
        if widget is None:
            return
        try:
            if not widget.winfo_exists():
                return
            widget.insert(END, f"[{timestamp}] ", ("timestamp",))
            widget.insert(END, f"{text}\n", (tag,))
            widget.see(END)
        except Exception:
            pass

    def _show_generate_log_modal(self):
        if self.generate_log_modal is not None:
            try:
                if self.generate_log_modal.winfo_exists():
                    self.generate_log_modal.deiconify()
                    self._ensure_window_in_taskbar(self.generate_log_modal)
                    self._activate_modal(self.generate_log_modal)
                    self._refresh_generate_modal_previews()
                    return
            except Exception:
                pass

        top = Toplevel(self.root)
        self.generate_log_modal = top
        top.withdraw()
        top.title(tr(self.lang, "generation_monitor"))
        top.configure(bg=Theme.BORDER)
        try:
            top.overrideredirect(True)
        except Exception:
            pass
        top.geometry("1120x720")
        top.minsize(900, 560)

        def close_modal():
            self._deactivate_modal(top)
            self.generate_log_modal = None
            self.generate_modal_log = None
            self.generate_modal_progress = None
            self.generate_modal_source_label = None
            self.generate_modal_preview_label = None
            self.generate_modal_source_image = None
            self.generate_modal_preview_image = None
            self.generate_modal_preview_shadow_path = None
            try:
                top.destroy()
            except Exception:
                pass
            try:
                self.root.lift()
                self.root.focus_force()
                self.root.update_idletasks()
            except Exception:
                pass

        top.protocol("WM_DELETE_WINDOW", close_modal)
        shell = self._modal_shell(top)
        content_shell = Frame(shell, bg=Theme.BG)
        content_shell.pack(fill=BOTH, expand=True, padx=18, pady=16)

        header = Frame(content_shell, bg=Theme.BG)
        header.pack(fill=X)
        dots = Canvas(header, width=46, height=14, bg=Theme.BG, highlightthickness=0)
        dots.create_oval(2, 3, 12, 13, fill=Theme.DANGER, outline="")
        dots.create_oval(18, 3, 28, 13, fill=Theme.WARN, outline="")
        dots.create_oval(34, 3, 44, 13, fill=Theme.SUCCESS, outline="")
        dots.pack(side=LEFT, padx=(0, 10))
        title_label = self._label(header, "generation_monitor", anchor="w", font=(Theme.FONT_FAMILY, 11, "bold"))
        title_label.pack(side=LEFT)
        self._button(header, "close", close_modal).pack(side=RIGHT)
        self._button(header, "export_logs", self.export_detailed_log).pack(side=RIGHT, padx=(0, 8))

        self._bind_modal_drag(top, header)

        preview_grid = Frame(content_shell, bg=Theme.BG)
        preview_grid.pack(fill=BOTH, expand=True, pady=(12, 0))
        preview_grid.grid_columnconfigure(0, weight=1, uniform="preview")
        preview_grid.grid_columnconfigure(1, weight=1, uniform="preview")
        preview_grid.grid_rowconfigure(1, weight=1)

        self._label(preview_grid, "generation_source_preview", bg=Theme.BG, fg=Theme.SUBTLE,
                    font=(Theme.FONT_FAMILY, 8, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=(0, 6))
        self._label(preview_grid, "generation_live_preview", bg=Theme.BG, fg=Theme.SUBTLE,
                    font=(Theme.FONT_FAMILY, 8, "bold"), anchor="w").grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=(0, 6))

        source_border = Frame(preview_grid, bg=Theme.BORDER)
        source_border.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        source_inner = Frame(source_border, bg=Theme.BG, width=480, height=360)
        source_inner.pack(fill=BOTH, expand=True, padx=1, pady=1)
        # Stop the inner frame from resizing to the Label's image: otherwise a
        # rendered image grows the label, which grows the frame, which grows
        # the window, which fires <Configure>, which re-renders at the new
        # bigger size — feedback loop that eats the whole modal.
        source_inner.pack_propagate(False)
        self.generate_modal_source_label = Label(
            source_inner, text=tr(self.lang, "preview_hint"), bg=Theme.BG, fg=Theme.MUTED,
            font=(Theme.FONT_FAMILY, 10),
        )
        self.generate_modal_source_label.pack(fill=BOTH, expand=True)
        source_inner.bind(
            "<Configure>", lambda _e: self._schedule_modal_preview_refresh()
        )

        preview_border = Frame(preview_grid, bg=Theme.BORDER)
        preview_border.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        preview_inner = Frame(preview_border, bg=Theme.BG, width=480, height=360)
        preview_inner.pack(fill=BOTH, expand=True, padx=1, pady=1)
        preview_inner.pack_propagate(False)
        self.generate_modal_preview_label = Label(
            preview_inner, text=tr(self.lang, "generation_preview_waiting"), bg=Theme.BG, fg=Theme.MUTED,
            font=(Theme.FONT_FAMILY, 10), wraplength=360, justify="center",
        )
        self.generate_modal_preview_label.pack(fill=BOTH, expand=True)
        preview_inner.bind(
            "<Configure>", lambda _e: self._schedule_modal_preview_refresh()
        )

        progress_band = Frame(content_shell, bg=Theme.BG)
        progress_band.pack(fill=X, pady=(12, 0))
        progress_inner = Frame(progress_band, bg=Theme.BG)
        progress_inner.pack(anchor="center")
        self._label(progress_inner, "progress", anchor="w",
                    font=(Theme.FONT_FAMILY, 8, "bold"), fg=Theme.SUBTLE).pack(side=LEFT, padx=(0, 8))
        Label(progress_inner, textvariable=self.generate_modal_percent, anchor="center",
              fg=Theme.ACCENT_SOFT, bg=Theme.BG,
              font=(Theme.FONT_FAMILY, 9, "bold"), justify="center", width=4).pack(side=LEFT, padx=(0, 8))
        self.generate_modal_progress = ttk.Progressbar(
            progress_inner, mode="determinate", maximum=100, style="App.Horizontal.TProgressbar", length=260
        )
        self.generate_modal_progress.pack(side=LEFT, padx=(0, 10))
        self.generate_modal_progress["value"] = self.generate_progress_value
        if not self.generate_modal_status.get():
            self.generate_modal_status.set(tr(self.lang, "generation_progress_idle"))
        self._set_generate_modal_progress(self.generate_progress_value, self.generate_modal_status.get())
        Label(progress_inner, textvariable=self.generate_modal_status, anchor="center",
              fg=Theme.ACCENT_SOFT, bg=Theme.BG,
              font=(Theme.FONT_FAMILY, 9, "bold"), justify="center").pack(side=LEFT)

        log_border = Frame(content_shell, bg=Theme.BORDER)
        log_border.pack(fill=BOTH, expand=True, pady=(12, 0))
        self.generate_modal_log = Text(log_border, height=9, borderwidth=0, highlightthickness=0)
        self.generate_modal_log.pack(fill=BOTH, expand=True, padx=1, pady=1)
        self._configure_log_text(self.generate_modal_log)
        self._populate_scoped_log_widget(self.generate_modal_log, "generate")

        self._apply_dark_theme_recursive(top)
        self._center_toplevel(top, 1120, 720)
        top.deiconify()
        self._ensure_window_in_taskbar(top)
        self._activate_modal(top)
        self._refresh_generate_modal_previews()

    def _generate_modal_bounds(self, label):
        if label is None:
            return 720, 480
        try:
            width = label.winfo_width()
            height = label.winfo_height()
        except Exception:
            width = height = 0
        if width <= 32 or height <= 32:
            return 720, 480
        return max(1, width), max(1, height)

    def _set_generate_modal_image(self, label, image_attr, data, empty_text):
        if label is None:
            return
        try:
            if data:
                image = PhotoImage(data=data)
                label.config(image=image, text="", bg=Theme.PREVIEW_BG)
                setattr(self, image_attr, image)
            else:
                label.config(image="", text=empty_text, bg=Theme.PREVIEW_BG)
                setattr(self, image_attr, None)
        except Exception:
            label.config(image="", text=empty_text, bg=Theme.PREVIEW_BG)
            setattr(self, image_attr, None)

    def _set_generate_modal_source(self, path):
        self.generate_modal_source_path = Path(path) if path else None
        if self.generate_modal_source_path is None:
            self._set_generate_modal_image(
                self.generate_modal_source_label, "generate_modal_source_image", None, tr(self.lang, "preview_hint")
            )
            return
        data = render_source_image_fit(self.generate_modal_source_path, self._generate_modal_bounds(self.generate_modal_source_label))
        self._set_generate_modal_image(
            self.generate_modal_source_label, "generate_modal_source_image", data, tr(self.lang, "preview_unavailable")
        )

    def _set_generate_modal_preview(self, path=None, data=None):
        if path is not None:
            self.generate_modal_preview_path = Path(path)
            self.generate_modal_preview_shadow_path = None
            data = render_source_image_fit(self.generate_modal_preview_path, self._generate_modal_bounds(self.generate_modal_preview_label))
        elif data:
            self.generate_modal_preview_path = None
            self.generate_modal_preview_shadow_path = None
        self._set_generate_modal_image(
            self.generate_modal_preview_label,
            "generate_modal_preview_image",
            data,
            tr(self.lang, "generation_preview_waiting"),
        )

    def _set_generate_modal_preview_shadow(self, path):
        self.generate_modal_preview_path = None
        self.generate_modal_preview_shadow_path = Path(path) if path else None
        if self.generate_modal_preview_shadow_path is None:
            self._set_generate_modal_image(
                self.generate_modal_preview_label,
                "generate_modal_preview_image",
                None,
                tr(self.lang, "generation_preview_waiting"),
            )
            return
        data = render_source_shadow_image(
            self.generate_modal_preview_shadow_path,
            self._generate_modal_bounds(self.generate_modal_preview_label),
        )
        self._set_generate_modal_image(
            self.generate_modal_preview_label,
            "generate_modal_preview_image",
            data,
            tr(self.lang, "generation_preview_waiting"),
        )

    def _refresh_generate_modal_previews(self):
        if self.generate_modal_source_label is not None and self.generate_modal_source_path is not None:
            self._set_generate_modal_source(self.generate_modal_source_path)
        if self.generate_modal_preview_label is not None and self.generate_modal_preview_path is not None:
            self._set_generate_modal_preview(self.generate_modal_preview_path)
        elif self.generate_modal_preview_label is not None and self.generate_modal_preview_shadow_path is not None:
            self._set_generate_modal_preview_shadow(self.generate_modal_preview_shadow_path)

    def _schedule_modal_preview_refresh(self, delay_ms=80):
        # Debounce Configure storms during a window resize.
        token = getattr(self, "_modal_preview_refresh_token", None)
        if token is not None:
            try:
                self.root.after_cancel(token)
            except Exception:
                pass
        self._modal_preview_refresh_token = self.root.after(
            delay_ms, self._refresh_generate_modal_previews
        )

    def _set_generate_modal_progress(self, value=None, text=None):
        if value is not None:
            try:
                self.generate_progress_value = max(0, min(100, int(round(float(value)))))
            except (TypeError, ValueError):
                pass
        self.generate_modal_percent.set(f"{self.generate_progress_value}%")
        if text is not None:
            clean_text = re.sub(r"^\s*\d{1,3}%\s*[·|-]\s*", "", str(text)).strip()
            if clean_text:
                self.generate_modal_status.set(clean_text)
            else:
                self.generate_modal_status.set("")
        if self.generate_modal_progress is not None:
            try:
                self.generate_modal_progress.configure(mode="determinate", maximum=100)
                self.generate_modal_progress["value"] = self.generate_progress_value
            except Exception:
                pass
        inline_bar = getattr(self, "generate_inline_progress", None)
        if inline_bar is not None:
            try:
                inline_bar.configure(mode="determinate", maximum=100)
                inline_bar["value"] = self.generate_progress_value
            except Exception:
                pass

    def _append_generate_modal_log(self, timestamp, text, tag):
        widget = self.generate_modal_log
        if widget is None:
            return
        try:
            if not widget.winfo_exists():
                return
            widget.insert(END, f"[{timestamp}] ", ("timestamp",))
            widget.insert(END, f"{text}\n", (tag,))
            widget.see(END)
        except Exception:
            pass

    def _field(self, parent, key, variable, row, values=None, readonly=False):
        self._label(parent, key, anchor="w").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=5)
        if values:
            widget = ttk.Combobox(parent, values=values, textvariable=variable, state="readonly" if readonly else "normal")
            self._style_combobox_popdown(widget)
        else:
            widget = Entry(parent, textvariable=variable)
        widget.grid(row=row, column=1, sticky="ew", pady=5)
        parent.columnconfigure(1, weight=1)
        return widget

    def _on_language(self, _event=None):
        self.lang = LANGUAGES.get(self.lang_combo.get(), "en")
        self._save_preferences()
        for widget, key, option in self.translated:
            try:
                widget.config(**{option: tr(self.lang, key)})
            except Exception:
                pass
        for nav_key, item in getattr(self, "nav_items", {}).items():
            try:
                item["label"].config(text=tr(self.lang, item["label_key"]))
            except Exception:
                pass
        if hasattr(self, "section_title"):
            self.section_title.config(text=self._section_title_text())
        if hasattr(self, "section_subtitle"):
            self.section_subtitle.config(text=self._section_subtitle_text())
        self._refresh_section_header_wrap()
        if self.photo is None:
            self.preview_label.config(text=tr(self.lang, "preview_hint"))
            if hasattr(self, "import_preview_label"):
                self.import_preview_label.config(text=tr(self.lang, "preview_hint"))
        self._update_json_empty_state()
        self._refresh_batch_queue_text()
        self._refresh_import_readiness()
        self._refresh_entry_placeholders()
        self._update_quality_summary()
        if hasattr(self, "region_image_label_var"):
            self._region_update_image_label()
        if hasattr(self, "region_tool_buttons"):
            self._region_set_tool(self.region_tool.get())
        if hasattr(self, "advanced_button"):
            self.advanced_button.config(text=tr(self.lang, "hide_advanced" if self.advanced_visible else "show_advanced"))
        if hasattr(self, "full_shape_notes"):
            try:
                self.full_shape_notes.config(state="normal")
                self.full_shape_notes.delete("1.0", END)
                self.full_shape_notes.insert(END, tr(self.lang, "full_shape_notes"))
                self.full_shape_notes.config(state="disabled")
            except Exception:
                pass
        if self.import_log_modal is not None:
            try:
                self.import_log_modal.title(tr(self.lang, "logs"))
                self.import_log_status.set(self.status.get())
            except Exception:
                pass
        if self.quality_settings_modal is not None:
            try:
                self.quality_settings_modal.title(tr(self.lang, "quality_settings"))
            except Exception:
                pass
        if self.market_modal is not None:
            try:
                self.market_modal.title(tr(self.lang, "market_title"))
                self._refresh_market_language()
            except Exception:
                pass
        self._refresh_profile_combo()
        self._refresh_runtime_location_log()
        self._update_tutorial()
        self.status.set(tr(self.lang, "ready"))

    def _update_tutorial(self):
        self.tutorial_text.config(state="normal")
        self.tutorial_text.delete("1.0", END)
        self.tutorial_text.insert(END, tr(self.lang, "tutorial"))
        self.tutorial_text.config(state="disabled")

    def _update_setting_description(self, _event=None):
        item = self._selected_setting()
        description = self._localized_profile_description(item) if item else ""
        if hasattr(self, "setting_description"):
            self.setting_description.config(text=description)
        if self.quality_modal_description is not None:
            try:
                self.quality_modal_description.config(text=description)
            except Exception:
                pass
        if item and self.use_custom_settings.get() != "1":
            values = item.get("values", {})
            self.custom_stop_at.set(values.get("stopAt", "3000"))
            self.custom_max_resolution.set(values.get("maxResolution", "1200"))
            self.custom_random_samples.set(values.get("randomSamples", "3000"))
            self.custom_mutated_samples.set(values.get("mutatedSamples", "1000"))
            self.custom_save_at.set(values.get("saveAt", values.get("stopAt", "3000")))
            self.custom_preprocess_mode.set(values.get("preprocessMode", "none"))
        self._update_quality_summary()

    def _format_layers_value(self, value):
        try:
            return str(int(str(value).replace(",", "").strip()))
        except Exception:
            return str(value or "").strip() or "-"

    def _current_quality_layers(self):
        if self.use_custom_settings.get() == "1":
            return self.custom_stop_at.get()
        item = self._selected_setting()
        if item:
            return item.get("values", {}).get("stopAt", self.custom_stop_at.get())
        return self.custom_stop_at.get()

    def _update_quality_summary(self):
        label = getattr(self, "quality_layers_label", None)
        if label is None:
            return
        layers = self._format_layers_value(self._current_quality_layers())
        label.config(text=tr(self.lang, "quality_layers_summary").format(layers=layers))

    def _sync_custom_state(self):
        state = "normal" if self.use_custom_settings.get() == "1" else "disabled"
        for entry in getattr(self, "custom_fields", []):
            entry.config(state=state)
        if state == "disabled":
            self._update_setting_description()

    def _effective_setting(self):
        setting = self._selected_setting()
        if not setting or self.use_custom_settings.get() != "1":
            return setting
        return write_custom_settings(setting, self._custom_values())

    def _custom_values(self):
        custom = {
            "stopAt": self.custom_stop_at.get(),
            "maxResolution": self.custom_max_resolution.get(),
            "randomSamples": self.custom_random_samples.get(),
            "mutatedSamples": self.custom_mutated_samples.get(),
            "saveAt": self.custom_save_at.get(),
            "preprocessMode": self.custom_preprocess_mode.get(),
        }
        if not custom["saveAt"] and custom["stopAt"]:
            custom["saveAt"] = custom["stopAt"]
        return custom

    def save_custom_preset(self):
        setting = self._selected_setting()
        if not setting:
            self.log_line(tr(self.lang, "log_no_quality_profile"))
            return
        USER_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = filedialog.asksaveasfilename(
            title=tr(self.lang, "save_custom_preset"),
            initialdir=str(USER_SETTINGS_DIR),
            initialfile=f"user-preset-{timestamp}.ini",
            defaultextension=".ini",
            filetypes=[("INI settings", "*.ini"), ("All files", "*.*")],
        )
        if not output:
            return
        try:
            saved_path = write_user_settings_preset(setting, self._custom_values(), output)
        except OSError as exc:
            self.log_line(tr(self.lang, "log_failed_save_preset").format(error=exc))
            return
        self._reload_settings(preferred_path=saved_path)
        self.log_line(tr(self.lang, "saved_preset").format(path=saved_path))

    def toggle_advanced(self):
        self.advanced_visible = not self.advanced_visible
        if self.advanced_visible:
            self.advanced_frame.pack(fill=X, pady=(10, 0))
        else:
            self.advanced_frame.pack_forget()
        self.advanced_button.config(text=tr(self.lang, "hide_advanced" if self.advanced_visible else "show_advanced"))

    def _selected_setting(self):
        label = self.selected_profile.get()
        for item in self.settings:
            if item["label"] == label or self._localized_profile_label(item) == label:
                return item
        match = re.match(r"\s*(\d+)\.", label or "")
        if match:
            for item in self.settings:
                if str(item.get("index")) == match.group(1):
                    return item
        return self.settings[0] if self.settings else None

    def _reload_settings(self, preferred_path=None):
        previous = preferred_path
        if previous is None:
            current = self._selected_setting()
            previous = current.get("path") if current else None
        try:
            previous_resolved = Path(previous).resolve() if previous else None
        except OSError:
            previous_resolved = None
        self.settings = load_settings()
        values = [self._localized_profile_label(item) for item in self.settings]
        if hasattr(self, "profile_combo"):
            self.profile_combo["values"] = values
        selected = None
        if previous_resolved:
            for item in self.settings:
                try:
                    if item["path"].resolve() == previous_resolved:
                        selected = self._localized_profile_label(item)
                        break
                except OSError:
                    pass
        if selected is None and values:
            selected = values[min(2, len(values) - 1)]
        self.selected_profile.set(selected or "")
        self._update_setting_description()

    def _render_lists(self):
        self.image_list.delete(0, END)
        for index, path in enumerate(self.images):
            status_key = getattr(self, "batch_queue_state", {}).get(str(path.resolve()), "")
            status = tr(self.lang, status_key) if status_key else ""
            prefix = f"[{status}] " if status else ""
            self.image_list.insert(END, prefix + str(path))
        self.json_list.delete(0, END)
        for path in self.json_files:
            self.json_list.insert(END, str(path))
        self._update_json_empty_state()
        self._refresh_batch_queue_text()
        self._refresh_import_readiness()

    def _update_json_empty_state(self):
        hint = getattr(self, "json_empty_hint", None)
        if hint is None:
            return
        if self.json_files:
            hint.pack_forget()
        else:
            hint.pack(anchor="w", padx=14, pady=(2, 8))

    def _register_drop_target(self, widget, handler):
        if DND_FILES is None or not hasattr(widget, "drop_target_register"):
            return
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", handler)
        except Exception:
            pass

    def _dropped_paths(self, event):
        try:
            values = self.root.tk.splitlist(event.data)
        except Exception:
            values = str(getattr(event, "data", "") or "").split()
        return [Path(value) for value in values if str(value).strip()]

    def _add_image_paths(self, paths):
        allowed = {".png", ".jpg", ".jpeg", ".bmp"}
        added = []
        rejected = []
        for path in paths:
            path = Path(path)
            if not path.exists() or path.suffix.lower() not in allowed:
                rejected.append(path)
                continue
            if path not in self.images:
                self.images.append(path)
                added.append(path)
                self._load_existing_checkpoints_for_image(path)
        self._render_lists()
        if added:
            self.show_source_preview(added[0])
            output_dir = self._selected_output_folder()
            existing_added = sum(1 for path in added if generated_jsons(path, output_dir))
            if existing_added:
                self.log_line(tr(self.lang, "cannot_resume_checkpoint"))
        if rejected:
            self.log_line(tr(self.lang, "drop_rejected_images").format(count=len(rejected)))
        return added

    def _add_manual_json_paths(self, paths):
        added = []
        rejected = []
        for path in paths:
            path = Path(path)
            if not path.exists() or path.suffix.lower() != ".json":
                rejected.append(path)
                continue
            if path not in self.json_files:
                self.json_files.append(path)
                added.append(path)
        self._render_lists()
        if added:
            self.show_json_preview(added[0])
        if rejected:
            self.log_line(tr(self.lang, "drop_rejected_json").format(count=len(rejected)))
        self._refresh_import_readiness()
        return added

    def _drop_images(self, event):
        self._add_image_paths(self._dropped_paths(event))
        return "break"

    def _drop_json_files(self, event):
        self._add_manual_json_paths(self._dropped_paths(event))
        return "break"

    def _bind_list_context_menu(self, listbox, kind):
        listbox.bind("<Button-3>", lambda event: self._show_list_context_menu(event, kind), add="+")
        listbox.bind("<Button-2>", lambda event: self._show_list_context_menu(event, kind), add="+")

    def _list_paths_for_kind(self, kind):
        if kind == "images":
            return self.images
        if kind == "json":
            return self.json_files
        return []

    def _listbox_for_kind(self, kind):
        if kind == "images":
            return getattr(self, "image_list", None)
        if kind == "json":
            return getattr(self, "json_list", None)
        return None

    def _show_list_context_menu(self, event, kind):
        listbox = self._listbox_for_kind(kind)
        paths = self._list_paths_for_kind(kind)
        if listbox is None or not paths:
            return "break"
        try:
            index = listbox.nearest(event.y)
        except Exception:
            return "break"
        if index < 0 or index >= len(paths):
            return "break"
        try:
            bbox = listbox.bbox(index)
        except Exception:
            bbox = None
        if not bbox:
            return "break"
        _x, row_y, _w, row_h = bbox
        if event.y < row_y or event.y > row_y + row_h:
            return "break"
        try:
            listbox.selection_clear(0, END)
            listbox.selection_set(index)
            listbox.activate(index)
        except Exception:
            pass
        if kind == "images":
            self._preview_selected_image()
        else:
            self._preview_selected_json()

        self._show_themed_context_menu(
            event.x_root,
            event.y_root,
            [
                (tr(self.lang, "context_open_location"), lambda: self._open_file_location(paths[index]), "normal"),
                (tr(self.lang, "context_delete_item"), lambda: self._delete_list_item(kind, index), "danger"),
            ],
        )
        return "break"

    def _show_themed_context_menu(self, x, y, actions):
        self._hide_themed_context_menu()
        top = Toplevel(self.root)
        self.context_menu = top
        top.withdraw()
        top.overrideredirect(True)
        top.configure(bg=Theme.BORDER)

        outer = Frame(top, bg=Theme.BORDER)
        outer.pack(fill=BOTH, expand=True)
        panel = Frame(outer, bg=Theme.PANEL)
        panel.pack(fill=BOTH, expand=True, padx=1, pady=1)

        def close(_event=None):
            self._hide_themed_context_menu()

        def run_action(callback):
            close()
            callback()

        for index, (label_text, callback, variant) in enumerate(actions):
            if index:
                Frame(panel, bg=Theme.BORDER, height=1).pack(fill=X, padx=8)
            row = Label(
                panel,
                text=label_text,
                bg=Theme.PANEL,
                fg=Theme.DANGER if variant == "danger" else Theme.TEXT,
                activebackground=Theme.BUTTON_HOVER,
                activeforeground=Theme.TEXT,
                anchor="w",
                padx=14,
                pady=9,
                font=(Theme.FONT_FAMILY, 9, "bold" if variant == "danger" else "normal"),
                cursor="hand2",
            )
            row.pack(fill=X)

            def on_enter(_event=None, widget=row, item_variant=variant):
                widget.configure(
                    bg=Theme.DANGER if item_variant == "danger" else Theme.ACCENT_DARK,
                    fg=Theme.TEXT_ON_ACCENT,
                )

            def on_leave(_event=None, widget=row, item_variant=variant):
                widget.configure(
                    bg=Theme.PANEL,
                    fg=Theme.DANGER if item_variant == "danger" else Theme.TEXT,
                )

            row.bind("<Enter>", on_enter, add="+")
            row.bind("<Leave>", on_leave, add="+")
            row.bind("<Button-1>", lambda _event, cb=callback: run_action(cb), add="+")

        top.update_idletasks()
        width = max(190, top.winfo_reqwidth())
        height = top.winfo_reqheight()
        screen_w = top.winfo_screenwidth()
        screen_h = top.winfo_screenheight()
        x = min(max(0, x), max(0, screen_w - width - 8))
        y = min(max(0, y), max(0, screen_h - height - 40))
        top.geometry(f"{width}x{height}+{x}+{y}")
        top.deiconify()
        top.lift()
        try:
            top.attributes("-topmost", True)
            top.after(250, lambda: self._clear_topmost(top))
        except Exception:
            pass
        try:
            top.focus_force()
        except Exception:
            pass
        top.bind("<Escape>", close, add="+")

        def close_if_outside(event):
            try:
                inside = (
                    top.winfo_rootx() <= event.x_root <= top.winfo_rootx() + top.winfo_width()
                    and top.winfo_rooty() <= event.y_root <= top.winfo_rooty() + top.winfo_height()
                )
            except Exception:
                inside = False
            if not inside:
                close()
                return "break"
            return None

        top.bind("<ButtonPress-1>", close_if_outside, add="+")
        top.bind("<ButtonPress-2>", close_if_outside, add="+")
        top.bind("<ButtonPress-3>", close_if_outside, add="+")
        try:
            top.grab_set()
        except Exception:
            pass

    def _hide_themed_context_menu(self):
        top = getattr(self, "context_menu", None)
        self.context_menu = None
        if top is None:
            return
        try:
            if top.winfo_exists():
                try:
                    top.grab_release()
                except Exception:
                    pass
                top.destroy()
        except Exception:
            pass
        modal = getattr(self, "active_modal", None)
        if modal is not None:
            try:
                if modal.winfo_exists():
                    modal.grab_set()
            except Exception:
                pass

    def _delete_list_item(self, kind, index):
        paths = self._list_paths_for_kind(kind)
        if index < 0 or index >= len(paths):
            return
        removed_path = Path(paths[index])
        try:
            del paths[index]
        except IndexError:
            return
        self._render_lists()
        if kind == "images":
            self._clear_preview_if_path(removed_path, import_only=False)
        else:
            self._clear_preview_if_path(removed_path, import_only=False)
            self._refresh_import_readiness()

    def _clear_preview_if_path(self, path, import_only=False):
        path = Path(path)
        request = getattr(self, "current_preview_request", None)
        if request and len(request) >= 2:
            try:
                if Path(request[1]).resolve() == path.resolve():
                    self.current_preview_request = None
            except Exception:
                self.current_preview_request = None
        if self.preview_resize_job is not None:
            try:
                self.root.after_cancel(self.preview_resize_job)
            except Exception:
                pass
            self.preview_resize_job = None
        self.photo = None
        targets = []
        if not import_only and hasattr(self, "preview_label"):
            targets.append(self.preview_label)
        if hasattr(self, "import_preview_label"):
            targets.append(self.import_preview_label)
        for label in targets:
            try:
                label.config(image="", text=tr(self.lang, "preview_hint"), bg=Theme.PREVIEW_BG)
                label.image = None
            except Exception:
                pass

    def _open_file_location(self, path):
        path = Path(path)
        try:
            if os.name == "nt" and path.exists():
                subprocess.Popen(f'explorer.exe /select,"{path}"')
                return
            folder = path.parent if path.parent.exists() else ROOT
            os.startfile(folder)
        except Exception as exc:
            self.log_line(tr(self.lang, "context_open_location_failed").format(error=exc))

    def _selected_json_path(self):
        if not hasattr(self, "json_list"):
            return self.json_files[0] if self.json_files else None
        selection = self.json_list.curselection()
        if selection:
            try:
                return self.json_files[selection[0]]
            except IndexError:
                return None
        return self.json_files[0] if self.json_files else None

    def _format_layer_fit(self, path):
        if not path:
            return tr(self.lang, "compat_no_json")
        try:
            from generator_backend import geometry_shape_count
            fit = layer_fit(geometry_shape_count(path), self.layer_count.get())
        except Exception:
            return tr(self.lang, "compat_unknown")
        try:
            return tr(self.lang, fit.message_key).format(
                json=fit.drawable_layers,
                template=fit.template_layers or 0,
                usable=fit.usable_layers or 0,
                recommended=fit.recommended_template_layers or 0,
            )
        except Exception:
            return tr(self.lang, fit.message_key)

    def _refresh_import_readiness(self):
        if hasattr(self, "compatibility_text"):
            self.compatibility_text.set(self._format_layer_fit(self._selected_json_path()))
        labels = getattr(self, "readiness_labels", None)
        if not labels:
            return
        pid = self.selected_pid_value()
        session = load_session_location()
        game = self.selected_game.get() or "fh6"
        layer_count = self.layer_count.get().strip()
        checks = readiness_checks(
            has_json=bool(self.json_files),
            template_layers=layer_count,
            has_process=bool(pid and self._pid_matches_game(pid, game)),
            is_admin=app_is_admin(),
            has_manual_addresses=bool(self.count_address.get().strip() or self.table_address.get().strip()),
            has_session=bool(session_matches_current_import(session, game, pid, layer_count)),
        )
        for label, (key, passed) in zip(labels, checks):
            label.config(
                text=f"{'OK' if passed else '--'}  {tr(self.lang, key)}",
                fg=Theme.SUCCESS if passed else Theme.MUTED,
            )
        self._resize_readiness_labels()

    def _resize_readiness_labels(self, event=None, immediate=False):
        labels = getattr(self, "readiness_labels", None)
        if not labels:
            return
        try:
            width = event.width if event is not None else self.readiness_frame.winfo_width()
        except Exception:
            width = 0
        bucket = self._layout_width_bucket(width)
        if not immediate:
            if bucket == self.readiness_resize_bucket:
                return
            self.readiness_resize_bucket = bucket
            if self.readiness_resize_job is not None:
                try:
                    self.root.after_cancel(self.readiness_resize_job)
                except Exception:
                    pass
            self.readiness_resize_job = self.root.after(
                LAYOUT_RESIZE_DEBOUNCE_MS,
                lambda: self._resize_readiness_labels(immediate=True),
            )
            return
        self.readiness_resize_job = None
        try:
            width = max(180, (self.readiness_frame.winfo_width() // 2) - 18)
        except Exception:
            width = 260
        for label in labels:
            try:
                label.configure(wraplength=width)
            except Exception:
                pass

    def _refresh_batch_queue_text(self):
        if not hasattr(self, "batch_queue_text"):
            return
        total = len(self.images)
        if not total:
            self.batch_queue_text.set(tr(self.lang, "queue_empty"))
            return
        states = getattr(self, "batch_queue_state", {})
        done = sum(1 for path in self.images if states.get(str(path.resolve())) == "queue_done")
        running = next((path.name for path in self.images if states.get(str(path.resolve())) == "queue_running"), "")
        if running:
            self.batch_queue_text.set(tr(self.lang, "queue_running_summary").format(done=done, total=total, image=running))
        else:
            self.batch_queue_text.set(tr(self.lang, "queue_summary").format(count=total))

    def _set_batch_queue_state(self, path, key):
        if not hasattr(self, "batch_queue_state"):
            self.batch_queue_state = {}
        self.batch_queue_state[str(Path(path).resolve())] = key
        self._render_lists()

    def _add_json_paths(self, paths):
        added = 0
        for output in best_geometry_jsons(paths):
            output = Path(output)
            if output not in self.outputs:
                self.outputs.append(output)
            if output not in self.json_files:
                self.json_files.append(output)
                added += 1
        return added

    def _selected_output_folder(self):
        folder = self.output_folder.get().strip()
        return Path(folder) if folder else None

    def _load_existing_checkpoints_for_image(self, image_path, log_to_queue=False):
        existing = best_geometry_jsons(generated_jsons(image_path, self._selected_output_folder()))
        if not existing:
            return 0
        added = self._add_json_paths(existing[:1])
        if added:
            message = tr(self.lang, "existing_checkpoints_found").format(image=Path(image_path).name, count=len(existing))
            if log_to_queue:
                self.queue.put(("log", message))
            else:
                self.log_line(message)
        return added

    def _queue_generated_outputs(self, image_path, before, output_dir=None):
        after = generated_jsons(image_path, output_dir)
        new_outputs = best_geometry_jsons([path for path in after if path.resolve() not in before])
        if not new_outputs and after:
            new_outputs = best_geometry_jsons(after[:1])
        for output in new_outputs:
            if output not in self.outputs:
                self.outputs.append(output)
            if output not in self.json_files:
                self.json_files.append(output)
            self.queue.put(("log", tr(self.lang, "log_generated_file").format(path=output)))
        return new_outputs

    def _runtime_location_message(self):
        return tr(self.lang, "runtime_location").format(runtime=ROOT / "runtime", probe=PROBE_DIR.parent)

    def _log_runtime_location(self):
        self.runtime_location_log_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._insert_log_entry(
            self._runtime_location_message(),
            timestamp=self.runtime_location_log_timestamp,
            line_tag="runtime_location_line",
        )
        self.log.see(END)

    def _refresh_runtime_location_log(self):
        try:
            ranges = self.log.tag_ranges("runtime_location_line")
        except Exception:
            return
        if not ranges:
            return
        start = str(ranges[0])
        end = str(ranges[-1])
        try:
            was_at_bottom = self.log.yview()[1] >= 0.99
        except Exception:
            was_at_bottom = True
        try:
            self.log.delete(start, end)
            self._insert_log_entry(
                self._runtime_location_message(),
                timestamp=self.runtime_location_log_timestamp,
                line_tag="runtime_location_line",
                record_detail=False,
                index=start,
            )
            if was_at_bottom:
                self.log.see(END)
        except Exception:
            pass

    def _log_message_tag(self, message):
        text = str(message)
        lowered = text.lower()
        if any(keyword in lowered for keyword in ("failed", "error", "cannot", "denied")):
            return "error"
        if any(keyword in lowered for keyword in ("warn", "stop", "recycled", "admin")):
            return "warn"
        return "info"

    def _active_log_scope(self, scope=None):
        if scope:
            return scope
        if self.active_log_scope:
            return self.active_log_scope
        section = getattr(self, "current_section", None)
        return section if section in self.tab_log_entries else "general"

    def _visible_log_scope(self):
        section = getattr(self, "current_section", None)
        return section if section in self.tab_log_entries else "general"

    def _record_scoped_log(self, scope, timestamp, text, tag):
        scope = self._active_log_scope(scope)
        entries = self.tab_log_entries.setdefault(scope, deque(maxlen=1200))
        entries.append((timestamp, text, tag))
        return scope

    def _progress_from_text(self, text):
        raw = str(text or "")
        for pattern in (
            r"(?:Generated layer|Saved JSON checkpoint|Writing layer)\s+(\d+)/(\d+)",
            r"Scanned\s+(\d+)/(\d+)\s+regions",
            r"Image\s+(\d+)/(\d+):",
        ):
            match = re.search(pattern, raw)
            if not match:
                continue
            current = int(match.group(1))
            total = int(match.group(2))
            if total > 0:
                return max(0, min(100, int(round(100 * current / total))))
        return None

    def _apply_log_progress(self, value=None, text=None):
        progress = 0
        if value is not None:
            try:
                progress = max(0, min(100, int(round(float(value)))))
            except (TypeError, ValueError):
                progress = 0
        self.progress_percent.set(f"{progress}%")
        if text is not None:
            clean_text = re.sub(r"^\s*\d{1,3}%\s*[·|-]\s*", "", str(text)).strip()
            self.progress_text.set(clean_text)
        if hasattr(self, "progress_bar"):
            try:
                self.progress_bar.configure(mode="determinate", maximum=100)
                self.progress_bar["value"] = progress
            except Exception:
                pass

    def _set_scoped_log_progress(self, scope, value=None, text=None):
        scope = self._active_log_scope(scope)
        progress = self.tab_log_progress.setdefault(scope, {"value": 0, "text": ""})
        if value is not None:
            try:
                progress["value"] = max(0, min(100, int(round(float(value)))))
            except (TypeError, ValueError):
                pass
        if text is not None:
            progress["text"] = str(text)
        if self.log_area_visible and scope == self._visible_log_scope():
            self._apply_log_progress(progress.get("value", 0), progress.get("text", ""))
        if scope == getattr(self, "import_log_modal_scope", None):
            self._apply_import_modal_progress(progress.get("value", 0), progress.get("text", ""))

    def _refresh_visible_log_progress(self):
        progress = self.tab_log_progress.get(self._visible_log_scope(), {"value": 0, "text": ""})
        self._apply_log_progress(progress.get("value", 0), progress.get("text", ""))

    def _populate_scoped_log_widget(self, widget, scope):
        if widget is None:
            return
        try:
            widget.delete("1.0", END)
            for timestamp, text, tag in self.tab_log_entries.get(scope, ()):
                widget.insert(END, f"[{timestamp}] ", ("timestamp",))
                widget.insert(END, f"{text}\n", (tag,))
            widget.see(END)
        except Exception:
            pass

    def _insert_log_entry(self, message, timestamp=None, line_tag=None, record_detail=True, index=END, scope=None):
        timestamp = timestamp or datetime.now().strftime("%H:%M:%S.%f")[:-3]
        if record_detail:
            self._record_detail(f"UI: {message}")
        text = str(message)
        msg_tag = self._log_message_tag(text)
        resolved_scope = self._record_scoped_log(scope, timestamp, text, msg_tag) if index == END else self._active_log_scope(scope)
        if index == END:
            progress = self._progress_from_text(text)
            if progress is not None:
                self._set_scoped_log_progress(resolved_scope, progress, text)
            if resolved_scope in ("import", "full_shape", "region") and resolved_scope == getattr(self, "import_log_modal_scope", None):
                self._append_import_modal_log(timestamp, text, msg_tag)
            if resolved_scope == "generate":
                self._append_generate_modal_log(timestamp, text, msg_tag)
            if (not self.log_area_visible) or resolved_scope != self._visible_log_scope():
                return None, None
        mark = "_log_insert_cursor"
        try:
            start = self.log.index(index)
            self.log.mark_set(mark, index)
            self.log.mark_gravity(mark, "right")
            timestamp_tags = ("timestamp", line_tag) if line_tag else ("timestamp",)
            message_tags = (msg_tag, line_tag) if line_tag else (msg_tag,)
            self.log.insert(mark, f"[{timestamp}] ", timestamp_tags)
            self.log.insert(mark, f"{text}\n", message_tags)
            end = self.log.index(mark)
            self.log.mark_unset(mark)
            return start, end
        except Exception:
            self.log.insert(END, f"[{timestamp}] {text}\n")
            return None, None

    def log_line(self, message, scope=None):
        self._insert_log_entry(message, scope=scope)
        if self.log_area_visible:
            self.log.see(END)

    def _record_detail(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        text = str(message).rstrip()
        entry = f"[{timestamp}] {text}"
        with self.detailed_log_lock:
            self.detailed_log_lines.append(entry)
            self.detailed_log_chars += len(entry) + 1
            while self.detailed_log_chars > DETAILED_LOG_MEMORY_LIMIT and self.detailed_log_lines:
                removed = self.detailed_log_lines.popleft()
                self.detailed_log_chars -= len(removed) + 1

    def _format_command(self, cmd):
        return subprocess.list2cmdline([str(item) for item in cmd])

    def _diagnostic_log_header(self):
        try:
            profile = self._selected_setting()
            profile_name = profile["label"] if profile else ""
            profile_path = str(profile["path"]) if profile else ""
        except Exception:
            profile_name = ""
            profile_path = ""
        selected_pid = self.selected_pid_value()
        generator_exists = GENERATOR_EXE.exists()
        lines = [
            f"{APP_DISPLAY_NAME} detailed log",
            f"Generated at: {datetime.now().isoformat(timespec='seconds')}",
            f"App version: {__version__}",
            f"Python: {sys.version.replace(os.linesep, ' ')}",
            f"Platform: {platform.platform()}",
            f"Root: {ROOT}",
            f"Generator: {GENERATOR_EXE} exists={generator_exists}",
            f"Selected game: {self.selected_game.get()}",
            f"Selected PID: {selected_pid}",
            f"Selected process label: {self.selected_pid.get()}",
            f"Template layer count: {self.layer_count.get()}",
            f"Manual count address: {self.count_address.get()}",
            f"Manual table address: {self.table_address.get()}",
            f"Quality profile: {profile_name}",
            f"Quality profile path: {profile_path}",
            f"Custom settings enabled: {self.use_custom_settings.get()}",
            f"Images: {len(self.images)}",
            *[f"  image: {path}" for path in self.images],
            f"JSON files: {len(self.json_files)}",
            *[f"  json: {path}" for path in self.json_files],
            f"Generated outputs: {len(self.outputs)}",
            *[f"  output: {path}" for path in self.outputs],
        ]
        if SESSION_PATH.exists():
            try:
                session_text = SESSION_PATH.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                session_text = f"<failed to read session: {exc}>"
            lines.extend(["Current FH6 session file:", session_text[:4000]])
        return "\n".join(lines).rstrip() + "\n"

    def _build_detailed_log_text(self):
        header = self._diagnostic_log_header()
        try:
            visible_log = self.log.get("1.0", END).strip()
        except Exception:
            visible_log = ""
        with self.detailed_log_lock:
            detail_log = "\n".join(self.detailed_log_lines).strip()
        body = "\n\n".join(
            section
            for section in (
                "=== Detailed Event Log ===\n" + (detail_log or "<empty>"),
                "=== Visible UI Log ===\n" + (visible_log or "<empty>"),
            )
            if section
        )
        marker = f"\n\n--- Log truncated to last {DETAILED_LOG_OUTPUT_LIMIT} characters ---\n"
        prefix = header + "\n"
        budget = DETAILED_LOG_OUTPUT_LIMIT - len(prefix)
        if budget <= len(marker):
            result = (prefix + body)[-DETAILED_LOG_OUTPUT_LIMIT:]
            return result if len(result) <= DETAILED_LOG_OUTPUT_LIMIT else result[-DETAILED_LOG_OUTPUT_LIMIT:]
        if len(body) > budget:
            body = marker + body[-(budget - len(marker)):]
        result = (prefix + body).rstrip()
        if len(result) >= DETAILED_LOG_OUTPUT_LIMIT:
            result = result[:DETAILED_LOG_OUTPUT_LIMIT - 1]
        return result + "\n"

    def export_detailed_log(self):
        initial = f"forza-painter-fh6-log-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
        output = filedialog.asksaveasfilename(
            title="Export detailed log",
            defaultextension=".txt",
            initialfile=initial,
            filetypes=[("Text log", "*.txt"), ("All files", "*.*")],
        )
        if not output:
            return
        text = self._build_detailed_log_text()
        try:
            Path(output).write_text(text, encoding="utf-8")
        except OSError as exc:
            self.log_line(tr(self.lang, "log_failed_export").format(error=exc))
            return
        self.log_line(tr(self.lang, "log_detailed_log_exported").format(path=output, chars=len(text), limit=DETAILED_LOG_OUTPUT_LIMIT))

    def export_full_shape_report(self):
        report_dir_text = str(self.full_shape_last_report_dir.get() or "").strip()
        if not report_dir_text:
            self.log_line(tr(self.lang, "full_shape_no_report"), scope="full_shape")
            return
        report_dir = Path(report_dir_text)
        if not report_dir.exists():
            self.log_line(tr(self.lang, "full_shape_no_report"), scope="full_shape")
            return
        output_path = filedialog.asksaveasfilename(
            title=tr(self.lang, "export_full_shape_report"),
            defaultextension=".zip",
            initialdir=str(report_dir.parent),
            initialfile=f"{report_dir.name}.zip",
            filetypes=[("ZIP", "*.zip"), ("All files", "*.*")],
        )
        if not output_path:
            return
        try:
            with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in report_dir.rglob("*"):
                    if path.is_file():
                        archive.write(path, path.relative_to(report_dir))
        except Exception as exc:
            self.log_line(f"{tr(self.lang, 'full_shape_report_export_failed')}: {exc}", scope="full_shape")
            return
        self.log_line(tr(self.lang, "full_shape_report_exported").format(path=output_path), scope="full_shape")

    def show_full_shape_failure_prompt(self, detail):
        message = tr(self.lang, "full_shape_failure_prompt").format(detail=detail)
        self.log_line(message, scope="full_shape")
        self._show_themed_alert(tr(self.lang, "full_shape_failed"), message)

    def start_update_check(self):
        if self.closed or self.update_check_started:
            return
        self.update_check_started = True
        threading.Thread(target=self._update_check_worker, daemon=True).start()

    def _update_check_worker(self):
        try:
            version_source = fetch_text_url(UPDATE_VERSION_URL)
            latest_version = parse_version_source(version_source)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            self.queue.put(("update_failed", str(exc)))
            return

        changelog = ""
        try:
            changelog = fetch_text_url(UPDATE_CHANGELOG_URL)
        except (OSError, urllib.error.URLError) as exc:
            self._record_detail(f"Update changelog fetch failed: {exc}")

        payload = {
            "current": __version__,
            "latest": latest_version,
            "changelog": extract_changelog_section(changelog, latest_version),
        }
        if version_key(latest_version) > version_key(__version__):
            self.queue.put(("update_available", payload))
        else:
            self.queue.put(("update_current", payload))

    def _set_update_indicator(self, text="", color=Theme.WARN):
        if hasattr(self, "update_indicator"):
            self.update_indicator.config(text=text, fg=color)

    def _handle_update_failed(self, error):
        self.update_state = {"status": "failed", "error": error}
        self._set_update_indicator("!", Theme.WARN)
        self.log_line(tr(self.lang, "log_update_check_failed").format(error=error))

    def _handle_update_current(self, payload):
        self.update_state = {"status": "current", **payload}
        self._set_update_indicator("")
        self._record_detail(f"Update check OK: latest={payload.get('latest')}")

    def _handle_update_available(self, payload):
        self.update_state = {"status": "available", **payload}
        self._set_update_indicator("!", Theme.ACCENT)
        self.log_line(tr(self.lang, "log_new_version_available").format(latest=payload.get('latest'), current=__version__))
        self.show_update_dialog(payload)

    def show_update_status(self, _event=None):
        status = self.update_state.get("status")
        if status == "failed":
            messagebox.showwarning(
                tr(self.lang, "update_check_failed_title"),
                tr(self.lang, "update_check_failed_message").format(error=self.update_state.get("error", "")),
                parent=self.root,
            )
        elif status == "available":
            self.show_update_dialog(self.update_state)

    def show_update_dialog(self, payload=None):
        payload = payload or self.update_state
        if self.update_dialog is not None and self.update_dialog.winfo_exists():
            self.update_dialog.lift()
            self.update_dialog.focus_force()
            return

        latest = payload.get("latest", "")
        changelog = payload.get("changelog") or "No changelog section was available."
        dialog = Toplevel(self.root)
        self.update_dialog = dialog
        dialog.title(tr(self.lang, "update_available_title"))
        dialog.configure(bg=Theme.BG)
        dialog.resizable(True, True)

        body = Frame(dialog, bg=Theme.BG)
        body.pack(fill=BOTH, expand=True, padx=16, pady=14)
        Label(
            body,
            text=tr(self.lang, "update_available_message").format(current=__version__, latest=latest),
            bg=Theme.BG,
            fg=Theme.TEXT,
            justify=LEFT,
            anchor="w",
            font=("Segoe UI", 11, "bold"),
        ).pack(fill=X, pady=(0, 10))
        Label(
            body,
            text=tr(self.lang, "changelog"),
            bg=Theme.BG,
            fg=Theme.MUTED,
            anchor="w",
        ).pack(fill=X)

        text_frame = Frame(body, bg=Theme.BG)
        text_frame.pack(fill=BOTH, expand=True, pady=(4, 12))
        changelog_text = Text(text_frame, width=80, height=18, wrap="word")
        scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=changelog_text.yview)
        changelog_text.configure(
            yscrollcommand=scrollbar.set,
            bg=Theme.INPUT,
            fg=Theme.TEXT,
            insertbackground=Theme.TEXT,
            selectbackground=Theme.ACCENT_DARK,
            selectforeground=Theme.TEXT_ON_ACCENT,
            highlightthickness=1,
            highlightbackground=Theme.BORDER,
            relief="flat",
            font=(Theme.FONT_FAMILY, 10),
        )
        changelog_text.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill="y")
        changelog_text.insert(END, changelog)
        changelog_text.config(state="disabled")

        actions = Frame(body, bg=Theme.BG)
        actions.pack(fill=X)

        def close_update_dialog():
            self.update_dialog = None
            dialog.destroy()

        def open_update_page():
            webbrowser.open(UPDATE_RELEASE_URL)
            close_update_dialog()

        later_btn = Button(
            actions,
            text=tr(self.lang, "update_later"),
            command=close_update_dialog,
            bg=Theme.BUTTON,
            fg=Theme.TEXT,
            activebackground=Theme.BUTTON_ACTIVE,
            activeforeground=Theme.TEXT,
            relief="flat",
            bd=0,
            padx=14,
            pady=7,
            cursor="hand2",
            font=(Theme.FONT_FAMILY, 10),
        )
        later_btn.pack(side=RIGHT)
        self._attach_button_hover(later_btn)
        open_btn = Button(
            actions,
            text=tr(self.lang, "update_open_page"),
            command=open_update_page,
            bg=Theme.ACCENT_DARK,
            fg=Theme.TEXT_ON_ACCENT,
            activebackground=Theme.ACCENT,
            activeforeground=Theme.TEXT_ON_ACCENT,
            relief="flat",
            bd=0,
            padx=14,
            pady=7,
            cursor="hand2",
            font=(Theme.FONT_FAMILY, 10, "bold"),
        )
        open_btn.pack(side=RIGHT, padx=(0, 8))

        def open_enter(_e=None):
            try:
                open_btn.configure(bg=Theme.ACCENT)
            except Exception:
                pass

        def open_leave(_e=None):
            try:
                open_btn.configure(bg=Theme.ACCENT_DARK)
            except Exception:
                pass

        open_btn.bind("<Enter>", open_enter, add="+")
        open_btn.bind("<Leave>", open_leave, add="+")

        dialog.protocol("WM_DELETE_WINDOW", close_update_dialog)

    def _reset_generation_eta(self):
        self.eta_samples.clear()
        self.eta_display_time = None
        self.eta_smoothed_seconds_per_layer = None
        self.eta_display_remaining = None
        self.eta_max_layer_seen = None
        self.eta_recycle_notice_active = False

    def _format_remaining_time(self, seconds):
        seconds = max(0, int(round(seconds)))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        suffix = tr(self.lang, "log_time_left")
        if hours:
            return f"{hours}h {minutes:02d}m {suffix}"
        if minutes:
            return f"{minutes}m {seconds:02d}s {suffix}"
        return f"{seconds}s {suffix}"

    def _progress_with_eta(self, friendly):
        match = re.match(r"Generated layer\s+(\d+)/(\d+)", friendly)
        if not match:
            return friendly
        current = int(match.group(1))
        total = int(match.group(2))
        progress_label = f"{tr(self.lang, 'log_layer')} {current}/{total}"
        eta_label = tr(self.lang, "log_eta")
        now = time.time()
        if self.eta_max_layer_seen is not None and current <= self.eta_max_layer_seen:
            if current < self.eta_max_layer_seen and not self.eta_recycle_notice_active:
                self.eta_recycle_notice_active = True
                return tr(self.lang, "generator_recycled_layers").format(max_layer=self.eta_max_layer_seen, total=total)
            return None
        self.eta_max_layer_seen = current
        self.eta_recycle_notice_active = False
        self.eta_samples.append((current, now))
        while len(self.eta_samples) > 1 and now - self.eta_samples[0][1] > ETA_MAX_HISTORY_SECONDS:
            self.eta_samples.popleft()

        remaining_layers = max(0, total - current)
        if remaining_layers == 0:
            self.eta_display_remaining = 0
            self.eta_display_time = now
            eta_time = datetime.fromtimestamp(now).strftime("%H:%M:%S")
            return f"{progress_label} | {eta_label} {eta_time} ({self._format_remaining_time(0)})"

        if len(self.eta_samples) < 2:
            return progress_label

        start_layer, start_time = self.eta_samples[0]
        for sample_layer, sample_time in self.eta_samples:
            if now - sample_time <= ETA_WINDOW_SECONDS:
                start_layer, start_time = sample_layer, sample_time
                break
        layer_delta = current - start_layer
        elapsed_seconds = now - start_time
        if layer_delta < ETA_MIN_WINDOW_LAYERS or elapsed_seconds < ETA_MIN_WINDOW_SECONDS:
            start_layer, start_time = self.eta_samples[0]
            layer_delta = current - start_layer
            elapsed_seconds = now - start_time
            if layer_delta < ETA_MIN_WINDOW_LAYERS or elapsed_seconds < ETA_MIN_WINDOW_SECONDS:
                return progress_label

        seconds_per_layer = elapsed_seconds / layer_delta
        self.eta_smoothed_seconds_per_layer = seconds_per_layer
        measured_remaining = seconds_per_layer * remaining_layers
        if self.eta_display_remaining is None:
            self.eta_display_remaining = measured_remaining
        else:
            elapsed_since_display = now - self.eta_display_time if self.eta_display_time is not None else 0
            expected_remaining = max(0, self.eta_display_remaining - elapsed_since_display)
            if measured_remaining < expected_remaining * 0.65 or measured_remaining > expected_remaining * 1.6:
                self.eta_display_remaining = measured_remaining
            else:
                self.eta_display_remaining = expected_remaining * 0.35 + measured_remaining * 0.65
        self.eta_display_time = now
        remaining_seconds = self.eta_display_remaining
        eta_time = datetime.fromtimestamp(now + remaining_seconds).strftime("%H:%M:%S")
        return f"{progress_label} | {eta_label} {eta_time} ({self._format_remaining_time(remaining_seconds)})"

    def friendly_generator_line(self, line):
        text = (line or "").strip()
        if not text:
            return None
        progress = re.match(r"\[(\d+)/(\d+)\]\s+(.*)", text)
        if progress:
            current, total, detail = progress.groups()
            if "Added rotated ellipse" in detail:
                return f"Generated layer {current}/{total}"
            if "Saved geometry checkpoint" in detail:
                return f"Saved JSON checkpoint {current}/{total}"
            if "Saved preview snapshot" in detail:
                return f"Updated preview {current}/{total}"
            if "Step completed" in detail:
                return None
            return None
        if text.startswith("Loaded image:"):
            return text
        if text.startswith("Settings:"):
            return text
        if text.startswith("OpenCL: Selected device"):
            return text
        if text.startswith("Scoring mode:"):
            return text
        if text in ("FINISHED",):
            return text
        if "error" in text.lower() or "failed" in text.lower() or "panic" in text.lower():
            return text
        return None

    def _localize_generator_line(self, friendly):
        """Replace hardcoded English prefixes from the native generator with translated ones."""
        if not friendly:
            return friendly
        for english_prefix, key in (
            ("Saved JSON checkpoint", "log_checkpoint"),
            ("Updated preview", "log_preview"),
            ("Loaded image:", "log_loaded_image"),
            ("Settings:", "log_settings"),
            ("OpenCL: Selected device", "log_opencl"),
            ("Scoring mode:", "log_scoring_mode"),
        ):
            if friendly.startswith(english_prefix):
                remainder = friendly[len(english_prefix):]
                if remainder and not remainder.startswith(" "):
                    remainder = " " + remainder
                return tr(self.lang, key) + remainder
        if friendly == "FINISHED":
            return tr(self.lang, "log_finished").upper()
        return friendly

    def queue_generator_message(self, friendly, last_message):
        if not friendly or friendly == last_message:
            return last_message
        if friendly.startswith("Generated layer "):
            match = re.match(r"Generated layer\s+(\d+)/(\d+)", friendly)
            message = self._progress_with_eta(friendly)
            if not message:
                return last_message
            if match:
                current = int(match.group(1))
                total = int(match.group(2))
                value = int(round(100 * current / max(1, total)))
                self.queue.put(("generation_progress", {"value": value, "text": message}))
            self.queue.put(("progress", message))
            self.queue.put(("log", message))
            return friendly
        if friendly == "FINISHED":
            message = self._localize_generator_line(friendly)
            self.queue.put(("generation_progress", {"value": 100, "text": message}))
            self.queue.put(("progress", message))
        self.queue.put(("log", self._localize_generator_line(friendly)))
        return friendly

    def _int_setting(self, setting, key, default=0):
        try:
            return int(str(setting.get("values", {}).get(key, default)).strip())
        except (TypeError, ValueError):
            return default

    def _log_generation_load_warning(self, setting):
        stop_at = self._int_setting(setting, "stopAt")
        random_samples = self._int_setting(setting, "randomSamples")
        mutated_samples = self._int_setting(setting, "mutatedSamples")
        max_resolution = self._int_setting(setting, "maxResolution")
        if random_samples >= 200000 or mutated_samples >= 8000 or max_resolution >= 2000:
            self.queue.put((
                "log",
                "High quality generation selected: "
                f"layers={stop_at}, randomSamples={random_samples}, "
                f"mutatedSamples={mutated_samples}, maxResolution={max_resolution}. "
                "The first layer can take a long time before progress appears.",
            ))

    def _generator_exit_message(self, returncode):
        if returncode in (3221225477, -1073741819):
            return tr(self.lang, "log_gpu_access_violation")
        if returncode == 3221226505:
            return tr(self.lang, "log_gpu_stack_overrun")
        return tr(self.lang, "log_generator_exit").format(code=returncode)

    def add_images(self):
        files = filedialog.askopenfilenames(
            title="Choose images",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")],
        )
        self._add_image_paths([Path(item) for item in files])

    def remove_selected_image(self):
        selection = list(self.image_list.curselection())
        if not selection:
            self.log_line(tr(self.lang, "no_image_selected"))
            return
        removed_paths = []
        for index in sorted(selection, reverse=True):
            try:
                removed_paths.append(Path(self.images[index]))
                del self.images[index]
            except IndexError:
                pass
        self._render_lists()
        for path in removed_paths:
            self._clear_preview_if_path(path, import_only=False)

    def _unique_preset_destination(self, source_path):
        USER_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        stem = source_path.stem
        suffix = source_path.suffix or ".ini"
        candidate = USER_SETTINGS_DIR / f"{stem}{suffix}"
        index = 2
        while candidate.exists():
            candidate = USER_SETTINGS_DIR / f"{stem} ({index}){suffix}"
            index += 1
        return candidate

    def import_preset(self):
        files = filedialog.askopenfilenames(
            title="Import generator preset",
            filetypes=[("INI settings", "*.ini"), ("All files", "*.*")],
        )
        imported = []
        for item in files:
            source = Path(item)
            if not source.exists():
                continue
            destination = self._unique_preset_destination(source)
            try:
                shutil.copy2(source, destination)
                imported.append(destination)
            except OSError as exc:
                self.log_line(tr(self.lang, "log_failed_import_preset").format(source=source, error=exc))
        if imported:
            self._reload_settings(preferred_path=imported[-1])
            self.log_line(tr(self.lang, "imported_presets").format(count=len(imported)))

    def open_preset_folder(self):
        USER_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(USER_SETTINGS_DIR)

    def open_market_modal(self):
        top = self.market_modal
        try:
            if top is not None and top.winfo_exists():
                top.deiconify()
                self._ensure_window_in_taskbar(top)
                self._activate_modal(top)
                return
        except Exception:
            pass

        top = Toplevel(self.root)
        self.market_modal = top
        top.withdraw()
        top.title(tr(self.lang, "market_title"))
        top.configure(bg=Theme.BORDER)
        try:
            top.overrideredirect(True)
        except Exception:
            pass
        top.geometry("1120x860")
        top.minsize(980, 760)
        top.protocol("WM_DELETE_WINDOW", self._hide_market_modal)

        shell = self._modal_shell(top)
        content = Frame(shell, bg=Theme.BG)
        content.pack(fill=BOTH, expand=True, padx=18, pady=16)

        header = Frame(content, bg=Theme.BG)
        header.pack(fill=X, pady=(0, 12))
        title = self._label(header, "market_title", anchor="w", font=(Theme.FONT_FAMILY, 14, "bold"))
        title.pack(side=LEFT)
        self._button(header, "close", self._hide_market_modal).pack(side=RIGHT)
        self.market_open_button = self._button(header, "market_open_website", self.open_selected_market_website)
        self.market_open_button.pack(side=RIGHT, padx=(0, 8))
        self._bind_modal_drag(top, header)

        modebar = Frame(content, bg=Theme.BG)
        modebar.pack(fill=X, pady=(0, 10))
        for view, key in (
            ("browse", "market_view_browse"),
            ("downloaded", "market_view_downloaded"),
            ("recent", "market_view_recent"),
        ):
            self._market_view_button(modebar, view, key).pack(side=LEFT, padx=(0, 8), ipady=4)
        self._refresh_market_view_buttons()

        toolbar = Frame(content, bg=Theme.BG)
        toolbar.pack(fill=X, pady=(0, 10))
        self._label(toolbar, "market_search", bg=Theme.BG, fg=Theme.SUBTLE,
                    font=(Theme.FONT_FAMILY, 9, "bold")).pack(side=LEFT, padx=(0, 8))
        search_wrap = Frame(toolbar, bg=Theme.BORDER)
        search_wrap.pack(side=LEFT, fill=X, expand=True)
        search_entry = Entry(search_wrap, textvariable=self.market_search)
        search_entry.pack(fill=X, padx=1, pady=1, ipady=5)
        search_entry.bind("<Return>", lambda _e: self.refresh_market_items())
        self._button(toolbar, "market_refresh", self.refresh_market_items).pack(side=LEFT, padx=(10, 0), ipady=4)

        filterbar = Frame(content, bg=Theme.BG)
        filterbar.pack(fill=X, pady=(0, 10))
        self._label(filterbar, "market_sort", bg=Theme.BG, fg=Theme.SUBTLE,
                    font=(Theme.FONT_FAMILY, 9, "bold")).pack(side=LEFT, padx=(0, 8))
        self.market_sort_combo = ThemedDropdown(filterbar, values=self._market_sort_labels(), width=18)
        if not self.market_sort.get():
            self.market_sort.set(self._market_sort_labels()[0])
        self.market_sort_combo.set(self.market_sort.get())
        self.market_sort_combo.pack(side=LEFT, padx=(0, 12))
        self.market_sort_combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_market_filters())

        self._label(filterbar, "market_layers_filter", bg=Theme.BG, fg=Theme.SUBTLE,
                    font=(Theme.FONT_FAMILY, 9, "bold")).pack(side=LEFT, padx=(0, 8))

        min_wrap = Frame(filterbar, bg=Theme.BORDER, width=72, height=30)
        min_wrap.pack(side=LEFT, padx=(0, 8))
        min_wrap.pack_propagate(False)
        min_entry = Entry(min_wrap, textvariable=self.market_layer_min, width=7, justify="center")
        min_entry.pack(fill=X, padx=1, pady=1, ipady=4)
        min_entry.bind("<Return>", lambda _e: self._apply_market_filters())
        self._entry_with_placeholder(min_entry, self.market_layer_min, "market_layers_min")

        max_wrap = Frame(filterbar, bg=Theme.BORDER, width=72, height=30)
        max_wrap.pack(side=LEFT, padx=(0, 12))
        max_wrap.pack_propagate(False)
        max_entry = Entry(max_wrap, textvariable=self.market_layer_max, width=7, justify="center")
        max_entry.pack(fill=X, padx=1, pady=1, ipady=4)
        max_entry.bind("<Return>", lambda _e: self._apply_market_filters())
        self._entry_with_placeholder(max_entry, self.market_layer_max, "market_layers_max")
        self._button(filterbar, "market_apply_filters", self._apply_market_filters).pack(side=LEFT, padx=(0, 12), ipady=3)

        fit_check = Checkbutton(
            filterbar,
            text=tr(self.lang, "market_fit_template"),
            variable=self.market_fit_template_only,
            onvalue="1",
            offvalue="0",
            command=self._apply_market_filters,
            bg=Theme.BG,
            fg=Theme.TEXT,
            activebackground=Theme.BG,
            activeforeground=Theme.TEXT,
            selectcolor=Theme.INPUT,
            font=(Theme.FONT_FAMILY, 9, "bold"),
        )
        fit_check.pack(side=LEFT)
        self.translated.append((fit_check, "market_fit_template", "text"))

        body_border = Frame(content, bg=Theme.BORDER)
        body_border.pack(fill=BOTH, expand=True)
        body = Frame(body_border, bg=Theme.PANEL)
        body.pack(fill=BOTH, expand=True, padx=1, pady=1)

        body_inner = Frame(body, bg=Theme.PANEL)
        body_inner.pack(fill=BOTH, expand=True, padx=10, pady=10)

        list_panel = Frame(body_inner, bg=Theme.PANEL, width=430)
        list_panel.pack(side=LEFT, fill=BOTH, padx=(0, 12))
        list_panel.pack_propagate(False)
        list_header = Frame(list_panel, bg=Theme.PANEL)
        list_header.pack(fill=X, pady=(0, 8))
        self._label(list_header, "market_presets", bg=Theme.PANEL, fg=Theme.TEXT,
                    font=(Theme.FONT_FAMILY, 10, "bold")).pack(side=LEFT)
        self.market_count_label = Label(
            list_header, text="", bg=Theme.PANEL, fg=Theme.SUBTLE,
            font=(Theme.FONT_FAMILY, 9, "bold"),
        )
        self.market_count_label.pack(side=RIGHT)
        self.market_list = Listbox(list_panel, height=12, borderwidth=0, highlightthickness=0)
        self.market_list.pack(fill=BOTH, expand=True)
        self.market_list.bind("<<ListboxSelect>>", self._update_market_status)
        self.market_list.bind("<ButtonPress-3>", self._show_market_context_menu)
        self.market_list.bind("<ButtonPress-2>", self._show_market_context_menu)
        self.market_list.bind("<ButtonRelease-3>", lambda _event: "break")
        self.market_list.bind("<ButtonRelease-2>", lambda _event: "break")

        detail_panel = Frame(body_inner, bg=Theme.PANEL)
        detail_panel.pack(side=LEFT, fill=BOTH, expand=True)

        preview_panel = Frame(detail_panel, bg=Theme.BORDER, height=300)
        preview_panel.pack(fill=X, pady=(0, 12))
        preview_panel.pack_propagate(False)
        preview_inner = Frame(preview_panel, bg=Theme.PREVIEW_BG)
        preview_inner.pack(fill=BOTH, expand=True, padx=1, pady=1)
        self.market_preview_label = Label(
            preview_inner, text=tr(self.lang, "market_preview_hint"),
            bg=Theme.PREVIEW_BG, fg=Theme.MUTED,
            anchor="center", justify="center",
            wraplength=430, font=(Theme.FONT_FAMILY, 10),
        )
        self.market_preview_label.pack(fill=BOTH, expand=True, padx=8, pady=8)

        detail_actions = Frame(detail_panel, bg=Theme.PANEL)
        detail_actions.pack(side=BOTTOM, fill=X, pady=(12, 0))
        Label(
            detail_actions, textvariable=self.market_notice,
            bg=Theme.PANEL, fg=Theme.MUTED,
            anchor="w", justify=LEFT,
            wraplength=300,
            font=(Theme.FONT_FAMILY, 9),
        ).pack(side=LEFT, fill=X, expand=True, padx=(0, 12))
        button_box = Frame(detail_actions, bg=Theme.PANEL, width=180, height=42)
        button_box.pack(side=RIGHT)
        button_box.pack_propagate(False)
        self.market_download_button = self._primary_button(
            button_box, "market_download", self.download_selected_market_json,
            variant="accent", padx=8, pady=7,
            font=(Theme.FONT_FAMILY, 10, "bold"),
            wraplength=150, justify="center",
        )
        self.market_download_button.pack(fill=BOTH, expand=True)

        detail_card_border = Frame(detail_panel, bg=Theme.BORDER)
        detail_card_border.pack(fill=BOTH, expand=True)
        detail_card = Frame(detail_card_border, bg=Theme.PANEL_ALT)
        detail_card.pack(fill=BOTH, expand=True, padx=1, pady=1)
        detail_content = Frame(detail_card, bg=Theme.PANEL_ALT)
        detail_content.pack(fill=BOTH, expand=True, padx=14, pady=12)
        Label(detail_content, textvariable=self.market_detail_title, bg=Theme.PANEL_ALT, fg=Theme.TEXT,
              anchor="w", justify=LEFT, wraplength=490,
              font=(Theme.FONT_FAMILY, 13, "bold")).pack(fill=X)
        Label(detail_content, textvariable=self.market_detail_author, bg=Theme.PANEL_ALT, fg=Theme.MUTED,
              anchor="w", font=(Theme.FONT_FAMILY, 9)).pack(fill=X, pady=(3, 8))

        metrics = Frame(detail_content, bg=Theme.PANEL_ALT)
        metrics.pack(fill=X, pady=(0, 8))
        self._market_metric(metrics, "market_layers_label", self.market_detail_layers).pack(side=LEFT, fill=X, expand=True, padx=(0, 6))
        self._market_metric(metrics, "market_stats_label", self.market_detail_stats).pack(side=LEFT, fill=X, expand=True, padx=(6, 0))

        Label(detail_content, textvariable=self.market_detail_tags, bg=Theme.PANEL_ALT, fg=Theme.ACCENT_SOFT,
              anchor="w", justify=LEFT, wraplength=490,
              font=(Theme.FONT_FAMILY, 9, "bold")).pack(fill=X, pady=(0, 8))
        desc_border = Frame(detail_content, bg=Theme.BORDER, height=104)
        desc_border.pack(fill=X)
        desc_border.pack_propagate(False)
        desc_inner = Frame(desc_border, bg=Theme.INPUT)
        desc_inner.pack(fill=BOTH, expand=True, padx=1, pady=1)
        self.market_description_text = Text(desc_inner, wrap="word", height=5, borderwidth=0, highlightthickness=0)
        self.market_description_text.pack(side=LEFT, fill=BOTH, expand=True)
        desc_scroll = ttk.Scrollbar(desc_inner, orient="vertical", command=self.market_description_text.yview)
        desc_scroll.pack(side=RIGHT, fill=Y)
        self.market_description_text.configure(yscrollcommand=desc_scroll.set)
        self.market_description_text.configure(
            bg=Theme.INPUT, fg=Theme.MUTED,
            insertbackground=Theme.TEXT,
            selectbackground=Theme.ACCENT_DARK,
            selectforeground=Theme.TEXT_ON_ACCENT,
            font=(Theme.FONT_FAMILY, 9),
            state="disabled",
        )

        self._apply_dark_theme_recursive(top)
        self._refresh_market_view_buttons()
        self._center_toplevel(top, 1120, 860)
        top.deiconify()
        self._ensure_window_in_taskbar(top)
        self._activate_modal(top)
        search_entry.focus_set()
        self.refresh_market_items()

    def _hide_market_modal(self):
        top = self.market_modal
        if top is None:
            return
        try:
            if top.winfo_exists():
                self._deactivate_modal(top)
                top.withdraw()
        except Exception:
            self._deactivate_modal(top)
            self.market_modal = None

    def _market_metric(self, parent, label_key, variable):
        outer = Frame(parent, bg=Theme.BORDER, height=92)
        outer.pack_propagate(False)
        inner = Frame(outer, bg=Theme.PANEL_ALT)
        inner.pack(fill=BOTH, expand=True, padx=1, pady=1)
        self._label(inner, label_key, bg=Theme.PANEL_ALT, fg=Theme.SUBTLE,
                    font=(Theme.FONT_FAMILY, 8, "bold"), anchor="w").pack(fill=X, padx=10, pady=(7, 0))
        value_label = Label(
            inner, textvariable=variable, bg=Theme.PANEL_ALT, fg=Theme.TEXT,
            font=(Theme.FONT_FAMILY, 10, "bold"), anchor="nw", justify=LEFT,
            wraplength=240,
        )
        value_label.pack(fill=BOTH, expand=True, padx=10, pady=(2, 10))

        def resize_metric(event=None):
            try:
                value_label.configure(wraplength=max(180, inner.winfo_width() - 22))
            except Exception:
                pass

        inner.bind("<Configure>", resize_metric, add="+")
        value_label.after_idle(resize_metric)
        return outer

    def _market_view_button(self, parent, view, label_key):
        button = Button(
            parent,
            text=tr(self.lang, label_key),
            command=lambda: self._set_market_view(view),
            bg=Theme.BUTTON,
            fg=Theme.TEXT,
            activebackground=Theme.BUTTON_ACTIVE,
            activeforeground=Theme.TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=Theme.BORDER,
            padx=14,
            pady=5,
            font=(Theme.FONT_FAMILY, 10, "bold"),
            cursor="hand2",
        )
        self.market_view_buttons[view] = (button, label_key)
        return button

    def _set_market_view(self, view):
        self.market_view.set(view)
        self._refresh_market_view_buttons()
        if view == "browse" and not self.market_all_items:
            self.refresh_market_items()
            return
        self._apply_market_filters()

    def _refresh_market_view_buttons(self):
        active = self.market_view.get() or "browse"
        for view, (button, label_key) in self.market_view_buttons.items():
            selected = view == active
            try:
                button.config(
                    text=tr(self.lang, label_key),
                    bg=Theme.ACCENT_DARK if selected else Theme.BUTTON,
                    fg=Theme.TEXT_ON_ACCENT if selected else Theme.TEXT,
                    highlightbackground=Theme.ACCENT if selected else Theme.BORDER,
                )
            except Exception:
                pass

    def _market_download_folder(self):
        return ROOT / "runtime" / "market-downloads"

    def _market_download_index_path(self):
        return self._market_download_folder() / "index.json"

    def _load_market_download_index(self):
        path = self._market_download_index_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_market_download_index(self, data):
        path = self._market_download_index_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _market_index_item_from_download(self, item, detail, geometry, output, reused=False):
        detail = detail if isinstance(detail, dict) else {}
        geometry = geometry if isinstance(geometry, dict) else {}
        title = detail.get("title") or item.get("title") or item.get("id") or Path(output).stem
        author = detail.get("author") or item.get("author") or {}
        if isinstance(author, str):
            author = {"displayName": author}
        layers = geometry.get("defaultLayers") or detail.get("defaultLayers") or item.get("defaultLayers")
        template = geometry.get("recommendedTemplateLayers") or detail.get("recommendedTemplateLayers") or item.get("recommendedTemplateLayers")
        now = datetime.now().isoformat(timespec="seconds")
        return {
            "id": str(item.get("id") or detail.get("id") or Path(output).stem),
            "title": title,
            "author": author,
            "description": detail.get("description") or item.get("description") or "",
            "tags": detail.get("tags") or item.get("tags") or [],
            "defaultLayers": layers,
            "recommendedTemplateLayers": template,
            "downloads": detail.get("downloads", item.get("downloads", 0)),
            "likes": detail.get("likes", item.get("likes", 0)),
            "comments": detail.get("comments", item.get("comments", 0)),
            "previewUrl": detail.get("previewUrl") or item.get("previewUrl"),
            "localPath": str(output),
            "downloadedAt": now,
            "reused": bool(reused),
        }

    def _remember_market_download(self, item, detail, geometry, output, reused=False):
        entry = self._market_index_item_from_download(item, detail, geometry, output, reused=reused)
        data = self._load_market_download_index()
        previous = data.get(entry["id"])
        if isinstance(previous, dict):
            entry["downloadedAt"] = previous.get("downloadedAt") or entry["downloadedAt"]
        data[entry["id"]] = entry
        self._save_market_download_index(data)
        return entry

    def _mark_market_imported_path(self, path):
        try:
            target = str(Path(path).resolve()).lower()
        except Exception:
            return
        data = self._load_market_download_index()
        changed = False
        now = datetime.now().isoformat(timespec="seconds")
        for item_id, entry in data.items():
            if not isinstance(entry, dict):
                continue
            try:
                local_path = str(Path(entry.get("localPath") or "").resolve()).lower()
            except Exception:
                continue
            if local_path == target:
                entry["importedAt"] = now
                entry["usedAt"] = now
                data[item_id] = entry
                changed = True
        if changed:
            self._save_market_download_index(data)

    def _market_downloaded_items(self):
        data = self._load_market_download_index()
        items = []
        changed = False
        for key, entry in list(data.items()):
            if not isinstance(entry, dict):
                data.pop(key, None)
                changed = True
                continue
            local_path = Path(entry.get("localPath") or "")
            if local_path.is_file() and local_path.suffix.lower() == ".json":
                items.append(entry)
            else:
                data.pop(key, None)
                changed = True
        if changed:
            self._save_market_download_index(data)
        return items

    def _local_market_path_for_item(self, item):
        local_path_value = str((item or {}).get("localPath") or "").strip()
        if local_path_value:
            local_path = Path(local_path_value)
            if local_path.is_file() and local_path.suffix.lower() == ".json":
                return local_path
        item_id = str((item or {}).get("id") or "")
        entry = self._load_market_download_index().get(item_id)
        if isinstance(entry, dict):
            local_path_value = str(entry.get("localPath") or "").strip()
            if local_path_value:
                local_path = Path(local_path_value)
                if local_path.is_file() and local_path.suffix.lower() == ".json":
                    return local_path
        return None

    def _market_source_items(self):
        view = self.market_view.get() or "browse"
        if view in ("downloaded", "recent"):
            items = self._market_downloaded_items()
            if view == "recent":
                items = [item for item in items if item.get("importedAt")]
                items.sort(key=lambda item: item.get("importedAt") or item.get("usedAt") or "", reverse=True)
            return items
        return list(getattr(self, "market_all_items", []) or [])

    MARKET_SORT_KEYS = (
        "market_sort_popular",
        "market_sort_downloads",
        "market_sort_newest",
        "market_sort_layers_high",
        "market_sort_layers_low",
        "market_sort_title",
    )

    def _market_sort_labels(self):
        return [tr(self.lang, key) for key in self.MARKET_SORT_KEYS]

    def _market_sort_key(self):
        current = ""
        try:
            current = self.market_sort_combo.get()
        except Exception:
            current = self.market_sort.get()
        labels = self._market_sort_labels()
        for key, label in zip(self.MARKET_SORT_KEYS, labels):
            if current == label:
                return key
        return self.MARKET_SORT_KEYS[0]

    def _refresh_market_sort_values(self):
        if not hasattr(self, "market_sort_combo"):
            return
        previous_key = self._market_sort_key()
        labels = self._market_sort_labels()
        self.market_sort_combo["values"] = labels
        selected = tr(self.lang, previous_key)
        self.market_sort.set(selected)
        self.market_sort_combo.set(selected)

    def _market_int(self, value, default=0):
        try:
            return int(float(str(value or "").strip()))
        except (TypeError, ValueError):
            return default

    def _market_layer_count(self, item):
        return self._market_int(item.get("defaultLayers") or item.get("layers"), 0)

    def _market_template_count(self, item):
        return self._market_int(item.get("recommendedTemplateLayers") or item.get("templateLayers"), 0)

    def _market_filter_number(self, value):
        text = str(value or "").strip()
        if not text:
            return None
        placeholders = {tr(self.lang, "market_layers_min"), tr(self.lang, "market_layers_max")}
        if text in placeholders:
            return None
        parsed = self._market_int(text, -1)
        return parsed if parsed >= 0 else None

    def _market_item_date_key(self, item):
        for key in ("createdAt", "updatedAt", "publishedAt"):
            value = item.get(key)
            if value:
                return str(value)
        return str(item.get("id") or "")

    def _sort_market_items(self, items):
        if self.market_view.get() == "recent":
            items = sorted(items, key=lambda item: item.get("importedAt") or "", reverse=True)
            if self._market_sort_key() == self.MARKET_SORT_KEYS[0]:
                return items
        sort_key = self._market_sort_key()
        if sort_key == "market_sort_downloads":
            return sorted(items, key=lambda item: self._market_int(item.get("downloads")), reverse=True)
        if sort_key == "market_sort_newest":
            return sorted(items, key=self._market_item_date_key, reverse=True)
        if sort_key == "market_sort_layers_high":
            return sorted(items, key=self._market_layer_count, reverse=True)
        if sort_key == "market_sort_layers_low":
            return sorted(items, key=self._market_layer_count)
        if sort_key == "market_sort_title":
            return sorted(items, key=lambda item: str(item.get("title") or item.get("id") or "").casefold())
        return sorted(
            items,
            key=lambda item: (
                self._market_int(item.get("downloads")),
                self._market_int(item.get("likes")),
                self._market_int(item.get("comments")),
            ),
            reverse=True,
        )

    def _schedule_market_filter_apply(self):
        if self.market_filter_job is not None:
            try:
                self.root.after_cancel(self.market_filter_job)
            except Exception:
                pass
        self.market_filter_job = self.root.after(220, self._apply_market_filters)

    def _apply_market_filters(self):
        self.market_filter_job = None
        all_items = self._market_source_items()
        min_layers = self._market_filter_number(self.market_layer_min.get())
        max_layers = self._market_filter_number(self.market_layer_max.get())
        template_limit = self._market_filter_number(self.layer_count.get())
        fit_only = self.market_fit_template_only.get() == "1"
        filtered = []
        for item in all_items:
            layers = self._market_layer_count(item)
            if min_layers is not None and layers < min_layers:
                continue
            if max_layers is not None and layers > max_layers:
                continue
            if fit_only and template_limit is not None:
                recommended = self._market_template_count(item) or layers + 4
                if recommended > template_limit:
                    continue
            filtered.append(item)
        self._render_market_list(self._sort_market_items(filtered), total_count=len(all_items))

    def _set_market_notice(self, key=None, **payload):
        self.market_notice_key = key
        self.market_notice_payload = dict(payload or {})
        if not key:
            self.market_notice.set("")
            return
        message = tr(self.lang, key)
        if payload:
            try:
                message = message.format(**payload)
            except Exception:
                pass
        self.market_notice.set(message)

    def _refresh_market_language(self):
        self._refresh_market_sort_values()
        self._refresh_market_view_buttons()
        if hasattr(self, "market_count_label") and self.market_count_label is not None:
            self._update_market_count_label(len(self.market_items), len(self._market_source_items()))
        item = self._selected_market_item()
        if item:
            self._set_market_detail_text(item)
        else:
            self._clear_market_details()
        if self.market_notice_key:
            self._set_market_notice(self.market_notice_key, **self.market_notice_payload)
        if self.market_preview_label is not None and getattr(self.market_preview_label, "image", None) is None:
            self.market_preview_label.config(text=tr(self.lang, "market_preview_hint"))
        self._apply_market_filters()

    def _market_api_url(self, path, query=None):
        base = MARKET_URL.rstrip("/")
        if not path.startswith("/"):
            path = "/" + path
        url = base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        return url

    def _market_request(self, path, query=None):
        request = urllib.request.Request(
            self._market_api_url(path, query),
            headers={"User-Agent": f"{APP_DISPLAY_NAME}/{__version__}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))

    def refresh_market_items(self):
        self._set_market_notice("market_loading")
        if self.market_list is not None:
            self.market_list.delete(0, END)
        if hasattr(self, "market_count_label"):
            self.market_count_label.config(text="")
        self._clear_market_details()
        if self.market_preview_label is not None:
            self.market_preview_item_id = None
            self.market_preview_label.config(image="", text=tr(self.lang, "market_preview_hint"))
            self.market_preview_label.image = None
        search = self._normalized_market_search(self.market_search.get())
        threading.Thread(target=self._market_load_worker, args=(search,), daemon=True).start()

    def _market_load_worker(self, search):
        try:
            search_text = search.get("text") if isinstance(search, dict) else search
            query = {"sort": "popular"}
            if search_text:
                query["q"] = search_text
            payload = self._market_request("/api/market/items", query)
            items = payload.get("items", [])
            if search_text:
                fallback = self._market_request("/api/market/items", {"sort": "popular"}).get("items", [])
                matched = self._filter_market_items(fallback, search)
                if isinstance(search, dict) and search.get("tag_only"):
                    items = matched
                else:
                    seen = {item.get("id") for item in items}
                    items.extend(item for item in matched if item.get("id") not in seen)
            self.queue.put(("market_items", items))
        except Exception as exc:
            self.queue.put(("market_error", str(exc)))

    def _normalized_market_search(self, value):
        text = str(value or "").strip()
        is_tag = text.startswith("#")
        while text.startswith("#"):
            text = text[1:].strip()
        if is_tag:
            return {"text": text, "tag_only": True}
        return text

    def _filter_market_items(self, items, search):
        tag_only = isinstance(search, dict) and search.get("tag_only")
        needle = str(search.get("text") if isinstance(search, dict) else search or "").casefold()
        if not needle:
            return list(items or [])
        matched = []
        for item in items or []:
            tags = [str(tag) for tag in (item.get("tags") or [])]
            if tag_only:
                if any(needle == tag.casefold() for tag in tags):
                    matched.append(item)
                continue
            author = (item.get("author") or {}).get("displayName") or ""
            haystack = " ".join([
                str(item.get("title") or ""),
                str(item.get("description") or ""),
                author,
                " ".join(tags),
            ]).casefold()
            if needle in haystack:
                matched.append(item)
        return matched

    def _render_market_items(self, items):
        self.market_all_items = list(items or [])
        self._apply_market_filters()

    def _update_market_count_label(self, visible_count, total_count=None):
        if not hasattr(self, "market_count_label"):
            return
        total_count = visible_count if total_count is None else total_count
        if total_count != visible_count:
            text = tr(self.lang, "market_count_filtered").format(count=visible_count, total=total_count)
        else:
            text = tr(self.lang, "market_count").format(count=visible_count)
        self.market_count_label.config(text=text)

    def _render_market_list(self, items, total_count=None):
        self.market_items = list(items or [])
        if self.market_list is None:
            return
        self.market_list.delete(0, END)
        self._update_market_count_label(len(self.market_items), total_count)
        for item in self.market_items:
            author = (item.get("author") or {}).get("displayName") or "?"
            title = item.get("title") or item.get("id") or "Untitled"
            layers = item.get("defaultLayers", "-")
            downloads = item.get("downloads", 0)
            self.market_list.insert(
                END,
                f"{self._clip_market_text(title, 26)}  ·  {layers}  ·  {downloads} dl  ·  {self._clip_market_text(author, 14)}",
            )
        if self.market_items:
            self.market_list.selection_set(0)
            self.market_list.activate(0)
            self._update_market_status()
        else:
            self._set_market_notice("market_filter_empty" if total_count else self._market_empty_notice_key())
            self._clear_market_details()
            if self.market_preview_label is not None:
                self.market_preview_item_id = None
                self.market_preview_label.config(image="", text=tr(self.lang, "market_preview_hint"))
                self.market_preview_label.image = None

    def _market_empty_notice_key(self):
        view = self.market_view.get() or "browse"
        if view == "downloaded":
            return "market_downloaded_empty"
        if view == "recent":
            return "market_recent_empty"
        return "market_empty"

    def _selected_market_item(self):
        if self.market_list is None:
            return None
        selection = self.market_list.curselection()
        if not selection:
            return None
        try:
            return self.market_items[selection[0]]
        except IndexError:
            return None

    def _show_market_context_menu(self, event):
        listbox = self.market_list
        if listbox is None or not self.market_items:
            return "break"
        try:
            index = listbox.nearest(event.y)
        except Exception:
            return "break"
        if index < 0 or index >= len(self.market_items):
            return "break"
        try:
            bbox = listbox.bbox(index)
        except Exception:
            bbox = None
        if not bbox:
            return "break"
        _x, row_y, _w, row_h = bbox
        if event.y < row_y or event.y > row_y + row_h:
            return "break"

        item = self.market_items[index]
        local_path = self._local_market_path_for_item(item)
        if local_path is None:
            return "break"

        try:
            current_selection = listbox.curselection()
            already_selected = bool(current_selection and current_selection[0] == index)
            listbox.selection_clear(0, END)
            listbox.selection_set(index)
            listbox.activate(index)
        except Exception:
            already_selected = False
        if not already_selected:
            self._update_market_status()

        self._show_themed_context_menu(
            event.x_root,
            event.y_root,
            [
                (tr(self.lang, "context_open_location"), lambda: self._open_file_location(local_path), "normal"),
            ],
        )
        return "break"

    def _update_market_status(self, _event=None):
        item = self._selected_market_item()
        if not item:
            return
        self._load_market_preview(item)
        self._set_market_notice()
        self._set_market_detail_text(item)

    def _set_market_detail_text(self, item):
        author = (item.get("author") or {}).get("displayName") or "?"
        title = item.get("title") or item.get("id") or "Untitled"
        description = item.get("description") or tr(self.lang, "market_no_description")
        layers = item.get("defaultLayers", "-")
        template = item.get("recommendedTemplateLayers", "-")
        downloads = item.get("downloads", 0)
        likes = item.get("likes", 0)
        comments = item.get("comments", 0)
        tags = item.get("tags") or []
        self.market_detail_title.set(title)
        self.market_detail_author.set(tr(self.lang, "market_author").format(author=author))
        self.market_detail_layers.set(tr(self.lang, "market_layers_metric").format(layers=layers, template=template))
        self.market_detail_stats.set(tr(self.lang, "market_stats_metric").format(
            downloads=downloads, likes=likes, comments=comments))
        self.market_detail_tags.set(" ".join(f"#{tag}" for tag in tags[:10]) if tags else tr(self.lang, "market_no_tags"))
        self.market_detail_description.set(description)
        self._set_market_description(description)

    def _clear_market_details(self):
        self.market_detail_title.set("")
        self.market_detail_author.set("")
        self.market_detail_layers.set("")
        self.market_detail_stats.set("")
        self.market_detail_tags.set("")
        self.market_detail_description.set("")
        self._set_market_description("")

    def _set_market_description(self, text):
        widget = getattr(self, "market_description_text", None)
        if widget is None:
            return
        try:
            widget.config(state="normal")
            widget.delete("1.0", END)
            if text:
                widget.insert(END, text)
            widget.config(state="disabled")
        except Exception:
            pass

    def _clip_market_text(self, value, limit):
        text = str(value or "")
        if len(text) <= limit:
            return text
        return text[:max(1, limit - 1)] + "…"

    def _load_market_preview(self, item):
        item_id = item.get("id")
        self.market_preview_item_id = item_id
        label = self.market_preview_label
        if label is not None:
            label.config(image="", text=tr(self.lang, "market_preview_loading"))
            label.image = None
        preview_url = item.get("previewUrl")
        if not preview_url:
            if label is not None:
                label.config(text=tr(self.lang, "market_preview_hint"))
            return
        threading.Thread(target=self._market_preview_worker, args=(item_id, preview_url), daemon=True).start()

    def _market_preview_worker(self, item_id, preview_url):
        try:
            if str(preview_url).startswith("http"):
                url = str(preview_url)
            else:
                url = self._market_api_url(str(preview_url))
            request = urllib.request.Request(
                url,
                headers={"User-Agent": f"{APP_DISPLAY_NAME}/{__version__}", "Accept": "image/png,image/*"},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
            self.queue.put(("market_preview", {"id": item_id, "data": data}))
        except Exception as exc:
            self.queue.put(("market_preview_error", {"id": item_id, "error": str(exc)}))

    def _market_preview_photo(self, data):
        try:
            from PIL import Image, ImageTk
            with Image.open(io.BytesIO(data)) as image:
                image = image.convert("RGBA")
                image.thumbnail((244, 300), Image.Resampling.LANCZOS)
                return ImageTk.PhotoImage(image)
        except Exception:
            return PhotoImage(data=data)

    def _show_market_preview(self, item_id, data):
        if item_id != self.market_preview_item_id or self.market_preview_label is None:
            return
        try:
            image = self._market_preview_photo(data)
        except Exception:
            self.market_preview_label.config(image="", text=tr(self.lang, "preview_unavailable"))
            self.market_preview_label.image = None
            return
        self.market_preview_label.config(image=image, text="")
        self.market_preview_label.image = image

    def download_selected_market_json(self):
        item = self._selected_market_item()
        if not item:
            self._set_market_notice("market_empty")
            return
        local_path_value = str(item.get("localPath") or "").strip()
        local_path = Path(local_path_value) if local_path_value else None
        if local_path is not None and local_path.is_file() and local_path.suffix.lower() == ".json":
            self.queue.put(("market_downloaded", {
                "path": local_path,
                "template": item.get("recommendedTemplateLayers"),
                "reused": True,
                "item": item,
                "detail": item,
                "geometry": {
                    "defaultLayers": item.get("defaultLayers"),
                    "recommendedTemplateLayers": item.get("recommendedTemplateLayers"),
                },
            }))
            return
        self._set_market_notice("market_loading")
        threading.Thread(target=self._market_download_worker, args=(item,), daemon=True).start()

    def open_selected_market_website(self):
        item = self._selected_market_item()
        if not item:
            webbrowser.open(MARKET_URL)
            return
        item_id = item.get("id")
        if item_id:
            webbrowser.open(f"{MARKET_URL.rstrip()}/?q={urllib.parse.quote(str(item_id))}")
        else:
            webbrowser.open(MARKET_URL)

    def _market_download_worker(self, item):
        try:
            item_id = item.get("id")
            detail = self._market_request(f"/api/market/items/{urllib.parse.quote(str(item_id), safe='')}")
            files = detail.get("geometryFiles") or []
            if not files:
                raise RuntimeError("No geometry JSON files are available for this preset.")
            geometry = next((file for file in files if file.get("isDefault")), files[0])
            folder = ROOT / "runtime" / "market-downloads"
            folder.mkdir(parents=True, exist_ok=True)
            filename = self._safe_market_filename(geometry.get("fileName") or f"{detail.get('title') or item_id}.json")
            output = folder / filename

            if output.exists():
                try:
                    existing_data = output.read_bytes()
                    json.loads(existing_data.decode("utf-8", errors="replace"))
                    expected_hash = str(geometry.get("sha256") or "").strip().lower()
                    if expected_hash and hashlib.sha256(existing_data).hexdigest().lower() != expected_hash:
                        raise ValueError("Existing market JSON does not match the preset hash.")
                    self.queue.put(("market_downloaded", {
                        "path": output,
                        "template": geometry.get("recommendedTemplateLayers") or detail.get("recommendedTemplateLayers"),
                        "reused": True,
                        "item": item,
                        "detail": detail,
                        "geometry": geometry,
                    }))
                    return
                except Exception:
                    pass

            file_id = urllib.parse.quote(str(geometry.get("id")), safe="")
            url = self._market_api_url(f"/api/market/items/{urllib.parse.quote(str(item_id), safe='')}/files/{file_id}")
            request = urllib.request.Request(
                url,
                headers={"User-Agent": f"{APP_DISPLAY_NAME}/{__version__}", "Accept": "application/json,*/*"},
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
            json.loads(data.decode("utf-8", errors="replace"))
            output.write_bytes(data)
            self.queue.put(("market_downloaded", {
                "path": output,
                "template": geometry.get("recommendedTemplateLayers") or detail.get("recommendedTemplateLayers"),
                "reused": False,
                "item": item,
                "detail": detail,
                "geometry": geometry,
            }))
        except Exception as exc:
            self.queue.put(("market_error", str(exc)))

    def _safe_market_filename(self, value):
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or "market.json")).strip(" ._")
        if not name.lower().endswith(".json"):
            name += ".json"
        return name[:180] or "market.json"

    def add_json(self):
        files = filedialog.askopenfilenames(
            title="Choose geometry JSON",
            filetypes=[("Geometry JSON", "*.json"), ("All files", "*.*")],
        )
        self._add_manual_json_paths([Path(item) for item in files])

    def remove_selected_json(self):
        selection = list(self.json_list.curselection())
        if not selection:
            self.log_line(tr(self.lang, "no_json_selected"))
            return
        removed_paths = []
        for index in sorted(selection, reverse=True):
            try:
                removed_paths.append(Path(self.json_files[index]))
                del self.json_files[index]
            except IndexError:
                pass
        self._render_lists()
        for path in removed_paths:
            self._clear_preview_if_path(path, import_only=False)
        self._refresh_import_readiness()

    def use_generated_outputs(self):
        for path in self.outputs:
            if path.exists() and path not in self.json_files:
                self.json_files.append(path)
        self._render_lists()
        self._refresh_import_readiness()
        self.log_line(tr(self.lang, "log_added_outputs").format(count=len(self.outputs)))

    def _preview_selected_image(self, _event=None):
        selection = self.image_list.curselection()
        if selection:
            self.show_source_preview(self.images[selection[0]])

    def _preview_selected_json(self, _event=None):
        selection = self.json_list.curselection()
        if selection:
            self.show_json_preview(self.json_files[selection[0]])
        self._refresh_import_readiness()

    def _active_preview_label(self):
        if getattr(self, "current_section", None) == "import" and hasattr(self, "import_preview_label"):
            return self.import_preview_label
        return getattr(self, "preview_label", None)

    def _preview_bounds(self, label=None):
        label = label or self._active_preview_label()
        if label is None:
            return PREVIEW_MAX, PREVIEW_MAX
        try:
            width = label.winfo_width()
            height = label.winfo_height()
        except Exception:
            width = height = 0
        if width <= 32 or height <= 32:
            return PREVIEW_MAX, PREVIEW_MAX
        return max(1, width - 16), max(1, height - 16)

    def _preview_size_bucket(self, label=None):
        width, height = self._preview_bounds(label)
        return max(0, width // PREVIEW_SIZE_BUCKET), max(0, height // PREVIEW_SIZE_BUCKET)

    def _schedule_preview_refresh(self, event=None):
        if not self.current_preview_request or self.closed:
            return
        label = event.widget if event is not None and hasattr(event, "widget") else self._active_preview_label()
        bucket = self._preview_size_bucket(label)
        if bucket == self.preview_resize_bucket:
            return
        self.preview_resize_bucket = bucket
        if self.preview_resize_job is not None:
            try:
                self.root.after_cancel(self.preview_resize_job)
            except Exception:
                pass
        self.preview_resize_job = self.root.after(PREVIEW_RESIZE_DEBOUNCE_MS, self._refresh_current_preview)

    def _refresh_current_preview(self):
        self.preview_resize_job = None
        request = self.current_preview_request
        if not request or self.closed:
            return
        kind, path = request
        path = Path(path)
        if not path.exists():
            return
        if kind == "json":
            data = render_geometry_json(path, self._preview_bounds())
        else:
            data = render_source_image_fit(path, self._preview_bounds())
        self.show_preview(data)

    def show_json_preview(self, path):
        path = Path(path)
        self.preview_resize_bucket = None
        self.current_preview_request = ("json", path)
        self.show_preview(render_geometry_json(path, self._preview_bounds()))

    def show_preview(self, data):
        if not data:
            self.current_preview_request = None
            self.preview_resize_bucket = None
            message = tr(self.lang, "preview_unavailable")
            self.preview_label.config(image="", text=message, bg=Theme.PREVIEW_BG)
            self.preview_label.image = None
            if hasattr(self, "import_preview_label"):
                self.import_preview_label.config(image="", text=message, bg=Theme.PREVIEW_BG)
                self.import_preview_label.image = None
            return
        self.photo = data
        image = PhotoImage(data=data)
        self.preview_label.config(image=image, text="", bg=Theme.PREVIEW_BG)
        self.preview_label.image = image
        if hasattr(self, "import_preview_label"):
            import_image = PhotoImage(data=data)
            self.import_preview_label.config(image=import_image, text="", bg=Theme.PREVIEW_BG)
            self.import_preview_label.image = import_image

    def show_source_preview(self, path):
        path = Path(path)
        self.preview_resize_bucket = None
        self.current_preview_request = ("source", path)
        data = render_source_image_fit(path, self._preview_bounds())
        if data:
            self.show_preview(data)
            return
        if Path(path).suffix.lower() in (".png", ".gif"):
            self.show_preview_file(path, remember=False)
            return
        self.show_preview(None)

    def show_preview_file(self, path, remember=True):
        path = Path(path)
        if remember:
            self.preview_resize_bucket = None
            self.current_preview_request = ("file", path)
        data = render_source_image_fit(path, self._preview_bounds())
        if data:
            self.show_preview(data)
            return
        try:
            image = PhotoImage(file=str(path))
        except Exception:
            self.show_preview(None)
            return
        self.photo = image
        self.preview_label.config(image=image, text="", bg=Theme.PREVIEW_BG)
        self.preview_label.image = image
        if hasattr(self, "import_preview_label"):
            import_image = PhotoImage(file=str(path))
            self.import_preview_label.config(image=import_image, text="", bg=Theme.PREVIEW_BG)
            self.import_preview_label.image = import_image

    def refresh_processes(self):
        self.processes = game_processes()
        values = [item["label"] for item in self.processes]
        if not values:
            values = [tr(self.lang, "no_game")]
        self.process_combo["values"] = values
        if self.processes:
            self.selected_pid.set(values[0])
            self.selected_game.set(self.processes[0]["profile"])
        else:
            self.selected_pid.set("")

    def selected_pid_value(self):
        raw = self.selected_pid.get()
        match = re.search(r"pid\s+(\d+)", raw, re.I)
        if match:
            return int(match.group(1))
        try:
            return int(raw.strip())
        except ValueError:
            return None

    def _pid_matches_game(self, pid, game):
        profile = PROFILES.get(game)
        if not pid or not profile:
            return False
        try:
            process_name = psutil.Process(pid).name().lower()
        except psutil.Error:
            return False
        return process_name in [name.lower() for name in profile.process_names]

    def ensure_live_game_pid(self):
        game = self.selected_game.get() or "fh6"
        pid = self.selected_pid_value()
        if self._pid_matches_game(pid, game):
            return pid
        if pid:
            self.log_line(tr(self.lang, "log_pid_no_longer_running").format(pid=pid))
        self.refresh_processes()
        game = self.selected_game.get() or game
        pid = self.selected_pid_value()
        if self._pid_matches_game(pid, game):
            return pid
        self.log_line(tr(self.lang, "log_no_live_game"))
        return None

    def stop_generate(self):
        with self.generation_lock:
            if not self.generation_running:
                self.log_line(tr(self.lang, "no_generation_running"))
                return
            proc = self.current_generator_proc
        self.log_line(tr(self.lang, "stopping_generation"))
        self.shutdown_event.set()
        if proc is not None:
            self._terminate_process(proc)
        self.status.set(tr(self.lang, "stopped"))

    def start_generate(self):
        self.active_log_scope = "generate"
        with self.generation_lock:
            if self.generation_running:
                self.log_line(tr(self.lang, "log_already_running"))
                return
            self.generation_running = True
        if not self.images:
            with self.generation_lock:
                self.generation_running = False
            message = tr(self.lang, "log_no_images_selected")
            self.log_line(message)
            self._show_themed_alert(tr(self.lang, "generate_tab"), message)
            return
        setting = self._effective_setting()
        if not setting:
            with self.generation_lock:
                self.generation_running = False
            self.log_line(tr(self.lang, "log_no_quality_profile"))
            return
        if not GENERATOR_EXE.exists():
            with self.generation_lock:
                self.generation_running = False
            self.log_line(tr(self.lang, "log_missing_generator").format(path=GENERATOR_EXE))
            return
        output_dir = self._selected_output_folder()
        if output_dir is not None:
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                with self.generation_lock:
                    self.generation_running = False
                self.log_line(tr(self.lang, "log_output_folder_failed").format(path=output_dir, error=exc))
                return
        self.shutdown_event.clear()
        self._reset_generation_eta()
        self.progress_text.set("")
        self._set_scoped_log_progress("generate", 0, tr(self.lang, "generation_progress_idle"))
        self.generate_progress_value = 0
        self.generate_modal_status.set(tr(self.lang, "generation_progress_idle"))
        self.generate_modal_percent.set("0%")
        self.generate_modal_source_path = self.images[0] if self.images else None
        self.generate_modal_preview_path = None
        self.generate_modal_preview_shadow_path = self.images[0] if self.images else None
        self.generate_modal_preview_image = None
        self.status.set(tr(self.lang, "running"))
        if hasattr(self, "generate_button"):
            self.generate_button.config(state="disabled")
        if hasattr(self, "stop_generate_button"):
            self.stop_generate_button.config(state="normal")
        self._show_generate_log_modal()
        self._set_generate_modal_progress(0, tr(self.lang, "generation_progress_idle"))
        threading.Thread(target=self._generate_worker, args=(setting, output_dir), daemon=True).start()

    def _generate_worker(self, setting, output_dir=None):
        try:
            profile_name = getattr(setting, "label", None) or setting['path'].name
            self.queue.put(("log", tr(self.lang, "log_selected_profile").format(name=profile_name)))
            self._log_generation_load_warning(setting)
            queued_images = list(self.images)
            for image_path in queued_images:
                self.queue.put(("batch_state", {"path": image_path, "key": "queue_pending"}))
            for index, image_path in enumerate(queued_images, start=1):
                if self.shutdown_event.is_set():
                    self.queue.put(("status", tr(self.lang, "stopped")))
                    return
                self._reset_generation_eta()
                self.queue.put(("batch_state", {"path": image_path, "key": "queue_running"}))
                self.queue.put(("progress", tr(self.lang, "queue_progress").format(current=index, total=len(queued_images), image=image_path.name)))
                self.queue.put(("generation_source_file", image_path))
                self.queue.put(("generation_progress", {
                    "value": 0,
                    "text": tr(self.lang, "queue_progress").format(current=index, total=len(queued_images), image=image_path.name),
                }))
                input_image = preprocess_input_image(image_path, setting)
                if input_image != image_path:
                    self.queue.put(("log", tr(self.lang, "log_preprocessed_image").format(path=input_image)))
                before = {path.resolve() for path in generated_jsons(input_image, output_dir)}
                preview_path = generator_preview_path(input_image)
                if preview_path.exists():
                    try:
                        preview_path.unlink()
                    except OSError:
                        pass
                self.queue.put(("log", tr(self.lang, "log_generating").format(path=image_path)))
                self.queue.put(("preview_file", image_path))
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                cmd = build_generator_command(input_image, setting, output_dir=output_dir)
                self._record_detail(f"GENERATOR COMMAND: {self._format_command(cmd)}")
                self.queue.put(("log", tr(self.lang, "log_running_generator").format(profile=setting['path'].name)))
                if self.shutdown_event.is_set():
                    self.queue.put(("status", tr(self.lang, "stopped")))
                    return
                proc = self._popen_registered(
                    cmd,
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=flags,
                    env=build_generator_env(),
                )
                if proc is None:
                    self.queue.put(("status", tr(self.lang, "stopped")))
                    return
                with self.generation_lock:
                    self.current_generator_proc = proc

                last_preview = None
                last_preview_mtime = None
                last_generator_message = None
                output_queue = queue.Queue()

                def _read_generator_output():
                    try:
                        for raw_line in proc.stdout:
                            self._record_detail(f"GENERATOR RAW: {raw_line.rstrip()}")
                            output_queue.put(raw_line)
                    finally:
                        output_queue.put(None)

                reader = threading.Thread(target=_read_generator_output, daemon=True)
                reader.start()

                def _drain_generator_output():
                    nonlocal last_generator_message
                    while True:
                        try:
                            raw_line = output_queue.get_nowait()
                        except queue.Empty:
                            break
                        if raw_line is None:
                            continue
                        friendly = self.friendly_generator_line(raw_line)
                        last_generator_message = self.queue_generator_message(friendly, last_generator_message)

                next_preview_scan = 0.0
                next_json_scan = 0.0

                try:
                    while proc.poll() is None:
                        if self.shutdown_event.is_set():
                            self._terminate_process(proc)
                            outputs = self._queue_generated_outputs(input_image, before, output_dir)
                            for output in outputs:
                                self.queue.put(("log", tr(self.lang, "checkpoint_available_after_failure").format(path=output)))
                            if outputs:
                                self.queue.put(("render_lists", None))
                            self.queue.put(("batch_state", {"path": image_path, "key": "queue_stopped"}))
                            self.queue.put(("status", tr(self.lang, "stopped")))
                            return
                        _drain_generator_output()
                        now = time.monotonic()
                        if now >= next_preview_scan:
                            next_preview_scan = now + GENERATOR_LIVE_PREVIEW_SCAN_SECONDS
                            preview_files = generated_preview_files(input_image)
                            if preview_files:
                                newest_preview = preview_files[0]
                                try:
                                    stat1 = newest_preview.stat()
                                except OSError:
                                    stat1 = None
                                # Wait one short tick and re-stat. Only queue the
                                # path once size+mtime are stable across two reads,
                                # so the GUI never tries to decode a half-written PNG
                                # (libpng raises "Read Error" on truncated data).
                                if stat1 is not None and stat1.st_mtime != last_preview_mtime:
                                    time.sleep(0.02)
                                    try:
                                        stat2 = newest_preview.stat()
                                    except OSError:
                                        stat2 = None
                                    if (
                                        stat2 is not None
                                        and stat2.st_size == stat1.st_size
                                        and stat2.st_mtime == stat1.st_mtime
                                        and stat2.st_size > 0
                                    ):
                                        last_preview_mtime = stat2.st_mtime
                                        self.queue.put(("preview_file", newest_preview))
                        if now >= next_json_scan:
                            next_json_scan = now + GENERATOR_JSON_SCAN_SECONDS
                            newest = generated_jsons(input_image, output_dir)
                            if newest and newest[0] != last_preview:
                                last_preview = newest[0]
                        time.sleep(GENERATOR_LIVE_PREVIEW_POLL_SLEEP_SECONDS)
                    if self.shutdown_event.is_set():
                        return
                    reader.join(timeout=1)
                    _drain_generator_output()
                finally:
                    self._unregister_process(proc)
                    with self.generation_lock:
                        if self.current_generator_proc is proc:
                            self.current_generator_proc = None
                if proc.returncode != 0:
                    self._record_detail(f"GENERATOR EXIT: {proc.returncode}")
                    outputs = self._queue_generated_outputs(input_image, before, output_dir)
                    for output in outputs:
                        self.queue.put(("log", tr(self.lang, "checkpoint_available_after_failure").format(path=output)))
                    if outputs:
                        self.queue.put(("render_lists", None))
                    self.queue.put(("batch_state", {"path": image_path, "key": "queue_failed"}))
                    self.queue.put(("log", self._generator_exit_message(proc.returncode)))
                    self.queue.put(("status", tr(self.lang, "failed")))
                    return
                self._record_detail("GENERATOR EXIT: 0")
                new_outputs = self._queue_generated_outputs(input_image, before, output_dir)
                if not new_outputs:
                    self.queue.put(("log", tr(self.lang, "log_generator_no_output")))
                    self.queue.put(("batch_state", {"path": image_path, "key": "queue_failed"}))
                    self.queue.put(("status", tr(self.lang, "failed")))
                    return
                self.queue.put(("batch_state", {"path": image_path, "key": "queue_done"}))
                for output in new_outputs:
                    preview_files = generated_preview_files(input_image)
                    if preview_files:
                        self.queue.put(("preview_file", preview_files[0]))
                    else:
                        self.queue.put(("preview_json", output))
            self.queue.put(("render_lists", None))
            self.queue.put(("status", tr(self.lang, "done")))
        except Exception as exc:
            self.queue.put(("log", f"{tr(self.lang, 'failed')}: {exc}"))
            self.queue.put(("status", tr(self.lang, "failed")))
        finally:
            self.queue.put(("generation_done", None))

    def open_output_folder(self):
        current = self._selected_output_folder()
        if current is not None and current.exists():
            initial_dir = current
        elif self.outputs:
            initial_dir = self.outputs[-1].parent
        elif self.images:
            initial_dir = self.images[-1].parent
        else:
            initial_dir = ROOT
        selected = filedialog.askdirectory(
            title=tr(self.lang, "choose_output_folder"),
            initialdir=str(initial_dir),
            mustexist=True,
        )
        if not selected:
            return
        folder = Path(selected)
        self.output_folder.set(str(folder))
        self._save_preferences()
        self.log_line(tr(self.lang, "log_output_folder_selected").format(path=folder))
        for image_path in list(self.images):
            self._load_existing_checkpoints_for_image(image_path)
        self._render_lists()

    def open_runtime_folder(self):
        ROOT.mkdir(parents=True, exist_ok=True)
        os.startfile(ROOT)

    def choose_full_shape_json(self):
        selected = filedialog.askopenfilename(
            title=tr(self.lang, "full_shape_choose_json"),
            filetypes=[("Full-shape JSON", "*.json"), ("All files", "*.*")],
        )
        if not selected:
            return
        selected_path = Path(selected)
        self.full_shape_json_path.set(str(selected_path))
        self.show_json_preview(selected_path)
        try:
            count = typecode_shape_count(selected_path)
            self.log_line(f"{tr(self.lang, 'full_shape_selected_json')} {selected_path.name} ({count} shapes)", scope="full_shape")
        except Exception as exc:
            self.log_line(f"{tr(self.lang, 'full_shape_invalid_json')}: {exc}", scope="full_shape")

    def open_full_shape_folder(self):
        folder = Path(self.full_shape_last_output_dir.get() or FULL_SHAPE_ROOT)
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def _full_shape_count_value(self):
        try:
            count = int(str(self.full_shape_count.get()).strip())
        except (TypeError, ValueError):
            self.log_line(tr(self.lang, "full_shape_count_required"), scope="full_shape")
            return None
        if count <= 0:
            self.log_line(tr(self.lang, "full_shape_count_required"), scope="full_shape")
            return None
        return count

    def _full_shape_run_dir(self, prefix, name):
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(name or "run")).strip("-") or "run"
        run_dir = FULL_SHAPE_ROOT / f"{prefix}-{safe_name}-{timestamp}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def start_full_shape_import(self):
        self.active_log_scope = "full_shape"
        pid = self.ensure_live_game_pid()
        if not pid:
            return
        if self.layer_count.get().strip():
            self.full_shape_count.set(self.layer_count.get().strip())
        template_count = self._full_shape_count_value()
        if not template_count:
            return
        json_path = Path(self.full_shape_json_path.get().strip())
        if not json_path.exists():
            self.log_line(tr(self.lang, "full_shape_no_json"), scope="full_shape")
            return
        try:
            shape_count = typecode_shape_count(json_path)
        except Exception as exc:
            self.log_line(f"{tr(self.lang, 'full_shape_invalid_json')}: {exc}", scope="full_shape")
            return
        if shape_count <= 0:
            self.log_line(tr(self.lang, "full_shape_no_shapes"), scope="full_shape")
            return
        if shape_count > template_count:
            self.log_line(f"{tr(self.lang, 'full_shape_too_many_shapes')} JSON={shape_count}, template={template_count}", scope="full_shape")
            return
        self.full_shape_running = True
        self._set_scoped_log_progress("full_shape", 0, tr(self.lang, "full_shape_importing"))
        self.status.set(tr(self.lang, "running"))
        threading.Thread(
            target=self._full_shape_import_worker,
            args=(pid, template_count, shape_count, json_path, self.full_shape_clear_unused.get() == "1"),
            daemon=True,
        ).start()

    def start_full_shape_export(self):
        self.active_log_scope = "full_shape"
        pid = self.ensure_live_game_pid()
        if not pid:
            self._show_themed_alert(tr(self.lang, "full_shape_failed"), tr(self.lang, "log_no_live_game"))
            return
        template_count = self._full_shape_count_value()
        if not template_count:
            return
        initial_dir = Path(self.full_shape_last_output_dir.get() or FULL_SHAPE_ROOT)
        initial_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = filedialog.asksaveasfilename(
            title=tr(self.lang, "full_shape_export_button"),
            initialdir=initial_dir,
            initialfile=f"fh6-full-shape-export-{template_count}-{timestamp}.json",
            defaultextension=".json",
            filetypes=[("Full-shape JSON", "*.json"), ("All files", "*.*")],
        )
        if not output:
            return
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.full_shape_last_output_dir.set(str(output_path.parent))
        self.full_shape_running = True
        self._set_scoped_log_progress("full_shape", 0, tr(self.lang, "full_shape_exporting"))
        self.status.set(tr(self.lang, "running"))
        threading.Thread(target=self._full_shape_export_worker, args=(pid, template_count, output_path), daemon=True).start()

    def _candidate_shape_count(self, candidate, shape_byte):
        counts = candidate.get("shape_id_counts_all") or {}
        return int(counts.get(str(shape_byte)) or counts.get(shape_byte) or 0)

    def _candidate_rejection(self, candidate, template_count, require_circle_template):
        valid_ptrs = int(candidate.get("valid_ptrs") or 0)
        sample_ok = int(candidate.get("sample_ok_count") or 0)
        min_sample_ok = min(8, int(template_count))
        vector_count = candidate.get("vector_count")
        capacity_count = candidate.get("capacity_count")
        if candidate.get("vector_ok") is False:
            return "vector metadata invalid"
        if vector_count is not None and int(vector_count) != int(template_count):
            return f"vector_count={vector_count}"
        if capacity_count is not None and int(capacity_count) < int(template_count):
            return f"capacity_count={capacity_count}"
        if valid_ptrs < int(template_count):
            return f"valid_ptrs={valid_ptrs}"
        if sample_ok < min_sample_ok:
            return f"sample_ok={sample_ok}"
        if require_circle_template:
            min_circle_count = int(int(template_count) * 0.90)
            circle_count = self._candidate_shape_count(candidate, 102)
            if circle_count < min_circle_count:
                return f"circle_template_check={circle_count}/{template_count}"
        return ""

    def _locate_full_shape_group(self, pid, template_count, run_dir, purpose):
        cmd = [
            *helper_command("fh6_typecode_probe"),
            "--pid", str(pid),
            "--count", str(template_count),
            "--max-seconds", "120",
            "--report-layers", "40",
            "--out-dir", run_dir,
        ]
        self.queue.put(("scoped_log", ("full_shape", tr(self.lang, "full_shape_probe_wait"))))
        code = self.run_subprocess(cmd, timeout=180)
        if code != 0:
            raise RuntimeError("full-shape probe did not complete")
        probe_files = sorted(run_dir.glob(f"fh6-group{template_count}-probe-*.json"), key=lambda path: path.stat().st_mtime)
        if not probe_files:
            raise RuntimeError("full-shape probe report was not created")
        probe_report = run_dir / f"fallback-{purpose}-probe.json"
        probe_files[-1].replace(probe_report)
        probe = json.loads(probe_report.read_text(encoding="utf-8"))
        candidates = probe.get("candidates") or []
        if not candidates:
            raise RuntimeError("no matching loaded FH6 group was found")

        require_circle_template = str(purpose).startswith("import")
        rejected = []
        selected = None
        for index, candidate in enumerate(candidates, start=1):
            group = candidate.get("group")
            table = candidate.get("table")
            rejection = self._candidate_rejection(candidate, template_count, require_circle_template)
            if group and table and not rejection:
                selected = (index, group, table, int(candidate.get("valid_ptrs") or 0), int(candidate.get("sample_ok_count") or 0), self._candidate_shape_count(candidate, 102))
                break
            rejected.append(f"#{index}: {rejection or 'missing group/table'}")

        if not selected:
            detail = "; ".join(rejected[:5]) if rejected else "no candidates"
            if require_circle_template:
                raise RuntimeError(tr(self.lang, "full_shape_fresh_template_required").format(detail=detail))
            raise RuntimeError(f"located group did not validate strongly enough ({detail})")
        index, group, table, valid_ptrs, sample_ok, circle_count = selected
        circle_suffix = f", circle_template={circle_count}/{template_count}" if require_circle_template else ""
        self.queue.put((
            "scoped_log",
            (
                "full_shape",
                f"FH6 full-shape group located: candidate #{index}, group={group}, table={table}, "
                f"layers={template_count}, valid_ptrs={valid_ptrs}, sample_ok={sample_ok}{circle_suffix}",
            ),
        ))
        return group, table, probe_report

    def _full_shape_import_worker(self, pid, template_count, shape_count, json_path, clear_unused):
        run_dir = self._full_shape_run_dir("import", json_path.stem)
        self.queue.put(("full_shape_report_dir", run_dir))
        backup_path = run_dir / "import-backup.json"
        import_report = run_dir / "import-report.json"
        try:
            self.queue.put(("scoped_log", ("full_shape", tr(self.lang, "full_shape_import_start").format(
                shapes=shape_count,
                template=template_count,
            ))))
            _group, table, _probe_report = self._locate_full_shape_group(pid, template_count, run_dir, "import-template")
            import_cmd = [
                *helper_command("fh6_typecode_import"),
                "--pid", str(pid),
                "--table", str(table),
                "--json", json_path,
                "--template-count", str(template_count),
                "--compact-supported-layers",
                "--allow-unknown-low-byte",
                "--backup", backup_path,
                "--report", import_report,
                "--write",
            ]
            if clear_unused:
                import_cmd.append("--clear-unused")
            code = self.run_subprocess(import_cmd, timeout=360)
            if code != 0:
                self.queue.put(("full_shape_failed_prompt", tr(self.lang, "full_shape_import_helper_error")))
                self.queue.put(("status", tr(self.lang, "failed")))
                return
            report = json.loads(import_report.read_text(encoding="utf-8"))
            imported = int(report.get("imported_layer_count") or 0)
            failures = int(report.get("failure_count") or 0)
            unsupported = int(report.get("unsupported_shape_count") or 0)
            if unsupported:
                self.queue.put(("scoped_log", ("full_shape", tr(self.lang, "full_shape_unsupported_skipped").format(count=unsupported))))
            if failures or imported <= 0:
                detail = f"imported={imported}, failures={failures}"
                self.queue.put(("scoped_log", ("full_shape", tr(self.lang, "full_shape_import_incomplete").format(detail=detail))))
                self.queue.put(("full_shape_failed_prompt", detail))
                self.queue.put(("status", tr(self.lang, "failed")))
                return
            self.queue.put(("scoped_log", ("full_shape", tr(self.lang, "full_shape_import_done").format(count=imported))))
            self.queue.put(("market_imported_path", str(json_path)))
            self.queue.put(("status", tr(self.lang, "done")))
        except Exception as exc:
            self.queue.put(("scoped_log", ("full_shape", f"{tr(self.lang, 'full_shape_failed')}: {exc}")))
            self.queue.put(("full_shape_failed_prompt", str(exc)))
            self.queue.put(("status", tr(self.lang, "failed")))
        finally:
            self.queue.put(("import_done", None))

    def _full_shape_export_worker(self, pid, template_count, output_path):
        run_dir = self._full_shape_run_dir("export", output_path.stem)
        self.queue.put(("full_shape_report_dir", run_dir))
        self.queue.put(("full_shape_output_dir", output_path.parent))
        export_report = run_dir / "export-report.json"
        try:
            group, table, probe_report = self._locate_full_shape_group(pid, template_count, run_dir, "export-template")
            export_cmd = [
                *helper_command("fh6_typecode_export"),
                "--pid", str(pid),
                "--group", str(group),
                "--table", str(table),
                "--count", str(template_count),
                "--out", output_path,
                "--report", export_report,
                "--probe-report", probe_report,
            ]
            code = self.run_subprocess(export_cmd, timeout=360)
            if code != 0:
                prompt_detail = tr(self.lang, "full_shape_export_helper_error")
                if export_report.exists():
                    try:
                        report = json.loads(export_report.read_text(encoding="utf-8"))
                        reasons = report.get("validation_reasons") or []
                        if reasons:
                            prompt_detail = "; ".join(str(reason) for reason in reasons[:4])
                            self.queue.put(("scoped_log", ("full_shape", tr(self.lang, "full_shape_export_validation").format(detail=prompt_detail))))
                    except Exception:
                        pass
                self.queue.put(("full_shape_failed_prompt", prompt_detail))
                self.queue.put(("status", tr(self.lang, "failed")))
                return
            report = json.loads(export_report.read_text(encoding="utf-8"))
            exported = int(report.get("exported_shape_count") or 0)
            failures = int(report.get("failure_count") or 0)
            if failures:
                self.queue.put(("scoped_log", ("full_shape", tr(self.lang, "full_shape_export_warning").format(count=failures))))
            self.queue.put(("full_shape_json_path", output_path))
            self.queue.put(("full_shape_output_dir", output_path.parent))
            self.queue.put(("scoped_log", ("full_shape", tr(self.lang, "full_shape_export_done").format(count=exported, path=output_path))))
            self.queue.put(("status", tr(self.lang, "done")))
        except Exception as exc:
            self.queue.put(("scoped_log", ("full_shape", f"{tr(self.lang, 'full_shape_failed')}: {exc}")))
            self.queue.put(("full_shape_failed_prompt", str(exc)))
            self.queue.put(("status", tr(self.lang, "failed")))
        finally:
            self.queue.put(("import_done", None))

    def run_subprocess(self, cmd, timeout=None):
        log_scope = self._active_log_scope()
        self._record_detail(f"HELPER COMMAND: {self._format_command(cmd)}")
        self.queue.put(("scoped_log", (log_scope, self._friendly_command_name(cmd))))
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        env = os.environ.copy()
        env.update({"FORZA_PAINTER_NO_ELEVATE": "1", "FORZA_PAINTER_NO_PAUSE": "1"})
        if self.shutdown_event.is_set():
            self._record_detail("HELPER EXIT: 130 before start")
            return 130
        proc = self._popen_registered(
            [str(x) for x in cmd],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
            env=env,
        )
        if proc is None:
            self._record_detail("HELPER EXIT: 130 no process")
            return 130
        started = time.time()
        try:
            while True:
                if self.shutdown_event.is_set():
                    self._terminate_process(proc)
                    self._record_detail("HELPER EXIT: 130 stopped")
                    return 130
                line = proc.stdout.readline()
                if line:
                    raw_line = line.rstrip()
                    self._record_detail(f"HELPER RAW: {raw_line}")
                    friendly = self._friendly_subprocess_line(raw_line)
                    if friendly:
                        progress = self._progress_from_text(raw_line)
                        if progress is not None:
                            self.queue.put(("scoped_progress", (log_scope, progress, friendly)))
                        self.queue.put(("scoped_log", (log_scope, friendly)))
                if proc.poll() is not None:
                    break
                if timeout and time.time() - started > timeout:
                    self._terminate_process(proc)
                    self._record_detail(f"HELPER EXIT: 124 timeout after {timeout} seconds")
                    self.queue.put(("scoped_log", (log_scope, tr(self.lang, "log_timed_out").format(seconds=timeout))))
                    return 124
                time.sleep(0.05)
            if self.shutdown_event.is_set():
                self._record_detail("HELPER EXIT: 130 stopped after process exit")
                return 130
            for line in proc.stdout.read().splitlines():
                raw_line = line.rstrip()
                self._record_detail(f"HELPER RAW: {raw_line}")
                friendly = self._friendly_subprocess_line(raw_line)
                if friendly:
                    progress = self._progress_from_text(raw_line)
                    if progress is not None:
                        self.queue.put(("scoped_progress", (log_scope, progress, friendly)))
                    self.queue.put(("scoped_log", (log_scope, friendly)))
            self._record_detail(f"HELPER EXIT: {proc.returncode}")
            return proc.returncode
        finally:
            self._unregister_process(proc)

    def _friendly_command_name(self, cmd):
        joined = " ".join(str(x) for x in cmd)
        if "fh6_probe.py" in joined and "--auto-locate" in joined:
            return tr(self.lang, "locating")
        if "fh6_typecode_probe" in joined:
            return tr(self.lang, "full_shape_locating")
        if "fh6_typecode_import" in joined:
            return tr(self.lang, "full_shape_importing")
        if "fh6_typecode_export" in joined:
            return tr(self.lang, "full_shape_exporting")
        if "fh6_typecode_trim" in joined:
            return tr(self.lang, "full_shape_trimming")
        if "main.py" in joined:
            return tr(self.lang, "importing")
        return tr(self.lang, "log_starting_helper")

    def _check_json_layer_fit(self, json_path, layer_count):
        if is_typecode_json(json_path):
            return
        try:
            from generator_backend import geometry_shape_count
            json_layers = geometry_shape_count(json_path)
            template_layers = int(layer_count)
        except Exception:
            return
        usable_layers = max(0, template_layers - 4)
        if json_layers and template_layers and json_layers > usable_layers:
            self.queue.put(("log", f"{tr(self.lang, 'json_needs_more_template_layers')} JSON={json_layers}, template={template_layers}, usable={usable_layers}"))
        if json_layers and usable_layers and json_layers < usable_layers * 0.75:
            self.queue.put(("log", f"{tr(self.lang, 'json_too_small')} JSON={json_layers}, usable={usable_layers}"))

    def _friendly_subprocess_line(self, line):
        if not line:
            return None
        raw = line.strip()
        lower = raw.lower()
        noisy_parts = (
            "base:",
            "candidate score=",
            "layout candidate",
            "table[",
            "ptr=0x",
            "count=0x",
            "tablefield=",
            "wrote fh6 session location",
            "fh6 layout-count scan checked",
            "process: forzahorizon",
            "current values:",
            "loaded ",
            "descriptor @",
            "info found:",
            "vtp found:",
        )
        if any(part in lower for part in noisy_parts):
            return None
        if "fast fh6 layer group candidates:" in lower:
            return tr(self.lang, "located")
        if "no safe fh6 layer group" in lower:
            return tr(self.lang, "safe_stop")
        if "auto-locating fh6 layer count/table" in lower:
            return tr(self.lang, "locating")
        if "cliverylayer table found" in lower:
            return tr(self.lang, "located")
        localized = self._localize_subprocess_line(raw)
        if localized != raw:
            return localized
        if "openprocess" in lower or "error" in lower or "failed" in lower or "traceback" in lower:
            return raw
        if raw.startswith("<class 'SystemExit'>") or raw.startswith("SystemExit: 0"):
            return None
        return raw

    def _localize_subprocess_line(self, raw):
        raw = re.sub(r"^\[\d{2}:\d{2}:\d{2}\]\s*", "", raw)
        match = re.match(r"(.+?) detected as (.+?) \(pid (\d+)\)$", raw)
        if match:
            game, process, pid = match.groups()
            return tr(self.lang, "log_detected_process").format(game=game, process=process, pid=pid)
        match = re.match(r"Opening pid=(\d+) (.+)$", raw)
        if match:
            pid, process = match.groups()
            return tr(self.lang, "full_shape_opening_process").format(pid=pid, process=process)
        match = re.match(r"Scanned (\d+)/(\d+) regions, ([\d.]+) MB, candidates=(\d+)$", raw)
        if match:
            current, total, mb, candidates = match.groups()
            return tr(self.lang, "full_shape_scan_progress").format(
                current=current,
                total=total,
                mb=mb,
                candidates=candidates,
            )
        match = re.match(r"Scan complete: ([\d.]+) MB, candidates=(\d+)$", raw)
        if match:
            mb, candidates = match.groups()
            return tr(self.lang, "full_shape_scan_complete").format(mb=mb, candidates=candidates)
        match = re.match(r"Wrote (.+)$", raw)
        if match:
            return tr(self.lang, "full_shape_probe_written").format(path=match.group(1))
        match = re.match(r"Exported (\d+) layer\(s\) to (.+)$", raw)
        if match:
            count, path = match.groups()
            return tr(self.lang, "full_shape_exported_layers").format(count=count, path=path)
        match = re.match(r"Report: (.+)$", raw)
        if match:
            return tr(self.lang, "full_shape_report_path").format(path=match.group(1))
        match = re.match(r"Failures: (\d+) unreadable layer\(s\)$", raw)
        if match:
            return tr(self.lang, "full_shape_failures").format(count=match.group(1))
        match = re.match(r"Validation details: (.+)$", raw)
        if match:
            return tr(self.lang, "full_shape_validation_details").format(detail=match.group(1))
        match = re.match(r"Manual (?:FH6 )?layer count address 0x([0-9a-fA-F]+); using template layer count (\d+)$", raw)
        if match:
            address, count = match.groups()
            return tr(self.lang, "log_manual_layer_count_template").format(address=address, count=count)
        match = re.match(r"Manual (?:FH6 )?layer count address 0x([0-9a-fA-F]+) -> (\d+)$", raw)
        if match:
            address, count = match.groups()
            return tr(self.lang, "log_manual_layer_count_value").format(address=address, count=count)
        match = re.match(
            r"Geometry has (\d+) drawable layers but FH bounds reserve (\d+) layers; trimming to (\d+) drawable layers\.$",
            raw,
        )
        if match:
            total, reserved, usable = match.groups()
            return tr(self.lang, "log_geometry_trimmed").format(total=total, reserved=reserved, usable=usable)
        match = re.match(
            r"Drawable layers to import: (\d+) \+ (\d+) FH bounds layers / template layers: (\d+)$",
            raw,
        )
        if match:
            drawable, reserved, template = match.groups()
            return tr(self.lang, "log_drawable_layers_to_import").format(
                drawable=drawable,
                reserved=reserved,
                template=template,
            )
        match = re.match(r"Writing layer (\d+)/(\d+)$", raw)
        if match:
            current, total = match.groups()
            return tr(self.lang, "log_writing_layer").format(current=current, total=total)
        if raw == "DONE!":
            return tr(self.lang, "log_done_caps")
        if raw.startswith("The ideal background color"):
            return tr(self.lang, "log_ideal_background_color")
        return raw

    def start_auto_locate(self):
        self.active_log_scope = "full_shape" if getattr(self, "current_section", None) == "full_shape" else "import"
        pid = self.ensure_live_game_pid()
        layer_count = self.layer_count.get().strip()
        if not pid or not layer_count:
            self.log_line(tr(self.lang, "log_pid_and_count_required"))
            return
        self.status.set(tr(self.lang, "running"))
        threading.Thread(target=self._auto_locate_worker, args=(pid, layer_count), daemon=True).start()

    def _auto_locate_worker(self, pid, layer_count):
        clear_session_location()
        cmd = [
            *helper_command("fh6_probe"),
            "--game",
            self.selected_game.get() or "fh6",
            "--pid",
            str(pid),
            "--layer-count",
            str(layer_count),
            "--auto-locate",
            "--write-session",
            SESSION_PATH,
            "--limit-mb",
            str(MEMORY_SNAPSHOT_LIMIT_MB),
            "--max-matches",
            "500000",
            "--inspect-radius",
            "0x800",
            "--max-seconds",
            str(FH6_AUTO_LOCATE_MAX_SECONDS),
        ]
        self.queue.put(("log", tr(self.lang, "locating_wait")))
        code = self.run_subprocess(cmd, timeout=FH6_AUTO_LOCATE_TIMEOUT_SECONDS)
        located = False
        if code == 0 and SESSION_PATH.exists():
            session = load_session_location()
            if session_matches_current_import(session, self.selected_game.get() or "fh6", pid, layer_count):
                self.queue.put(("log", tr(self.lang, "located")))
                located = True
        self.queue.put(("status", tr(self.lang, "done") if located else tr(self.lang, "failed")))
        return located

    def _json_paths_for_import(self):
        if hasattr(self, "json_list"):
            selection = list(self.json_list.curselection())
            if selection:
                return [self.json_files[index] for index in selection if 0 <= index < len(self.json_files)]
        return list(self.json_files)

    def start_import(self):
        self.active_log_scope = "import"
        alert_title = tr(self.lang, "import_tab")
        if self.import_running:
            self.log_line(tr(self.lang, "import_already_running"), scope="import")
            self._show_themed_alert(alert_title, tr(self.lang, "import_already_running"))
            return
        # Validate inputs BEFORE opening the modal. Opening the modal disables the
        # root window on Windows; if validation fails and the modal is then closed,
        # the OS leaves the root briefly unresponsive. Keep the modal closed until
        # we actually have work to run.  Since the log area is hidden on the import
        # tab, surface the error with a native alert so the user knows what to fix.
        paths = self._json_paths_for_import()
        if not paths:
            self.log_line(tr(self.lang, "log_no_json_files"))
            self.status.set(tr(self.lang, "failed"))
            self._show_themed_alert(alert_title, tr(self.lang, "log_no_json_files"))
            return
        layer_count = self.layer_count.get().strip()
        if not layer_count:
            self.log_line(tr(self.lang, "layer_count_required"))
            self.layer_count_entry.config(highlightbackground="red", highlightthickness=1)
            self.status.set(tr(self.lang, "failed"))
            self._show_themed_alert(alert_title, tr(self.lang, "layer_count_required"))
            try:
                self.layer_count_entry.focus_set()
            except Exception:
                pass
            return
        self.layer_count_entry.config(highlightbackground=Theme.BORDER, highlightthickness=0)
        pid = self.ensure_live_game_pid()
        if not pid:
            self.status.set(tr(self.lang, "failed"))
            self._show_themed_alert(alert_title, tr(self.lang, "log_no_live_game"))
            return
        typecode_paths = [Path(path) for path in paths if is_typecode_json(path)]
        if typecode_paths:
            if len(paths) != 1 or len(typecode_paths) != 1:
                self.log_line(tr(self.lang, "full_shape_single_json_required"))
                self.status.set(tr(self.lang, "failed"))
                self._show_themed_alert(alert_title, tr(self.lang, "full_shape_single_json_required"))
                return
            if (self.selected_game.get() or "fh6") != "fh6":
                self.log_line(tr(self.lang, "full_shape_invalid_json"))
                self.status.set(tr(self.lang, "failed"))
                return
            json_path = typecode_paths[0]
            try:
                template_count = int(layer_count)
                shape_count = typecode_shape_count(json_path)
            except Exception as exc:
                self.log_line(f"{tr(self.lang, 'full_shape_invalid_json')}: {exc}")
                self.status.set(tr(self.lang, "failed"))
                return
            if shape_count <= 0:
                self.log_line(tr(self.lang, "full_shape_no_shapes"))
                self.status.set(tr(self.lang, "failed"))
                return
            if shape_count > template_count:
                self.log_line(f"{tr(self.lang, 'full_shape_too_many_shapes')} JSON={shape_count}, template={template_count}")
                self.status.set(tr(self.lang, "failed"))
                return
            self.full_shape_count.set(str(template_count))
            self.full_shape_json_path.set(str(json_path))
            self.import_running = True
            self._set_scoped_log_progress("import", 0, tr(self.lang, "importing"))
            self._show_import_log_modal("import")
            self.status.set(tr(self.lang, "running"))
            self._set_import_modal_running(True)
            threading.Thread(
                target=self._full_shape_import_worker,
                args=(pid, template_count, shape_count, json_path, self.full_shape_clear_unused.get() == "1"),
                daemon=True,
            ).start()
            return
        # All checks passed — now it's safe to open the import log modal.
        self.import_running = True
        self._set_scoped_log_progress("import", 0, tr(self.lang, "importing"))
        self._show_import_log_modal("import")
        self.status.set(tr(self.lang, "running"))
        self._set_import_modal_running(True)
        threading.Thread(target=self._import_worker, args=(pid, paths), daemon=True).start()

    def _import_worker(self, pid, paths=None):
        try:
            paths = list(paths or self.json_files)
            game = self.selected_game.get() or "fh6"
            count_address = parse_hex_or_empty(self.count_address.get())
            table_address = parse_hex_or_empty(self.table_address.get())
            layer_count = self.layer_count.get().strip()
            if not count_address and not table_address and game == "fh6":
                clear_session_location()
                if pid and layer_count:
                    self.queue.put(("log", tr(self.lang, "locating")))
                    located = self._auto_locate_worker(pid, layer_count)
                    session = load_session_location()
                    if located and session_matches_current_import(session, game, pid, layer_count):
                        count_address = "0x{:x}".format(int(session["count_address"]))
                        table_address = "0x{:x}".format(int(session["table_address"]))
                        self.queue.put(("status", tr(self.lang, "importing")))
                    else:
                        self.queue.put(("status", tr(self.lang, "failed")))
                        return
            for path in paths:
                if game == "fh6" and layer_count:
                    self._check_json_layer_fit(path, layer_count)
                cmd = [*helper_command("main"), "--game", game, "--no-preview"]
                if pid:
                    cmd.extend(["--pid", str(pid)])
                if count_address:
                    cmd.extend(["--layer-count-address", count_address])
                if table_address:
                    cmd.extend(["--layer-table-address", table_address])
                if game == "fh6" and layer_count:
                    cmd.extend(["--layer-count-value", str(layer_count)])
                cmd.append(path)
                code = self.run_subprocess(cmd)
                if code != 0:
                    self.queue.put(("status", tr(self.lang, "failed")))
                    return
                self.queue.put(("market_imported_path", str(path)))
            self.queue.put(("status", tr(self.lang, "done")))
        except Exception as exc:
            self.queue.put(("log", tr(self.lang, "log_generator_failed").format(error=exc)))
            self.queue.put(("status", tr(self.lang, "failed")))
        finally:
            self.queue.put(("import_done", None))

    def start_diagnose(self):
        pid = self.ensure_live_game_pid()
        cmd = [*helper_command("main"), "--game", self.selected_game.get() or "fh6", "--diagnose"]
        if pid:
            cmd.extend(["--pid", str(pid)])
        self.status.set(tr(self.lang, "running"))
        threading.Thread(target=lambda: self._run_command_worker(cmd, 120), daemon=True).start()

    def start_save_snapshot(self):
        pid = self.ensure_live_game_pid()
        count = self.snapshot_count.get().strip() or self.layer_count.get().strip()
        if not pid or not count:
            self.log_line(tr(self.lang, "log_pid_and_snapshot_required"))
            return
        output_path = PROBE_DIR / f"memory-count-{count}.jsonl"
        cmd = [
            *helper_command("fh6_probe"),
            "--game",
            self.selected_game.get() or "fh6",
            "--pid",
            str(pid),
            "--layer-count",
            str(count),
            "--save-memory-snapshot",
            output_path,
            "--limit-mb",
            str(MEMORY_SNAPSHOT_LIMIT_MB),
        ]
        self.status.set(tr(self.lang, "running"))
        threading.Thread(target=lambda: self._run_command_worker(cmd, 360), daemon=True).start()

    def start_compare_snapshot(self):
        pid = self.ensure_live_game_pid()
        previous = self.snapshot_count.get().strip()
        current = self.current_count.get().strip() or self.layer_count.get().strip()
        if not pid or not previous or not current:
            self.log_line(tr(self.lang, "log_pid_snapshot_current_required"))
            return
        snapshot_path = PROBE_DIR / f"memory-count-{previous}.jsonl"
        candidates_path = PROBE_DIR / f"memory-count-{previous}-to-{current}-candidates.json"
        intersect_path = PROBE_DIR / f"memory-count-{int(previous) - 1}-to-{previous}-candidates.json"
        cmd = [
            *helper_command("fh6_probe"),
            "--game",
            self.selected_game.get() or "fh6",
            "--pid",
            str(pid),
            "--layer-count",
            str(current),
            "--compare-memory-snapshot",
            snapshot_path,
            "--write-candidates",
            candidates_path,
            "--max-matches",
            "50000",
        ]
        if intersect_path.exists():
            cmd.extend(["--intersect-candidates", intersect_path])
        self.status.set(tr(self.lang, "running"))
        threading.Thread(target=lambda: self._run_command_worker(cmd, 360), daemon=True).start()

    def start_inspect_table(self):
        pid = self.ensure_live_game_pid()
        table = self.inspect_table_value.get().strip()
        count = self.layer_count.get().strip()
        if not pid or not table or not count:
            self.log_line(tr(self.lang, "log_pid_count_table_required"))
            return
        cmd = [
            *helper_command("fh6_probe"),
            "--game",
            self.selected_game.get() or "fh6",
            "--pid",
            str(pid),
            "--layer-count",
            str(count),
            "--inspect-table",
            table,
            "--inspect-layers",
            "12",
        ]
        self.status.set(tr(self.lang, "running"))
        threading.Thread(target=lambda: self._run_command_worker(cmd, 60), daemon=True).start()

    def _run_command_worker(self, cmd, timeout):
        code = self.run_subprocess(cmd, timeout=timeout)
        self.queue.put(("status", tr(self.lang, "done") if code == 0 else tr(self.lang, "failed")))

    def _poll_queue(self):
        if self.closed:
            return
        while True:
            try:
                kind, payload = self.queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log_line(payload)
            elif kind == "scoped_log":
                scope, message = payload
                self.log_line(message, scope=scope)
            elif kind == "progress":
                self._set_scoped_log_progress(
                    self._active_log_scope(),
                    self._progress_from_text(payload),
                    payload,
                )
            elif kind == "scoped_progress":
                scope, value, text = payload
                self._set_scoped_log_progress(scope, value, text)
            elif kind == "status":
                self.status.set(payload)
                if getattr(self, "generate_log_modal", None) is not None:
                    self._set_generate_modal_progress(self.generate_progress_value, payload)
            elif kind == "import_done":
                done_scope = self.active_log_scope if self.active_log_scope in ("import", "full_shape") else None
                if done_scope:
                    status_text = self.status.get()
                    progress_value = 100 if status_text == tr(self.lang, "done") else None
                    self._set_scoped_log_progress(done_scope, progress_value, status_text)
                self.import_running = False
                self.full_shape_running = False
                self._set_import_modal_running(False)
                self.import_log_status.set(self.status.get())
                if self.active_log_scope in ("import", "full_shape"):
                    self.active_log_scope = None
            elif kind == "generation_done":
                stopped = self.shutdown_event.is_set()
                with self.generation_lock:
                    self.generation_running = False
                    self.current_generator_proc = None
                if hasattr(self, "generate_button"):
                    self.generate_button.config(state="normal")
                if hasattr(self, "stop_generate_button"):
                    self.stop_generate_button.config(state="disabled")
                if hasattr(self, "progress_bar"):
                    try:
                        self.progress_bar.stop()
                    except Exception:
                        pass
                if stopped and not self.closed:
                    self._set_scoped_log_progress("generate", self.generate_progress_value, tr(self.lang, "generation_stopped"))
                    self.status.set(tr(self.lang, "stopped"))
                    self._set_generate_modal_progress(self.generate_progress_value, tr(self.lang, "generation_stopped"))
                    self.log_line(tr(self.lang, "generation_stopped"))
                elif not self.closed:
                    status_text = self.status.get()
                    if status_text == tr(self.lang, "done"):
                        self._set_generate_modal_progress(100, tr(self.lang, "generation_progress_complete"))
                        self._set_scoped_log_progress("generate", 100, tr(self.lang, "generation_progress_complete"))
                    elif status_text == tr(self.lang, "failed"):
                        self._set_generate_modal_progress(self.generate_progress_value, tr(self.lang, "generation_progress_failed"))
                        self._set_scoped_log_progress("generate", self.generate_progress_value, tr(self.lang, "generation_progress_failed"))
                if self.active_log_scope == "generate":
                    self.active_log_scope = None
            elif kind == "preview":
                self.show_preview(payload)
                self._set_generate_modal_preview(data=payload)
            elif kind == "preview_json":
                self.show_json_preview(payload)
                self._set_generate_modal_preview(path=payload)
            elif kind == "preview_file":
                self.show_preview_file(payload)
                try:
                    payload_path = Path(payload).resolve()
                    source_path = Path(self.generate_modal_source_path).resolve() if self.generate_modal_source_path else None
                except Exception:
                    payload_path = None
                    source_path = None
                if payload_path is not None and payload_path != source_path:
                    self._set_generate_modal_preview(path=payload)
            elif kind == "generation_source_file":
                self._set_generate_modal_source(payload)
                self._set_generate_modal_preview_shadow(payload)
            elif kind == "generation_progress":
                self._set_generate_modal_progress(payload.get("value"), payload.get("text"))
                self._set_scoped_log_progress("generate", payload.get("value"), payload.get("text"))
            elif kind == "render_lists":
                self._render_lists()
            elif kind == "batch_state":
                self._set_batch_queue_state(payload.get("path"), payload.get("key"))
            elif kind == "market_items":
                self._render_market_items(payload)
            elif kind == "market_error":
                message = tr(self.lang, "market_error").format(error=payload)
                self._set_market_notice("market_error", error=payload)
                self.log_line(message)
            elif kind == "market_preview":
                self._show_market_preview(payload.get("id"), payload.get("data"))
            elif kind == "market_preview_error":
                if payload.get("id") == self.market_preview_item_id and self.market_preview_label is not None:
                    self.market_preview_label.config(image="", text=tr(self.lang, "preview_unavailable"))
                    self.market_preview_label.image = None
            elif kind == "market_downloaded":
                path = Path(payload.get("path"))
                if not path.is_file() or path.suffix.lower() != ".json":
                    message = tr(self.lang, "market_download_invalid").format(path=path)
                    self._set_market_notice("market_error", error=message)
                    self.log_line(message)
                    continue
                index_item = self._remember_market_download(
                    payload.get("item") or {},
                    payload.get("detail") or {},
                    payload.get("geometry") or {},
                    path,
                    reused=payload.get("reused"),
                )
                if path.exists() and path not in self.json_files:
                    self.json_files.append(path)
                template = payload.get("template")
                if template and not self.layer_count.get().strip():
                    self.layer_count.set(str(template))
                self._render_lists()
                if path in self.json_files and hasattr(self, "json_list"):
                    index = self.json_files.index(path)
                    self.json_list.selection_clear(0, END)
                    self.json_list.selection_set(index)
                    self.json_list.activate(index)
                self.show_json_preview(path)
                self._apply_market_filters()
                if payload.get("reused"):
                    message = tr(self.lang, "market_reused").format(path=path)
                    self._set_market_notice("market_reused_short")
                else:
                    message = tr(self.lang, "market_downloaded").format(path=path)
                    self._set_market_notice("market_downloaded_short")
                self.log_line(message)
                self._hide_market_modal()
            elif kind == "market_imported_path":
                self._mark_market_imported_path(payload)
                if self.market_modal is not None:
                    self._apply_market_filters()
            elif kind == "full_shape_json_path":
                self.full_shape_json_path.set(str(payload))
                self.show_json_preview(payload)
            elif kind == "full_shape_output_dir":
                self.full_shape_last_output_dir.set(str(payload))
            elif kind == "full_shape_report_dir":
                self.full_shape_last_report_dir.set(str(payload))
                if hasattr(self, "full_shape_report_button"):
                    self.full_shape_report_button.config(state="normal")
            elif kind == "full_shape_failed_prompt":
                self.show_full_shape_failure_prompt(payload)
            elif kind == "region_log":
                self.log_line(self._localize_region_line(payload), scope="region")
            elif kind == "region_progress":
                localized = self._localize_region_line(payload)
                self.region_progress.set(localized)
                self._set_scoped_log_progress("region", self._progress_from_text(payload), localized)
            elif kind == "region_preview":
                try:
                    self._region_display_preview(Path(payload))
                except Exception:
                    pass
            elif kind == "region_status":
                self.region_status.set(payload)
                if getattr(self, "import_log_modal_scope", None) == "region":
                    self.import_log_status.set(self.region_status.get())
                self.region_workflow_running = False
                self._region_update_button_states()
            elif kind == "region_done":
                self.region_workflow_running = False
                self.shutdown_event.clear()
                if self.active_log_scope == "region":
                    self.active_log_scope = None
                result = payload or {}
                if result.get("ok"):
                    self.region_status.set(tr(self.lang, "done"))
                    if getattr(self, "import_log_modal_scope", None) == "region":
                        self.import_log_status.set(self.region_status.get())
                    self.region_progress.set(tr(self.lang, "region_progress_idle"))
                    if result.get("new_total"):
                        self.region_progress.set(tr(self.lang, "region_total_layers_done").format(total=result["new_total"]))
                    self._set_scoped_log_progress("region", 100, self.region_progress.get())
                    pass_label = tr(self.lang, "region_pass_complete")
                    if self.region_current_output_dir:
                        try:
                            status = region_get_status(self.region_current_output_dir)
                            self.region_pass_list.delete(0, END)
                            for i, p in enumerate(status.get("passes", []), 1):
                                key = "region_history_region" if p.get("mask") else "region_history_first"
                                label = tr(self.lang, key).format(index=i, layers=p.get("layers", 0))
                                self.region_pass_list.insert(END, label)
                            passes = status.get("passes", [])
                            if passes:
                                last = passes[-1]
                                key = "region_history_region" if last.get("mask") else "region_history_first"
                                pass_label = tr(self.lang, "region_pass_complete_detail").format(
                                    pass_label=tr(self.lang, key).format(index=len(passes), layers=last.get("layers", 0))
                                )
                            self.region_remaining_var.set(str(status.get("remaining", 0)))
                        except Exception:
                            pass
                    self.log_line(pass_label, scope="region")
                    self._region_clear_mask()
                    preview_path = result.get("preview_path")
                    if not preview_path:
                        preview_path = Path(self.region_current_output_dir) / "preview.png"
                    else:
                        preview_path = Path(preview_path)
                    if preview_path.exists():
                        if self._region_right_tab == "preview":
                            self._region_display_preview(preview_path)
                        else:
                            self._region_preview_showing = str(preview_path)
                    heatmap_path = result.get("heatmap_path")
                    if heatmap_path:
                        heatmap_p = Path(heatmap_path)
                        if heatmap_p.exists():
                            if self._region_right_tab == "heatmap":
                                self._region_display_heatmap(heatmap_p)
                            else:
                                self._region_heatmap_showing = str(heatmap_p)
                            if self.region_tab_heatmap_btn:
                                self.region_tab_heatmap_btn.config(fg=Theme.TEXT, cursor="hand2")
                else:
                    self.region_status.set(tr(self.lang, "failed"))
                    if getattr(self, "import_log_modal_scope", None) == "region":
                        self.import_log_status.set(self.region_status.get())
                    error = self._localize_region_line(result.get("error", tr(self.lang, "unknown_error")))
                    self.region_progress.set(error)
                    self._set_scoped_log_progress("region", None, error)
                    self.log_line(tr(self.lang, "region_failed_detail").format(error=error), scope="region")
                self._region_update_button_states()
            elif kind == "region_canvas_update":
                self._region_display_image(payload)
            elif kind == "update_failed":
                self._handle_update_failed(payload)
            elif kind == "update_current":
                self._handle_update_current(payload)
            elif kind == "update_available":
                self._handle_update_available(payload)
        if not self.closed:
            self.root.after(100, self._poll_queue)

    def run(self):
        self.root.mainloop()


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--helper":
        run_embedded_helper(sys.argv[2], sys.argv[3:])
        return
    parser = argparse.ArgumentParser(description=f"Standalone {APP_DISPLAY_NAME} desktop app.")
    parser.add_argument("--version", action="version", version=f"{APP_DISPLAY_NAME} {__version__}")
    parser.add_argument("images", nargs="*", help="Optional image files to preload.")
    args = parser.parse_args()
    App(args.images).run()


if __name__ == "__main__":
    main()
