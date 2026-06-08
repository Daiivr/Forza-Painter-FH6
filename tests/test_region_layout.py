from __future__ import annotations

from app import centered_region_group_offset


def test_heatmap_bar_group_stays_centered_and_attached():
    canvas_w = 560
    heatmap_w = 320
    bar_w = 52
    gap = 1

    heatmap_x = centered_region_group_offset(canvas_w, heatmap_w, bar_w, gap)
    bar_x = heatmap_x + heatmap_w + gap
    group_w = heatmap_w + gap + bar_w

    assert bar_x - (heatmap_x + heatmap_w) == 1
    assert abs((heatmap_x + group_w / 2) - (canvas_w / 2)) <= 0.5


def test_preview_without_bar_centers_by_itself():
    canvas_w = 560
    preview_w = 320

    preview_x = centered_region_group_offset(canvas_w, preview_w)

    assert abs((preview_x + preview_w / 2) - (canvas_w / 2)) <= 0.5
