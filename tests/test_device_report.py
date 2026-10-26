"""scripts/device_report.py: device compatibility reports → tested devices list.

The report comes from a public issue form, so everything in it is
untrusted text that ends up in TESTED_DEVICES.md.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "device_report", Path(__file__).parent.parent / "scripts" / "device_report.py"
)
device_report = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(device_report)

_BODY = """### Manufacturer

Siemens

### Model

PXC200-E.D

### Firmware version

_No response_

### BACnet integration version

1.0.50-beta.6

### Connection

Same subnet

### What works

- [X] Reading values
- [x] COV (live updates)
- [ ] Writing values
- [ ] Schedules
- [ ] Alarms
- [ ] Trend logs

### Notes

Runs 14 controllers.
"""


def test_entry_from_issue_form():
    assert device_report.entry_from_issue(_BODY, 70) == {
        "vendor": "Siemens",
        "model": "PXC200-E.D",
        "firmware": "",
        "version": "1.0.50-beta.6",
        "connection": "Same subnet",
        "works": ["Reading values", "COV (live updates)"],
        "notes": "Runs 14 controllers.",
        "issue": 70,
    }


def test_render_table_escapes_untrusted_text():
    devices = [
        {
            "vendor": "Evil | Corp",
            "model": "<img src=x onerror=alert(1)>\n![x](http://t)",
            "firmware": "",
            "version": "1.0.50",
            "connection": "Same subnet",
            "works": ["Reading values", "COV (live updates)"],
            "notes": "",
            "issue": 70,
        }
    ]
    table = device_report.render_table(devices)
    row = table.splitlines()[-1]
    assert "<img" not in row and "![" not in row and "\n" not in row
    assert "http://t" not in row  # GitHub would turn a bare URL into a link
    assert "| Evil Corp |" in row
    assert row == "| Evil Corp | img srcx onerroralert1 | — |"


def test_add_report_updates_json_and_table(tmp_path):
    import json

    devices = tmp_path / "devices.json"
    devices.write_text(
        '[{"vendor": "Old", "model": "M", "version": "1.0", "issue": 70}]'
    )
    table = tmp_path / "TESTED_DEVICES.md"
    table.write_text(
        "intro\n<!-- tested-devices:start -->\nstale\n<!-- tested-devices:end -->\nend\n"
    )
    device_report.add_report(_BODY, 70, devices, table)

    # A report edited and verified again replaces its earlier entry.
    (entry,) = json.loads(devices.read_text())
    assert entry["model"] == "PXC200-E.D"
    text = table.read_text()
    assert "stale" not in text
    assert text.startswith("intro\n<!-- tested-devices:start -->\n| Manufacturer")
    assert "| Siemens | PXC200-E.D |" in text
    assert text.endswith("<!-- tested-devices:end -->\nend\n")


def test_render_table_lists_only_make_model_and_firmware():
    """No report links or feature columns: just what it works on."""
    table = device_report.render_table(
        [
            {"vendor": "V", "model": "M", "firmware": "", "version": "", "issue": 1},
            {"vendor": "A", "model": "B", "firmware": "2.1", "version": "", "issue": 2},
        ]
    )
    assert table.splitlines() == [
        "| Manufacturer | Model | Firmware |",
        "|---|---|---|",
        "| A | B | 2.1 |",
        "| V | M | — |",
    ]
