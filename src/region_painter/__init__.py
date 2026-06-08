"""Region-focused iterative painting workflow.

Implements staged geometry generation where:
- First pass generates a base layer budget across the entire image.
- Subsequent passes focus on user-selected regions via alpha-mask + exe -resume.
"""

__all__ = [
    "ini_manager",
    "state_manager",
    "image_processor",
    "preview_renderer",
    "workflow",
]
