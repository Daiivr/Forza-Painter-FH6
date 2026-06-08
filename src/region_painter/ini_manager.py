"""INI file read/modify/write for staged geometry generation.

Modifies only ``stopAt`` and ``saveAt`` fields; all other settings are
preserved verbatim from the original profile INI.
"""

from __future__ import annotations

from pathlib import Path


def modify_ini(
    original_path: str | Path,
    output_path: str | Path,
    stop_at: int,
    save_at: list[int] | None = None,
) -> None:
    """Read *original_path*, patch ``stopAt``/``saveAt``, write to *output_path*.

    Parsing rules:
        - Skip lines starting with ``#`` or ``;`` (comments).
        - Skip blank lines.
        - ``key = value`` format, trimmed.
        - Unknown keys are preserved unchanged.

    If *save_at* is ``None``, ``saveAt`` is set to *stop_at* (a single
    checkpoint at completion).  Any values in the original ``saveAt``
    that are greater than *stop_at* are dropped.
    """
    original_path = Path(original_path)
    output_path = Path(output_path)

    lines: list[str] = []
    stop_at_patched = False
    save_at_patched = False

    with open(original_path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            stripped = raw.strip()

            # Preserve comments and blank lines.
            if not stripped or stripped.startswith("#") or stripped.startswith(";"):
                lines.append(raw.rstrip("\n"))
                continue

            if "=" not in stripped:
                lines.append(raw.rstrip("\n"))
                continue

            key, _sep, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()

            if key == "stopAt":
                lines.append(f"stopAt = {stop_at}")
                stop_at_patched = True
            elif key == "saveAt":
                filtered = _filter_save_at(value, save_at, stop_at)
                lines.append(f"saveAt = {filtered}")
                save_at_patched = True
            else:
                lines.append(raw.rstrip("\n"))

    if not stop_at_patched:
        lines.append(f"stopAt = {stop_at}")
    if not save_at_patched:
        filtered = _filter_save_at("", save_at, stop_at)
        lines.append(f"saveAt = {filtered}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _filter_save_at(
    original_value: str,
    save_at: list[int] | None,
    stop_at: int,
) -> str:
    """Build the final ``saveAt`` value.

    - If *save_at* is explicitly provided, use only values ≤ *stop_at*.
    - Otherwise parse *original_value*, filter values > *stop_at*, and
      ensure *stop_at* itself is included.
    """
    if save_at is not None:
        filtered = sorted(v for v in save_at if v <= stop_at)
        return ",".join(str(v) for v in filtered) if filtered else str(stop_at)

    # Parse original comma-separated list.
    values: list[int] = []
    for part in original_value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            v = int(part)
        except ValueError:
            continue
        if v <= stop_at:
            values.append(v)

    if stop_at not in values:
        values.append(stop_at)

    return ",".join(str(v) for v in sorted(values))
