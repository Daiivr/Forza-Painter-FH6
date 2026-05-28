# Changelog

## v1.6.8.1 / 2026-05-28

- Updated the app version to `v1.6.8.1`; release packages now use `forza-painter-fh6-v1.6.8.1.exe`.

### Sidebar & step indicators
- Redrew the sidebar nav icons (Generate JSON, Import, Tutorial) as antialiased PIL renders at 4× supersample then downscaled with LANCZOS. The Tutorial glyph now reads as an open book instead of the previous question-mark badge, the Generate glyph uses `{ }` curly braces with a center sparkle, and the Import glyph is a downward arrow into a notched inbox. Hover and active states swap the pre-rendered tinted variant of the same icon rather than re-coloring individual canvas items.
- Replaced the thin-outline step number badges (Paso 1 / 2 / 3 on both the Generate JSON and Import tabs) with solid accent-blue discs that have a soft outer halo and white bold numerals, supersampled and downscaled for crisp edges at the same 24 px footprint.

### Themed warning alerts on the Import tab
- Added a borderless themed warning dialog (`_show_themed_alert`) that matches the existing dark-mode modal chrome: traffic-light dots, dark panel, hairline divider, warning-triangle glyph, accent-blue action button. Replaces the prior native `messagebox.showwarning` calls.
- Plays the standard Windows warning chime (`winsound.MessageBeep(MB_ICONEXCLAMATION)`) when the alert spawns. Non-Windows platforms fall back to the Tk bell.
- `Return` / `Escape` and the close button all dismiss the alert; the action button is focused on open so the alert can be cleared from the keyboard.

### Import validation flow
- Validating the import inputs now happens before the import log modal opens. Previously, clicking "Importar JSON" with a missing template layer count opened the heavy log modal first, which disabled the root window on Windows and left the layer-count entry briefly unresponsive after the modal was closed.
- "No JSON files selected", "Template layer count is required", and "No live supported game process is selected" each now surface as the themed alert with the appropriate translated message instead of writing to the log area that the Import tab hides.
- After the missing-layer-count alert is dismissed, the layer-count entry is auto-focused so the user can start typing immediately without having to click it.

### Modal behavior on Windows
- All three modals (warning alert, quality settings, import log) now register their own Windows taskbar entry. A new helper (`_ensure_window_in_taskbar`) flips the borderless Toplevel's extended style from `WS_EX_TOOLWINDOW` to `WS_EX_APPWINDOW` via ctypes so the modal shows up as its own item the user can click from the taskbar to bring back to the front.
- All three modals set `-topmost` so they float above the main window for the duration of their modality.
- All three modals re-activate the main window with `root.lift()` + `root.focus_force()` after dismissal, eliminating the "can't type until I alt-tab away and back" issue that the `root.attributes("-disabled", True)` modality trick caused on Windows.
- Added an `_on_root_activated` handler bound to the root window's `<FocusIn>` and `<Map>` events that re-raises the currently active modal whenever the main app is re-activated from the taskbar, so the modal does not get stuck behind the root window.
- Removed `transient(self.root)` from the quality settings, import log, and warning alert toplevels. The transient flag was telling Windows these were tool windows and hiding them from the taskbar, which conflicted with the dedicated taskbar-entry behavior above.

## v1.6.8 / 2026-05-28

- Updated the app version to `v1.6.8`; release packages now use `forza-painter-fh6-v1.6.8.exe`.
- Integrated the latest GitHub `main` changes from PR #87, keeping ellipse width/height as floating-point values for more accurate FH6 imports.
- Added an in-app preview note explaining that v1.6.8 favors better in-game results while previews remain approximate.
- Improved JSON preview rendering with supersampling so float-sized ellipses look less jagged or degraded in the preview panel.

## v1.6.7 / 2026-05-27

- Updated the app version to `v1.6.7`; release packages now use `forza-painter-fh6-v1.6.7.exe`.
- Updated the bundled GPU generator to upstream `canary-26052702`.
- Replaced FH6 import scale magic numbers with named `FH6_CIRCLE_BASE_SIZE` and `FH6_RECTANGLE_BASE_SIZE` constants so the in-game circle base size can be adjusted explicitly.
- Improved generation ETA estimation so buffered generator output and early slow phases do not keep the remaining-time display wildly inaccurate.

## v1.6.6 / 2026-05-26

- Updated the app version to `v1.6.6`; release packages now use `forza-painter-fh6-v1.6.6.exe`.
- Added Traditional Chinese UI translations and a wider top-right language selector.
- Fixed `luma_band` preprocessing for RGB images and made preprocessed-image writes atomic.
- Packaged OpenCV and NumPy into the one-file EXE so `luma_band` preprocessing works in release builds.
- Import now requires the FH6 template layer count before starting, preventing confusing auto-locate/import failures caused by an empty layer-count field.
- Refactored core modules with typed exceptions, frozen dataclasses, and shared utility helpers.
- Added pytest coverage for geometry JSON normalization and internal color/shape data classes.

## v1.6.5 / 2026-05-25

