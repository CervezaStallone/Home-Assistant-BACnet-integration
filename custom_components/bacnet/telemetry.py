"""Opt-in anonymous usage report.

Once a day, while at least one config entry has "share usage" ticked, one
report per Home Assistant installation goes to the telemetry server. The
server rejects the whole report when anything is outside its contract, so
every value is cleaned and cut here.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Mapping
from typing import Any

from .const import (
    CONF_FIRMWARE_VERSION,
    CONF_MODEL_NAME,
    CONF_USE_BBMD,
    CONF_VENDOR_NAME,
    TELEMETRY_SCHEMA,
)

_MAX_VERSION = 32
_MAX_DEVICES = 256
_MAX_TEXT = 128
_MAX_OBJECT_TYPE = 1023
_MAX_OBJECT_TYPES = 64
_MAX_OBJECT_COUNT = 100000


def clean_text(value: object, limit: int) -> str:
    """Text without control characters, trimmed and cut to limit."""
    if value is None:
        return ""
    text = "".join(c for c in str(value) if unicodedata.category(c) != "Cc")
    return text.strip()[:limit]


def object_counts(objects: list[dict]) -> dict[str, int]:
    """Imported objects per object type, lowest types first, within limits."""
    counts = Counter(
        o["object_type"] for o in objects if 0 <= o["object_type"] <= _MAX_OBJECT_TYPE
    )
    return {
        str(t): min(counts[t], _MAX_OBJECT_COUNT)
        for t in sorted(counts)[:_MAX_OBJECT_TYPES]
    }


def device_report(
    entry_data: Mapping[str, Any], objects: list[dict], *, cov: bool, rpm: bool
) -> dict:
    """One device in the report. Never addresses, instances or names."""
    return {
        "vendor": clean_text(entry_data.get(CONF_VENDOR_NAME), _MAX_TEXT),
        "model": clean_text(entry_data.get(CONF_MODEL_NAME), _MAX_TEXT),
        "firmware": clean_text(entry_data.get(CONF_FIRMWARE_VERSION), _MAX_TEXT),
        "connection": "foreign_device" if entry_data.get(CONF_USE_BBMD) else "direct",
        "cov": bool(cov),
        "rpm": bool(rpm),
        "objects": object_counts(objects),
    }


def build_report(
    install_id: str, integration_version: str, ha_version: str, devices: list[dict]
) -> dict:
    """The report the server accepts: exactly these keys, nothing else."""
    return {
        "schema": TELEMETRY_SCHEMA,
        "install_id": install_id.lower(),
        "integration_version": clean_text(integration_version, _MAX_VERSION),
        "ha_version": clean_text(ha_version, _MAX_VERSION),
        "devices": devices[:_MAX_DEVICES],
    }
