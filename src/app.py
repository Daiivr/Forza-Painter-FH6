from __future__ import annotations

import argparse
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
import urllib.request
import webbrowser
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import BOTH, BOTTOM, END, LEFT, RIGHT, X, Y, Button, Canvas, Checkbutton, Entry, Frame, Label, Listbox, PhotoImage, StringVar, Text, Tk, Toplevel, filedialog, messagebox, ttk

import psutil

from app_paths import ROOT, RESOURCE_ROOT
from game_profiles import PROFILES
from geometry_json import RECTANGLE, ROTATED_ELLIPSE, load_normalized_geometry
from generator_backend import GENERATOR_EXE, GENERATOR_JSON_SCAN_SECONDS, GENERATOR_POLL_SLEEP_SECONDS, GENERATOR_PREVIEW_SCAN_SECONDS, USER_SETTINGS_DIR, best_geometry_jsons, build_generator_command, build_generator_env, generated_jsons, generated_preview_files, generator_preview_path, load_settings, preprocess_input_image, write_custom_settings, write_user_settings_preset
from version import APP_DISPLAY_NAME, __version__, app_title


from app_config import (
    APP_DIR,
    PROBE_DIR,
    SESSION_PATH,
    MEMORY_SNAPSHOT_LIMIT_MB,
    PREVIEW_MAX,
    DETAILED_LOG_OUTPUT_LIMIT,
    DETAILED_LOG_MEMORY_LIMIT,
    FH6_AUTO_LOCATE_MAX_SECONDS,
    FH6_AUTO_LOCATE_TIMEOUT_SECONDS,
    UPDATE_VERSION_URL,
    UPDATE_CHANGELOG_URL,
    UPDATE_RELEASE_URL,
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


# Removed: TEXT dictionary (now in i18n.py)


def ensure_dirs():
    PROBE_DIR.mkdir(parents=True, exist_ok=True)


def set_windows_app_user_model_id():
    if platform.system() != "Windows":
        return
    try:
        import ctypes

        app_id = "bvzrays.forza-painter-fh6"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


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


def render_source_image(path, max_size=None):
    loaded = load_cv2()
    if loaded:
        cv2, _np = loaded
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
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


def render_geometry_json(path, max_size=None):
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
        top.bind("<Escape>", lambda _e: self._close_dropdown(), add="+")
        top.bind("<FocusOut>", lambda _e: self.after(120, self._close_if_focus_left), add="+")
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
                top.destroy()
            except Exception:
                pass
        self._set_resting()


class App:
    def __init__(self, initial_images):
        ensure_dirs()
        set_windows_app_user_model_id()
        self.root = Tk()
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
        self.update_state = {"status": "checking"}
        self.update_dialog = None
        self.update_check_started = False
        self.status = StringVar(value=tr(self.lang, "ready"))
        self.progress_text = StringVar(value="")
        self.import_log_status = StringVar(value="")
        self.selected_profile = StringVar()
        self.selected_game = StringVar(value="fh6")
        self.selected_pid = StringVar()
        self.layer_count = StringVar()
        self.snapshot_count = StringVar()
        self.current_count = StringVar()
        self.count_address = StringVar()
        self.table_address = StringVar()
        self.inspect_table_value = StringVar()
        self.runtime_folder = StringVar(value=str(ROOT))
        self.output_folder = StringVar(value="")
        self.advanced_visible = False
        self.brand_mark_photo = None
        self.brand_mark_icon = None
        self.brand_logo_photo = None
        self.log_area = None
        self.log_area_visible = False
        self.import_log_modal = None
        self.import_modal_log = None
        self.import_modal_progress = None
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
        self.tools_tab = Frame(self.content_area, bg=Theme.BG)
        self.tutorial_tab = Frame(self.content_area, bg=Theme.BG)
        self.section_frames = {
            "generate": self.generate_tab,
            "import": self.import_tab,
            "tools": self.tools_tab,
            "tutorial": self.tutorial_tab,
        }

        self._build_generate_tab()
        self._build_import_tab()
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
        brand.pack(fill=X, padx=22, pady=(26, 30))
        mark_row = Frame(brand, bg=Theme.PANEL)
        mark_row.pack(fill=X)
        if self.brand_mark_photo is not None:
            mark = Label(mark_row, image=self.brand_mark_photo, bg=Theme.PANEL, bd=0)
            mark.image = self.brand_mark_photo
            mark.pack(side=LEFT, padx=(0, 12))
        else:
            mark = Canvas(mark_row, width=34, height=34, bg=Theme.PANEL, highlightthickness=0)
            mark.create_rectangle(4, 4, 30, 30, outline=Theme.BORDER_STRONG, width=1)
            mark.create_polygon(8, 26, 26, 8, 22, 4, 4, 22, fill=Theme.ACCENT, outline="")
            mark.create_oval(20, 20, 28, 28, fill=Theme.SUCCESS, outline=Theme.PANEL, width=2)
            mark.pack(side=LEFT, padx=(0, 12))
        title_block = Frame(mark_row, bg=Theme.PANEL)
        title_block.pack(side=LEFT, fill=X, expand=True)
        Label(title_block, text="Forza-Painter", bg=Theme.PANEL, fg=Theme.TEXT,
              font=(Theme.FONT_FAMILY, 13, "bold"), anchor="w").pack(fill=X)
        Label(title_block, text=f"FH6  ·  v{__version__}", bg=Theme.PANEL, fg=Theme.MUTED,
              font=(Theme.FONT_FAMILY, 9), anchor="w").pack(fill=X, pady=(1, 0))

        # Nav section label
        self._label(sidebar, "workspace", bg=Theme.PANEL, fg=Theme.SUBTLE,
                    font=(Theme.FONT_FAMILY, 8, "bold"), anchor="w").pack(fill=X, padx=22, pady=(0, 8))

        nav = Frame(sidebar, bg=Theme.PANEL)
        nav.pack(fill=X, padx=12)
        for key, label_key, glyph in (
            ("generate", "generate_tab", "spark"),
            ("import", "import_tab", "download"),
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
        logo_path = RESOURCE_ROOT / "assets" / "imgs" / "brand_logo.png"
        if not (mark_path.exists() or logo_path.exists()):
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
            self.brand_mark_photo = fit(mark_path, 52, 52)
            try:
                icon_img = Image.open(mark_path).convert("RGBA")
                icon_img.thumbnail((128, 128), Image.Resampling.LANCZOS)
                self.brand_mark_icon = ImageTk.PhotoImage(icon_img)
                self.root.iconphoto(True, self.brand_mark_icon)
            except Exception:
                pass
        if logo_path.exists():
            self.brand_logo_photo = fit(logo_path, 460, 96)

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
            "tools": "tools_tab",
            "tutorial": "tutorial_tab",
        }.get(getattr(self, "current_section", "generate"), "generate_tab"))

    def _section_subtitle_text(self):
        return tr(self.lang, {
            "generate": "scroll_hint",
            "import": "step_import_hint",
            "tutorial": "subtitle",
        }.get(getattr(self, "current_section", "generate"), "subtitle"))

    def _select_section(self, key):
        if key not in self.section_frames:
            return
        if key != "generate":
            self._hide_quality_settings_modal()
            if hasattr(self, "profile_combo"):
                self.profile_combo._close_dropdown()
        for section_key, frame in self.section_frames.items():
            try:
                frame.pack_forget()
            except Exception:
                pass
        self.section_frames[key].pack(fill=BOTH, expand=True)
        self.current_section = key
        self._set_log_area_visible(key != "import")
        if hasattr(self, "section_title"):
            self.section_title.config(text=self._section_title_text())
        if hasattr(self, "section_subtitle"):
            self.section_subtitle.config(text=self._section_subtitle_text())
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

        title_block = Frame(topbar, bg=Theme.BG)
        title_block.pack(side=LEFT, fill=X, expand=True)
        self.section_title = Label(
            title_block, text=self._section_title_text(),
            bg=Theme.BG, fg=Theme.TEXT, font=(Theme.FONT_FAMILY, 22, "bold"), anchor="w",
        )
        self.section_title.pack(anchor="w")
        self.section_subtitle = Label(
            title_block, text=self._section_subtitle_text(),
            bg=Theme.BG, fg=Theme.MUTED, font=(Theme.FONT_FAMILY, 10), anchor="w",
        )
        self.section_subtitle.pack(anchor="w", pady=(2, 0))

        # Horizontal brand logo sits between the title block and the status pill
        if self.brand_logo_photo is not None:
            logo_label = Label(topbar, image=self.brand_logo_photo, bg=Theme.BG, bd=0)
            logo_label.image = self.brand_logo_photo
            logo_label.pack(side=LEFT, padx=(24, 24))

        right_block = Frame(topbar, bg=Theme.BG)
        right_block.pack(side=RIGHT)

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

    # ---------------------------------------------------------------------- card
    def _card(self, parent, title_key, step=None, side_pack=None, eyebrow=None):
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
                            fg=Theme.TEXT, font=(Theme.FONT_FAMILY, 12, "bold"), anchor="w")
        title_label.pack(fill=X)
        self.translated.append((title_label, title_key, "text"))

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

        def _sync_scroll(_event=None):
            left_canvas.configure(scrollregion=left_canvas.bbox("all"))

        def _match_width(event):
            left_canvas.itemconfigure(scroll_window, width=event.width)

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
        self.image_empty_hint = Label(list_inner, text=tr(self.lang, "add_images") + " —",
                                      bg=Theme.INPUT, fg=Theme.SUBTLE,
                                      font=(Theme.FONT_FAMILY, 9, "italic"))
        self._update_image_empty_state()

        quality_border = Frame(scroll_inner, bg=Theme.BORDER)
        quality_border.pack(fill=X, pady=(0, 12))
        quality_panel = Frame(quality_border, bg=Theme.PANEL)
        quality_panel.pack(fill=X, padx=1, pady=1)
        quality_inner = Frame(quality_panel, bg=Theme.PANEL)
        quality_inner.pack(fill=X, padx=22, pady=14)
        quality_copy = Frame(quality_inner, bg=Theme.PANEL, width=420)
        quality_copy.pack(side=LEFT, fill=BOTH, expand=True)
        quality_copy.pack_propagate(False)
        self.setting_description = Label(
            quality_copy, text="", anchor="w", justify=LEFT,
            wraplength=410, fg=Theme.MUTED, bg=Theme.PANEL,
            font=(Theme.FONT_FAMILY, 9),
        )
        self.setting_description.pack(fill=BOTH, expand=True)
        quality_button_box = Frame(quality_inner, bg=Theme.PANEL, width=150, height=52)
        quality_button_box.pack(side=RIGHT, padx=(16, 0))
        quality_button_box.pack_propagate(False)
        self._primary_button(
            quality_button_box, "quality_settings", self.open_quality_settings_modal, variant="accent",
            padx=10, pady=6, font=(Theme.FONT_FAMILY, 10, "bold"),
            wraplength=118, justify="center"
        ).pack(fill=BOTH, expand=True)

        # ----- card 4: generate CTA (pinned below scroll, in left_col) -----
        cta_card = self._card(left_col, "generate_step_run", step=2, side_pack={"fill": X, "pady": (8, 0)})
        self._label(cta_card, "generate_step_run_hint", anchor="w", justify=LEFT,
                    wraplength=540, fg=Theme.MUTED, font=(Theme.FONT_FAMILY, 9)).pack(fill=X, pady=(0, 12))
        actions = Frame(cta_card, bg=Theme.PANEL)
        actions.pack(fill=X)
        self.generate_button = self._primary_button(actions, "start_generate", self.start_generate)
        self.generate_button.pack(side=LEFT, fill=X, expand=True, ipady=4)
        self.stop_generate_button = self._danger_button(actions, "stop_generate", self.stop_generate, state="disabled")
        self.stop_generate_button.pack(side=LEFT, padx=10, ipady=4)
        open_output_btn = self._button(actions, "open_output", self.open_output_folder)
        open_output_btn.pack(side=LEFT, ipady=4)

        # ----- right column: preview card -----
        preview_card = self._card(right_col, "preview", side_pack={"fill": BOTH, "expand": True})
        self._label(preview_card, "preview_accuracy_note", anchor="w", justify=LEFT,
                    wraplength=500, fg=Theme.WARN, font=(Theme.FONT_FAMILY, 8)).pack(fill=X, pady=(0, 8))
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
                top.lift()
                top.focus_force()
                try:
                    top.attributes("-topmost", True)
                except Exception:
                    pass
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

        shell = Frame(top, bg=Theme.BG)
        shell.pack(fill=BOTH, expand=True, padx=1, pady=1)
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
        top.lift()
        try:
            top.attributes("-topmost", True)
        except Exception:
            pass
        self._ensure_window_in_taskbar(top)
        self._activate_modal(top)
        top.focus_force()

    def _activate_modal(self, top):
        self.active_modal = top
        try:
            top.lift()
            top.focus_force()
        except Exception:
            pass
        if os.name == "nt":
            try:
                self.root.attributes("-disabled", True)
            except Exception:
                pass
        try:
            top.lift()
            top.focus_force()
        except Exception:
            pass
        try:
            top.grab_set()
        except Exception:
            pass

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
        self._label(card1, "step_game_hint", anchor="w", justify=LEFT, wraplength=760,
                    fg=Theme.MUTED, font=(Theme.FONT_FAMILY, 9)).pack(fill=X, pady=(0, 8))
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
        self._label(card1, "step_template_hint", anchor="w", justify=LEFT, wraplength=760,
                    fg=Theme.MUTED, font=(Theme.FONT_FAMILY, 9)).pack(fill=X, pady=(0, 8))
        template_row = Frame(card1, bg=Theme.PANEL)
        template_row.pack(fill=X)
        self._label(template_row, "layer_count", font=(Theme.FONT_FAMILY, 10, "bold")).pack(side=LEFT)
        self.layer_count_entry = Entry(template_row, textvariable=self.layer_count,
                                       width=14, font=(Theme.FONT_FAMILY, 14, "bold"))
        self.layer_count_entry.pack(side=LEFT, padx=12, ipady=6)

        # ----- card 3: JSON files -----
        card3 = self._card(left, "step_json", step=2, side_pack={"fill": BOTH, "expand": True, "pady": (0, 8)})
        self._label(card3, "step_json_hint", anchor="w", justify=LEFT, wraplength=540,
                    fg=Theme.MUTED, font=(Theme.FONT_FAMILY, 9)).pack(fill=X, pady=(0, 10))
        row = Frame(card3, bg=Theme.PANEL)
        row.pack(fill=X)
        self._label(row, "json_files", font=(Theme.FONT_FAMILY, 10, "bold")).pack(side=LEFT)
        self._button(row, "add_json", self.add_json).pack(side=RIGHT)
        self._button(row, "remove_json", self.remove_selected_json).pack(side=RIGHT, padx=(8, 0))
        self._button(row, "use_outputs", self.use_generated_outputs).pack(side=RIGHT, padx=8)
        list_wrap = Frame(card3, bg=Theme.BORDER)
        list_wrap.pack(fill=BOTH, expand=True, pady=(10, 0))
        list_inner = Frame(list_wrap, bg=Theme.INPUT)
        list_inner.pack(fill=BOTH, expand=True, padx=1, pady=1)
        self.json_list = Listbox(list_inner, height=5, borderwidth=0, highlightthickness=0)
        self.json_list.pack(fill=BOTH, expand=True, padx=10, pady=8)
        self.json_list.bind("<<ListboxSelect>>", self._preview_selected_json)
        self.json_empty_hint = Label(list_inner, text=tr(self.lang, "add_json") + " —",
                                     bg=Theme.INPUT, fg=Theme.SUBTLE,
                                     font=(Theme.FONT_FAMILY, 9, "italic"))
        self._update_json_empty_state()

        # ----- right column: import CTA + preview -----
        card4 = self._card(right, "step_import", step=3)
        self._label(card4, "step_import_hint", anchor="w", justify=LEFT, wraplength=500,
                    fg=Theme.MUTED, font=(Theme.FONT_FAMILY, 9)).pack(fill=X, pady=(0, 6))
        self._label(card4, "easy_import_hint", anchor="w", justify=LEFT, wraplength=500,
                    fg=Theme.SUBTLE, font=(Theme.FONT_FAMILY, 9)).pack(fill=X, pady=2)
        self._label(card4, "admin_note", anchor="w", justify=LEFT, wraplength=500,
                    fg=Theme.WARN, font=(Theme.FONT_FAMILY, 9)).pack(fill=X, pady=(2, 12))
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
        self._label(preview_card, "preview_accuracy_note", anchor="w", justify=LEFT,
                    wraplength=500, fg=Theme.WARN, font=(Theme.FONT_FAMILY, 8)).pack(fill=X, pady=(0, 8))
        preview_inner = Frame(preview_card, bg=Theme.BORDER)
        preview_inner.pack(fill=BOTH, expand=True)
        self.import_preview_label = Label(
            preview_inner, text=tr(self.lang, "preview_hint"),
            bg=Theme.PREVIEW_BG, fg=Theme.MUTED, font=(Theme.FONT_FAMILY, 10),
        )
        self.import_preview_label.pack(fill=BOTH, expand=True, padx=1, pady=1)
        self.import_preview_label.bind("<Configure>", self._schedule_preview_refresh)

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
        self.progress_bar = ttk.Progressbar(prog_block, mode="indeterminate",
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

        shell = Frame(top, bg=Theme.BG)
        shell.pack(fill=BOTH, expand=True, padx=1, pady=1)

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
        top.lift()
        # Float above the root and register a taskbar entry so the user can
        # always navigate back to the alert from anywhere on Windows.
        try:
            top.attributes("-topmost", True)
        except Exception:
            pass
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

    def _show_import_log_modal(self):
        if self.import_log_modal is not None:
            try:
                if self.import_log_modal.winfo_exists():
                    self.import_log_modal.deiconify()
                    self.import_log_modal.lift()
                    self.import_log_modal.focus_force()
                    try:
                        self.import_log_modal.attributes("-topmost", True)
                    except Exception:
                        pass
                    self._ensure_window_in_taskbar(self.import_log_modal)
                    self._activate_modal(self.import_log_modal)
                    return
            except Exception:
                pass

        top = Toplevel(self.root)
        self.import_log_modal = top
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
        shell = Frame(top, bg=Theme.BG)
        shell.pack(fill=BOTH, expand=True, padx=1, pady=1)
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
        self.import_modal_progress = ttk.Progressbar(
            prog_block, mode="indeterminate", style="App.Horizontal.TProgressbar", length=180
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
        try:
            existing = self.log.get("1.0", END)
            if existing.strip():
                self.import_modal_log.insert(END, existing)
                self.import_modal_log.see(END)
        except Exception:
            pass
        self._apply_dark_theme_recursive(top)
        self._center_toplevel(top, 920, 380)
        top.deiconify()
        top.lift()
        try:
            top.attributes("-topmost", True)
        except Exception:
            pass
        self._ensure_window_in_taskbar(top)
        self._activate_modal(top)

    def _set_import_modal_running(self, running):
        progress = self.import_modal_progress
        if progress is None:
            return
        try:
            if running:
                progress.start(80)
            else:
                progress.stop()
        except Exception:
            pass

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
        if self.photo is None:
            self.preview_label.config(text=tr(self.lang, "preview_hint"))
            if hasattr(self, "import_preview_label"):
                self.import_preview_label.config(text=tr(self.lang, "preview_hint"))
        if hasattr(self, "advanced_button"):
            self.advanced_button.config(text=tr(self.lang, "hide_advanced" if self.advanced_visible else "show_advanced"))
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
        for path in self.images:
            self.image_list.insert(END, str(path))
        self.json_list.delete(0, END)
        for path in self.json_files:
            self.json_list.insert(END, str(path))
        self._update_image_empty_state()
        self._update_json_empty_state()

    def _update_image_empty_state(self):
        hint = getattr(self, "image_empty_hint", None)
        if hint is None:
            return
        if self.images:
            hint.pack_forget()
        else:
            hint.pack(anchor="w", padx=14, pady=(2, 8))

    def _update_json_empty_state(self):
        hint = getattr(self, "json_empty_hint", None)
        if hint is None:
            return
        if self.json_files:
            hint.pack_forget()
        else:
            hint.pack(anchor="w", padx=14, pady=(2, 8))

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

    def _insert_log_entry(self, message, timestamp=None, line_tag=None, record_detail=True, index=END):
        timestamp = timestamp or datetime.now().strftime("%H:%M:%S.%f")[:-3]
        if record_detail:
            self._record_detail(f"UI: {message}")
        text = str(message)
        msg_tag = self._log_message_tag(text)
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
            if index == END:
                self._append_import_modal_log(timestamp, text, msg_tag)
            return start, end
        except Exception:
            self.log.insert(END, f"[{timestamp}] {text}\n")
            if index == END:
                self._append_import_modal_log(timestamp, text, msg_tag)
            return None, None

    def log_line(self, message):
        self._insert_log_entry(message)
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
            message = self._progress_with_eta(friendly)
            if not message:
                return last_message
            self.queue.put(("progress", message))
            self.queue.put(("log", message))
            return friendly
        if friendly == "FINISHED":
            self.queue.put(("progress", self._localize_generator_line(friendly)))
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
        added_paths = []
        for item in files:
            path = Path(item)
            if path.exists() and path not in self.images:
                self.images.append(path)
                added_paths.append(path)
                self._load_existing_checkpoints_for_image(path)
        self._render_lists()
        if files:
            self.show_source_preview(Path(files[0]))
        if added_paths:
            output_dir = self._selected_output_folder()
            existing_added = sum(1 for path in added_paths if generated_jsons(path, output_dir))
            if existing_added:
                self.log_line(tr(self.lang, "cannot_resume_checkpoint"))

    def remove_selected_image(self):
        selection = list(self.image_list.curselection())
        if not selection:
            self.log_line(tr(self.lang, "no_image_selected"))
            return
        for index in sorted(selection, reverse=True):
            try:
                del self.images[index]
            except IndexError:
                pass
        self._render_lists()
        self.preview_label.config(image="", text=tr(self.lang, "preview_hint"))
        self.preview_label.image = None

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

    def add_json(self):
        files = filedialog.askopenfilenames(
            title="Choose geometry JSON",
            filetypes=[("Geometry JSON", "*.json"), ("All files", "*.*")],
        )
        for item in files:
            path = Path(item)
            if path.exists() and path not in self.json_files:
                self.json_files.append(path)
        self._render_lists()
        if files:
            self.show_json_preview(Path(files[0]))

    def remove_selected_json(self):
        selection = list(self.json_list.curselection())
        if not selection:
            self.log_line(tr(self.lang, "no_json_selected"))
            return
        for index in sorted(selection, reverse=True):
            try:
                del self.json_files[index]
            except IndexError:
                pass
        self._render_lists()
        if hasattr(self, "import_preview_label"):
            self.import_preview_label.config(image="", text=tr(self.lang, "preview_hint"))
            self.import_preview_label.image = None

    def use_generated_outputs(self):
        for path in self.outputs:
            if path.exists() and path not in self.json_files:
                self.json_files.append(path)
        self._render_lists()
        self.log_line(tr(self.lang, "log_added_outputs").format(count=len(self.outputs)))

    def _preview_selected_image(self, _event=None):
        selection = self.image_list.curselection()
        if selection:
            self.show_source_preview(self.images[selection[0]])

    def _preview_selected_json(self, _event=None):
        selection = self.json_list.curselection()
        if selection:
            self.show_json_preview(self.json_files[selection[0]])

    def _active_preview_label(self):
        if getattr(self, "current_section", None) == "import" and hasattr(self, "import_preview_label"):
            return self.import_preview_label
        return getattr(self, "preview_label", None)

    def _preview_bounds(self, label=None):
        label = label or self._active_preview_label()
        if label is None:
            return PREVIEW_MAX, PREVIEW_MAX
        try:
            self.root.update_idletasks()
            width = label.winfo_width()
            height = label.winfo_height()
        except Exception:
            width = height = 0
        if width <= 32 or height <= 32:
            return PREVIEW_MAX, PREVIEW_MAX
        return max(1, width - 16), max(1, height - 16)

    def _schedule_preview_refresh(self, _event=None):
        if not self.current_preview_request or self.closed:
            return
        if self.preview_resize_job is not None:
            try:
                self.root.after_cancel(self.preview_resize_job)
            except Exception:
                pass
        self.preview_resize_job = self.root.after(180, self._refresh_current_preview)

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
            data = render_source_image(path, self._preview_bounds())
        self.show_preview(data)

    def show_json_preview(self, path):
        path = Path(path)
        self.current_preview_request = ("json", path)
        self.show_preview(render_geometry_json(path, self._preview_bounds()))

    def show_preview(self, data):
        if not data:
            self.current_preview_request = None
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
        self.current_preview_request = ("source", path)
        data = render_source_image(path, self._preview_bounds())
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
            self.current_preview_request = ("file", path)
        data = render_source_image(path, self._preview_bounds())
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
        with self.generation_lock:
            if self.generation_running:
                self.log_line(tr(self.lang, "log_already_running"))
                return
            self.generation_running = True
        if not self.images:
            with self.generation_lock:
                self.generation_running = False
            self.log_line(tr(self.lang, "log_no_images_selected"))
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
        self.status.set(tr(self.lang, "running"))
        if hasattr(self, "generate_button"):
            self.generate_button.config(state="disabled")
        if hasattr(self, "stop_generate_button"):
            self.stop_generate_button.config(state="normal")
        if hasattr(self, "progress_bar"):
            try:
                self.progress_bar.start(80)
            except Exception:
                pass
        threading.Thread(target=self._generate_worker, args=(setting, output_dir), daemon=True).start()

    def _generate_worker(self, setting, output_dir=None):
        try:
            self.queue.put(("log", tr(self.lang, "log_selected_profile").format(name=setting['path'].name)))
            self._log_generation_load_warning(setting)
            for image_path in list(self.images):
                if self.shutdown_event.is_set():
                    self.queue.put(("status", tr(self.lang, "stopped")))
                    return
                self._reset_generation_eta()
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
                            self.queue.put(("status", tr(self.lang, "stopped")))
                            return
                        _drain_generator_output()
                        now = time.monotonic()
                        if now >= next_preview_scan:
                            next_preview_scan = now + GENERATOR_PREVIEW_SCAN_SECONDS
                            preview_files = generated_preview_files(input_image)
                            if preview_files:
                                newest_preview = preview_files[0]
                                preview_mtime = newest_preview.stat().st_mtime
                                if preview_mtime != last_preview_mtime:
                                    last_preview_mtime = preview_mtime
                                    self.queue.put(("preview_file", newest_preview))
                        if now >= next_json_scan:
                            next_json_scan = now + GENERATOR_JSON_SCAN_SECONDS
                            newest = generated_jsons(input_image, output_dir)
                            if newest and newest[0] != last_preview:
                                last_preview = newest[0]
                        time.sleep(GENERATOR_POLL_SLEEP_SECONDS)
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
                    self.queue.put(("log", self._generator_exit_message(proc.returncode)))
                    self.queue.put(("status", tr(self.lang, "failed")))
                    return
                self._record_detail("GENERATOR EXIT: 0")
                new_outputs = self._queue_generated_outputs(input_image, before, output_dir)
                if not new_outputs:
                    self.queue.put(("log", tr(self.lang, "log_generator_no_output")))
                    self.queue.put(("status", tr(self.lang, "failed")))
                    return
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

    def run_subprocess(self, cmd, timeout=None):
        self._record_detail(f"HELPER COMMAND: {self._format_command(cmd)}")
        self.queue.put(("log", self._friendly_command_name(cmd)))
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
                    self._record_detail(f"HELPER RAW: {line.rstrip()}")
                    friendly = self._friendly_subprocess_line(line.rstrip())
                    if friendly:
                        self.queue.put(("log", friendly))
                if proc.poll() is not None:
                    break
                if timeout and time.time() - started > timeout:
                    self._terminate_process(proc)
                    self._record_detail(f"HELPER EXIT: 124 timeout after {timeout} seconds")
                    self.queue.put(("log", tr(self.lang, "log_timed_out").format(seconds=timeout)))
                    return 124
                time.sleep(0.05)
            if self.shutdown_event.is_set():
                self._record_detail("HELPER EXIT: 130 stopped after process exit")
                return 130
            for line in proc.stdout.read().splitlines():
                self._record_detail(f"HELPER RAW: {line.rstrip()}")
                friendly = self._friendly_subprocess_line(line.rstrip())
                if friendly:
                    self.queue.put(("log", friendly))
            self._record_detail(f"HELPER EXIT: {proc.returncode}")
            return proc.returncode
        finally:
            self._unregister_process(proc)

    def _friendly_command_name(self, cmd):
        joined = " ".join(str(x) for x in cmd)
        if "fh6_probe.py" in joined and "--auto-locate" in joined:
            return tr(self.lang, "locating")
        if "main.py" in joined:
            return tr(self.lang, "importing")
        return tr(self.lang, "log_starting_helper")

    def _check_json_layer_fit(self, json_path, layer_count):
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
        match = re.match(r"(.+?) detected as (.+?) \(pid (\d+)\)$", raw)
        if match:
            game, process, pid = match.groups()
            return tr(self.lang, "log_detected_process").format(game=game, process=process, pid=pid)
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

    def start_import(self):
        # Validate inputs BEFORE opening the modal. Opening the modal disables the
        # root window on Windows; if validation fails and the modal is then closed,
        # the OS leaves the root briefly unresponsive. Keep the modal closed until
        # we actually have work to run.  Since the log area is hidden on the import
        # tab, surface the error with a native alert so the user knows what to fix.
        alert_title = tr(self.lang, "import_tab")
        if not self.json_files:
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
        # All checks passed — now it's safe to open the import log modal.
        self._show_import_log_modal()
        self.status.set(tr(self.lang, "running"))
        self._set_import_modal_running(True)
        threading.Thread(target=self._import_worker, args=(pid,), daemon=True).start()

    def _import_worker(self, pid):
        try:
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
            for path in list(self.json_files):
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
            elif kind == "progress":
                self.progress_text.set(payload)
            elif kind == "status":
                self.status.set(payload)
            elif kind == "import_done":
                self._set_import_modal_running(False)
                self.import_log_status.set(self.status.get())
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
                    self.progress_text.set(tr(self.lang, "generation_stopped"))
                    self.status.set(tr(self.lang, "stopped"))
                    self.log_line(tr(self.lang, "generation_stopped"))
            elif kind == "preview":
                self.show_preview(payload)
            elif kind == "preview_json":
                self.show_json_preview(payload)
            elif kind == "preview_file":
                self.show_preview_file(payload)
            elif kind == "render_lists":
                self._render_lists()
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