- Updated the app version to `v1.6.5`; release packages now use `forza-painter-fh6-v1.6.5.exe`.
- Updated the bundled GPU generator to upstream `v1.2-Canary-20260525`.
- Bundled presets now set `forceOpaqueShapes = false` by default so transparent source regions can remain transparent unless users override the setting.
- Generator launches now use a sanitized environment to avoid external Python, Conda, WebUI, Vulkan, or OpenCL override variables affecting the bundled GPU generator.
- Reduced generation-time filesystem polling so the app checks previews and JSON outputs less aggressively while the GPU generator is running.
- The heaviest bundled preset now uses `previewEvery = 100` instead of `previewEvery = 1`, avoiding excessive preview PNG writes during high-resolution generation.
- Fixed output discovery for preprocessed generation inputs so generated JSON and preview files are tracked from the actual image passed to the GPU generator.

## v1.6.1 / 2026-05-24

- Updated the app version to `v1.6.1`; release packages now use `forza-painter-fh6-v1.6.1.exe`.
- Disabled `luma_band` preprocessing by default in bundled presets.
- Import no longer reuses stale FH6 session data from `webui-data`; it re-locates the current template before writing.
- JSON previews now use one stable renderer path to avoid ellipse preview distortion differences between packaged EXE environments.

## v1.6.0 / 2026-05-24

- Updated the app version to `v1.6.0`; release packages now use `forza-painter-fh6-v1.6.0.exe`.
- Updated the bundled GPU generator to upstream `canary-26052401`.
- Added upstream `errorGridSize` preset support.
- Integrated the upstream transparent-area overhang prevention algorithm adjustment.
- Significantly improved generation quality for the large ellipse at the bottom of transparent images.

## v1.5.4 / 2026-05-23

- Fixed preview scaling for high-resolution source images, generator preview PNG files, and JSON previews.
- Previews now adapt to the current preview panel size while preserving the original image aspect ratio, avoiding stretched or partially visible previews when using large max-resolution settings such as 3000.
- Fixed JSON preview rendering for type 16 rotated ellipses in the packaged EXE by making the Pillow fallback follow the historical OpenCV preview coordinate conversion.

## v1.5.3 / 2026-05-22

- Added user preset import for the one-file EXE; imported `.ini` presets are stored in the external `config/settings/` folder beside the app.
- Added remove buttons for the selected image and selected JSON entries.
- Improved checkpoint handling: existing checkpoint JSON files are detected and reusable checkpoints are added to the Import list after failed or stopped generation.
- Fixed JSON output discovery when source image filenames contain extra dots, such as `image.1png.png`.
- Improved generation progress logs when the GPU generator recycles fully covered layers, so the UI no longer looks like generation restarted from an earlier layer.
- Added a Pillow-based preview fallback and packaged it into the EXE so fresh one-file installs can preview images and JSON without OpenCV.

## v1.5.2 / 2026-05-22

- Added a PyInstaller-based one-file EXE so normal users no longer need to install Python, create `.venv`, or keep helper files beside the app.
- The GUI EXE now re-launches itself in hidden helper mode for import and FH6 memory probing.
- The Tools page and startup log now show where external runtime/cache files are stored.
- Fixed the batch bootstrap variable-expansion bug that could run `-m venv` instead of `python -m venv`.
- Added a repeatable `scripts/make_exe_release.ps1` build script for the one-file EXE package.

## v1.5.1 / 2026-05-22

- Fixed startup dependency installation when a project `.venv` exists but its Python does not have `pip`; the bootstrapper now runs `ensurepip --upgrade` before installing requirements.
- Improved startup-script diagnostics when required release files are missing, with a clear message to fully extract the release ZIP first.

## v1.5.0 / 2026-05-22

- Added a startup update check against the GitHub `main` branch version file.
- When update checking fails, the app shows a small `!` indicator in the top-right corner; clicking it shows the failure reason.
- When a newer version is available, the app displays this changelog section and lets the user open the update page.
- Switched the desktop UI to a dark theme for better contrast during long generation and import sessions.
- Updated the bundled GPU/OpenCL generator to upstream `canary-26052102`.
- Added the upstream work-group evaluation algorithm from PR #4, reducing GPU candidate-evaluation overhead and improving generation throughput on supported OpenCL devices.
- `start_app.bat` now bootstraps the project-local `.venv`: it installs missing dependencies and then launches the app.
- Dependency installation now uses `.venv` instead of installing packages into the global Python environment.

## v1.4.1 / 2026-05-21

- FH6 template auto-location now tries both the v1.3 small/medium-region address-order scan and the v1.4 large-region chunked scan before giving up.
- Added an RTTI vtable fallback locator for difficult FH6 sessions while keeping the existing safe table validation before writing.
- Raised the FH6 auto-location budget to 300 seconds, with a 360-second outer watchdog timeout.
- Added a user-facing wait message before FH6 auto-location starts, warning users to keep the Vinyl Group Editor open and avoid switching menus.

## v1.4.0 / 2026-05-21

- Added detailed log export with a 50000-character output limit.
- Detailed logs include helper/generator raw output, commands, exit codes, process/template state, and current session data.
- Improved FH6 template auto-location by scanning large writable private memory regions in 4 MB chunks.
- Increased the FH6 auto-location scan budget to 120 seconds and the outer watchdog timeout to 160 seconds.

## v1.3.0 / 2026-05-21

- Updated the bundled GPU/OpenCL generator to upstream `canary-26052101`.
- Added the upstream generator device-selection fix, prioritizing NVIDIA GPUs with the most VRAM.
- Generation logs now show the selected OpenCL device.
- Improved FH6 template auto-locate failure handling so stale session cache is not reported as a newly verified template.
