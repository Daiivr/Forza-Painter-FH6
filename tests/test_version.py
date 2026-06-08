from __future__ import annotations

from version import APP_DISPLAY_NAME, __version__, app_title


def test_app_title_omits_version():
    assert app_title() == APP_DISPLAY_NAME
    assert __version__ not in app_title()
