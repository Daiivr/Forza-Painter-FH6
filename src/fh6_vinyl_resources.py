from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

try:
    from app_paths import RESOURCE_ROOT
except Exception:
    RESOURCE_ROOT = Path(__file__).resolve().parent.parent


VINYL_TYPE_BASES = {
    "Primitives": 1048677,
    "Community_Vinyls_1": 1050677,
    "Community_Vinyls_2": 1050777,
    "Community_Vinyls_3": 1050877,
    "Community_Vinyls_4": 1050977,
    "Gradient_Shapes": 1048777,
    "Stripes": 1048877,
    "Tears": 1048977,
    "Racing_Icons": 1049077,
    "Flames": 1049177,
    "Paint_Splats": 1049277,
    "Tribal": 1049377,
    "Nature": 1049477,
    "Upper_Letters_1": 1050477,
    "Lower_Letters_1": 1050577,
    "Upper_Letters_2": 1049877,
    "Lower_Letters_2": 1049977,
    "Upper_Letters_3": 1050077,
    "Lower_Letters_3": 1050177,
    "Upper_Letters_4": 1050277,
    "Lower_Letters_4": 1050377,
    "Upper_Letters_5": 1051077,
    "Lower_Letters_5": 1051177,
    "Upper_Letters_6": 1051277,
    "Lower_Letters_6": 1051377,
    "Upper_Letters_7": 1051477,
    "Lower_Letters_7": 1051577,
    "Upper_Letters_8": 1051677,
    "Lower_Letters_8": 1051777,
    "Upper_Letters_9": 1051877,
    "Lower_Letters_9": 1051977,
    "Upper_Letters_10": 1052077,
    "Lower_Letters_10": 1052177,
    "Upper_Letters_11": 1052277,
    "Lower_Letters_11": 1052377,
}


def vinyl_resource_roots():
    module_root = Path(__file__).resolve().parent
    candidates = [
        module_root / "data" / "fh6_vinyl_resources" / "Vinyls",
        Path(RESOURCE_ROOT) / "data" / "fh6_vinyl_resources" / "Vinyls",
        Path(RESOURCE_ROOT) / "src" / "data" / "fh6_vinyl_resources" / "Vinyls",
    ]
    seen = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        yield resolved


def type_code_to_resource(type_code):
    try:
        numeric = int(type_code)
    except (TypeError, ValueError):
        return None
    for family, base in VINYL_TYPE_BASES.items():
        index = numeric - base + 1
        if 1 <= index <= 80:
            return family, index
    return None


def resource_path_for_type_code(type_code):
    resolved = type_code_to_resource(type_code)
    if not resolved:
        return None
    family, index = resolved
    for root in vinyl_resource_roots():
        path = root / family / str(index)
        if path.exists():
            return path
    return None


@lru_cache(maxsize=512)
def load_vinyl_polygons(type_code):
    path = resource_path_for_type_code(int(type_code))
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    vertices = payload.get("Vertices") or []
    indices = payload.get("Indices") or []
    points = []
    for vertex in vertices:
        try:
            points.append((float(vertex["X"]), float(vertex["Y"])))
        except (KeyError, TypeError, ValueError):
            points.append(None)
    valid = [point for point in points if point is not None]
    if not valid:
        return None
    min_x = min(point[0] for point in valid)
    max_x = max(point[0] for point in valid)
    min_y = min(point[1] for point in valid)
    max_y = max(point[1] for point in valid)
    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    polygons = []
    for i in range(0, len(indices) - 2, 3):
        try:
            p0 = points[int(indices[i])]
            p1 = points[int(indices[i + 1])]
            p2 = points[int(indices[i + 2])]
        except (IndexError, TypeError, ValueError):
            continue
        if p0 is None or p1 is None or p2 is None:
            continue
        polygons.append(
            (
                (p0[0] - center_x, p0[1] - center_y),
                (p1[0] - center_x, p1[1] - center_y),
                (p2[0] - center_x, p2[1] - center_y),
            )
        )
    if not polygons:
        return None
    return {
        "type_code": int(type_code),
        "bounds": (min_x - center_x, min_y - center_y, max_x - center_x, max_y - center_y),
        "polygons": polygons,
    }
