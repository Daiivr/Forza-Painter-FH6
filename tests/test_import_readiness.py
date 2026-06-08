from __future__ import annotations

from import_readiness import layer_fit, parse_positive_int, readiness_checks


def test_parse_positive_int():
    assert parse_positive_int("1000") == 1000
    assert parse_positive_int("0") is None
    assert parse_positive_int("abc") is None


def test_layer_fit_states():
    assert layer_fit(1000, "").status == "missing_template"
    assert layer_fit(1000, "1003").status == "trimmed"
    assert layer_fit(100, "1004").status == "blurry"
    assert layer_fit(900, "1004").status == "great"
    assert layer_fit(2800, "1800").recommended_template_layers == 2804


def test_readiness_checks_keys_and_values():
    checks = readiness_checks(
        has_json=True,
        template_layers="1000",
        has_process=True,
        is_admin=False,
        has_manual_addresses=False,
        has_session=True,
    )
    assert [key for key, _ok in checks] == [
        "ready_json",
        "ready_template",
        "ready_process",
        "ready_admin",
        "ready_locator",
        "ready_ungrouped",
    ]
    assert [ok for _key, ok in checks] == [True, True, True, False, True, True]
