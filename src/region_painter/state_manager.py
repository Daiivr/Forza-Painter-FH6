"""Workflow state persistence for region-focused iterative painting.

Persists workflow progress to a ``state.json`` file so that multi-pass
generation can be paused and resumed across application restarts.
"""

from __future__ import annotations

import json
from pathlib import Path


class StateManager:
    """Manages the lifecycle of a single region-paint workflow.

    All persisted data lives under ``{work_dir}/state.json``.
    """

    def __init__(self, work_dir: str | Path) -> None:
        self._work_dir = Path(work_dir)
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self._work_dir / "state.json"
        self._data: dict = {}
        self.load()

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def load(self) -> dict:
        """Load state from disk, or return an empty default."""
        if self._state_path.exists():
            try:
                self._data = json.loads(self._state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}
        self._ensure_defaults()
        return self._data

    def save(self) -> None:
        """Persist current state to disk atomically."""
        text = json.dumps(self._data, indent=2, ensure_ascii=False)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self._state_path)

    # ------------------------------------------------------------------
    # Initialization helpers
    # ------------------------------------------------------------------

    def init_first_pass(
        self,
        original_image: str,
        original_ini: str,
        total_budget: int,
        working_width: int,
        working_height: int,
        max_resolution: int,
        max_preview_size: int,
    ) -> None:
        """Set up a fresh workflow before the first pass runs."""
        self._data = {
            "original_image": str(original_image),
            "original_ini": str(original_ini),
            "total_budget": total_budget,
            "used_layers": 0,
            "working_width": working_width,
            "working_height": working_height,
            "max_resolution": max_resolution,
            "max_preview_size": max_preview_size,
            "base_json": "",
            "target_path": "",
            "preview_path": "",
            "passes": [],
        }
        self.save()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def total_budget(self) -> int:
        return int(self._data.get("total_budget", 0))

    @property
    def used_layers(self) -> int:
        return int(self._data.get("used_layers", 0))

    @property
    def remaining_budget(self) -> int:
        return max(0, self.total_budget - self.used_layers)

    @property
    def is_first_pass_done(self) -> bool:
        return self.used_layers > 0 and len(self._data.get("passes", [])) >= 1

    @property
    def working_width(self) -> int:
        return int(self._data.get("working_width", 0))

    @property
    def working_height(self) -> int:
        return int(self._data.get("working_height", 0))

    @property
    def max_resolution(self) -> int:
        return int(self._data.get("max_resolution", 1200))

    @property
    def max_preview_size(self) -> int:
        return int(self._data.get("max_preview_size", 500))

    @property
    def target_path(self) -> str:
        return str(self._data.get("target_path", ""))

    @target_path.setter
    def target_path(self, value: str) -> None:
        self._data["target_path"] = str(value)

    @property
    def base_json(self) -> str:
        return str(self._data.get("base_json", ""))

    @base_json.setter
    def base_json(self, value: str) -> None:
        self._data["base_json"] = str(value)

    @property
    def preview_path(self) -> str:
        return str(self._data.get("preview_path", ""))

    @preview_path.setter
    def preview_path(self, value: str) -> None:
        self._data["preview_path"] = str(value)

    @property
    def passes(self) -> list[dict]:
        return list(self._data.get("passes", []))

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_pass(
        self,
        mask_path: str | None,
        layers: int,
        json_path: str,
    ) -> None:
        """Record a completed generation pass."""
        entry: dict = {
            "mask": str(mask_path) if mask_path else None,
            "layers": layers,
            "json": str(json_path),
        }
        self._data.setdefault("passes", []).append(entry)
        self._data["used_layers"] = sum(
            p.get("layers", 0) for p in self._data["passes"]
        )
        self.save()

    def reset(self) -> None:
        """Clear all state for a fresh workflow."""
        self._data = {}
        self._ensure_defaults()
        if self._state_path.exists():
            try:
                self._state_path.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_defaults(self) -> None:
        self._data.setdefault("total_budget", 0)
        self._data.setdefault("used_layers", 0)
        self._data.setdefault("working_width", 0)
        self._data.setdefault("working_height", 0)
        self._data.setdefault("max_resolution", 1200)
        self._data.setdefault("max_preview_size", 500)
        self._data.setdefault("original_image", "")
        self._data.setdefault("original_ini", "")
        self._data.setdefault("base_json", "")
        self._data.setdefault("target_path", "")
        self._data.setdefault("preview_path", "")
        self._data.setdefault("passes", [])
