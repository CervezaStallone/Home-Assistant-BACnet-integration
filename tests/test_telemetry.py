"""Tests for the opt-in anonymous usage report (telemetry.py).

The server rejects the whole report with 400 when anything is outside its
contract, so the payload limits are pinned here.
"""

from __future__ import annotations

import uuid

from custom_components.bacnet.telemetry import (
    build_report,
    clean_text,
    device_report,
    object_counts,
)


def test_clean_text_strips_control_chars_and_cuts():
    assert clean_text("V6.00\n.338\t\x00", 128) == "V6.00.338"
    assert clean_text("  x  ", 128) == "x"
    assert clean_text("a" * 300, 128) == "a" * 128
    assert clean_text(None, 128) == ""


def test_object_counts_keys_limits():
    objs = [{"object_type": 0}] * 3 + [{"object_type": 19}] + [{"object_type": 2000}]
    assert object_counts(objs) == {"0": 3, "19": 1}
    many = [{"object_type": t} for t in range(100)]
    assert list(object_counts(many)) == [str(t) for t in range(64)]
    assert object_counts([{"object_type": 0}] * 100001) == {"0": 100000}


def test_device_report_fields_and_connection():
    data = {
        "vendor_name": "Siemens",
        "model_name": "PXC200-E.D",
        "firmware_version": "V6.00.338",
        "use_bbmd": True,
        "device_address": "10.0.0.5",
        "device_id": 1234,
    }
    rep = device_report(data, [{"object_type": 2}], cov=True, rpm=False)
    assert rep == {
        "vendor": "Siemens",
        "model": "PXC200-E.D",
        "firmware": "V6.00.338",
        "connection": "foreign_device",
        "cov": True,
        "rpm": False,
        "objects": {"2": 1},
    }
    empty = device_report({}, [], cov=False, rpm=True)
    assert empty["connection"] == "direct"
    assert empty["vendor"] == ""


def test_build_report_shape():
    install_id = str(uuid.uuid4())
    rep = build_report(install_id.upper(), "1.0.50-beta.10", "2026.10.1", [{}] * 300)
    assert set(rep) == {
        "schema",
        "install_id",
        "integration_version",
        "ha_version",
        "devices",
    }
    assert rep["schema"] == 1
    assert len(rep["devices"]) == 256
    assert rep["install_id"] == install_id
