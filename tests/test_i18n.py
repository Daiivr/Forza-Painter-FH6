from __future__ import annotations

from i18n import TEXT, tr


def test_translation_keys_are_audited_against_english():
    english_keys = set(TEXT["en"])
    for lang, translations in TEXT.items():
        assert set(translations) == english_keys, lang


def test_translation_fallback_returns_human_text():
    assert tr("missing-language", "ready_json") == TEXT["en"]["ready_json"]
    assert tr("en", "missing_key") == "missing_key"
