from __future__ import annotations

from dataclasses import dataclass

FH_RESERVED_BOUND_LAYERS = 4


@dataclass(frozen=True)
class LayerFit:
    status: str
    drawable_layers: int
    template_layers: int | None
    usable_layers: int | None
    recommended_template_layers: int | None
    message_key: str


def parse_positive_int(value: object) -> int | None:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def layer_fit(drawable_layers: int, template_layers: object) -> LayerFit:
    template = parse_positive_int(template_layers)
    recommended = drawable_layers + FH_RESERVED_BOUND_LAYERS if drawable_layers else None
    if not drawable_layers:
        return LayerFit("unknown", 0, template, None, None, "compat_unknown")
    if template is None:
        return LayerFit("missing_template", drawable_layers, None, None, recommended, "compat_needs_template")

    usable = max(0, template - FH_RESERVED_BOUND_LAYERS)
    if usable <= 0:
        return LayerFit("template_too_small", drawable_layers, template, usable, recommended, "compat_template_too_small")
    if drawable_layers > usable:
        return LayerFit("trimmed", drawable_layers, template, usable, recommended, "compat_trimmed")
    if drawable_layers < usable * 0.75:
        return LayerFit("blurry", drawable_layers, template, usable, recommended, "compat_blurry")
    return LayerFit("great", drawable_layers, template, usable, recommended, "compat_great")


def readiness_checks(
    *,
    has_json: bool,
    template_layers: object,
    has_process: bool,
    is_admin: bool,
    has_manual_addresses: bool,
    has_session: bool,
) -> list[tuple[str, bool]]:
    template_ok = parse_positive_int(template_layers) is not None
    return [
        ("ready_json", bool(has_json)),
        ("ready_template", template_ok),
        ("ready_process", bool(has_process)),
        ("ready_admin", bool(is_admin)),
        ("ready_locator", bool(has_manual_addresses or has_session)),
        ("ready_ungrouped", template_ok and bool(has_process)),
    ]
