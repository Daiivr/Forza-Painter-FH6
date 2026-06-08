from __future__ import annotations

from app_paths import ROOT, SOURCE_DIR


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
APP_DIR = SOURCE_DIR
PROBE_DIR = ROOT / "webui-data" / "probes"
SESSION_PATH = PROBE_DIR / "current-fh6-session.json"
FULL_SHAPE_ROOT = ROOT / "runtime" / "full-shape-io"


# ---------------------------------------------------------------------------
# Limits & thresholds
# ---------------------------------------------------------------------------
MEMORY_SNAPSHOT_LIMIT_MB = 2048
PREVIEW_MAX = 520
DETAILED_LOG_OUTPUT_LIMIT = 50000
DETAILED_LOG_MEMORY_LIMIT = 120000
FH6_AUTO_LOCATE_MAX_SECONDS = 300
FH6_AUTO_LOCATE_TIMEOUT_SECONDS = 360
UPDATE_CHECK_TIMEOUT_SECONDS = 8


# ---------------------------------------------------------------------------
# Update URLs
# ---------------------------------------------------------------------------
UPDATE_VERSION_URL = (
    "https://raw.githubusercontent.com/Daiivr/Forza-Painter-FH6/main/src/version.py"
)
UPDATE_CHANGELOG_URL = (
    "https://raw.githubusercontent.com/Daiivr/Forza-Painter-FH6/main/CHANGELOG.md"
)
UPDATE_RELEASE_URL = "https://github.com/Daiivr/Forza-Painter-FH6/releases/latest"
MARKET_URL = "https://painter6.com"


# ---------------------------------------------------------------------------
# Theme colours (refined slate professional dark)
# ---------------------------------------------------------------------------
class Theme:
    # Surfaces (OLED-leaning dark)
    BG = "#070b13"
    PANEL = "#0f1520"
    PANEL_ALT = "#161d2b"
    PANEL_HEADER = "#131a26"
    INPUT = "#0a0f1a"
    PREVIEW_BG = "#070b13"

    # Text
    TEXT = "#e6ebf2"
    TEXT_ON_ACCENT = "#ffffff"
    MUTED = "#8a94a6"
    SUBTLE = "#5d6675"

    # Brand / status
    ACCENT = "#4c9aff"
    ACCENT_DARK = "#1d6feb"
    ACCENT_SOFT = "#7ab8ff"
    WARN = "#f5b544"
    SUCCESS = "#3fb950"
    SUCCESS_DARK = "#2ea043"
    SUCCESS_HOVER = "#46c459"
    DANGER = "#f85149"
    DANGER_HOVER = "#ff6259"

    # Section header (the small uppercase label above a group)
    SECTION = "#9fb6d4"

    # Lines & controls
    BORDER = "#1f2937"
    BORDER_STRONG = "#2a3445"
    BUTTON = "#1a2230"
    BUTTON_HOVER = "#222d3f"
    BUTTON_ACTIVE = "#2a3648"

    # Typography
    FONT_FAMILY = "Segoe UI"
    FONT_MONO = "Cascadia Mono"
