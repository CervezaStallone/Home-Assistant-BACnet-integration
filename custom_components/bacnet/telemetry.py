"""Opt-in anonymous usage report.

Once a day, while at least one config entry has "share usage" ticked, one
report per Home Assistant installation goes to the telemetry server. The
server rejects the whole report when anything is outside its contract, so
every value is cleaned and cut here.
"""

from __future__ import annotations

import logging
import unicodedata
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

import aiohttp
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration

from .const import (
    CONF_FIRMWARE_VERSION,
    CONF_MODEL_NAME,
    CONF_SHARE_USAGE,
    CONF_USE_BBMD,
    CONF_VENDOR_NAME,
    DOMAIN,
    TELEMETRY_FIRST_DELAY,
    TELEMETRY_INTERVAL,
    TELEMETRY_SCHEMA,
    TELEMETRY_TIMEOUT,
    TELEMETRY_URL,
)

_LOGGER = logging.getLogger(__name__)

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


def collect_devices(entries: Iterable[Any]) -> list[dict]:
    """Devices of opted-in entries that are loaded and have polled.

    A device that hasn't answered a poll yet is left out rather than
    reported with flags that mean nothing.
    """
    devices = []
    for entry in entries:
        if not entry.options.get(CONF_SHARE_USAGE, False):
            continue
        runtime_data = getattr(entry, "runtime_data", None)
        if runtime_data is None:
            continue
        coordinator = runtime_data.coordinator
        if coordinator.last_successful_poll is None:
            continue
        devices.append(
            device_report(
                entry.data,
                coordinator.objects,
                cov=coordinator.cov_active,
                rpm=coordinator.client.rpm_supported(coordinator.device_address),
            )
        )
    return devices


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


class TelemetryReporter:
    """Sends the daily report while at least one entry has opted in."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._store: Store = Store(hass, 1, "bacnet.telemetry")
        self._install_id: str | None = None
        self._unsubs: list = []

    async def async_install_id(self) -> str:
        """The random installation ID, created and saved on first use."""
        if self._install_id is None:
            try:
                data = await self._store.async_load()
                self._install_id = str(uuid.UUID(data["install_id"]))
            except Exception:  # noqa: BLE001 — missing or corrupt: start over
                self._install_id = str(uuid.uuid4())
                await self._store.async_save({"install_id": self._install_id})
        return self._install_id

    async def async_send(self, _now: Any = None) -> None:
        """Send one report; do nothing when no entry has opted in."""
        entries = self._hass.config_entries.async_entries(DOMAIN)
        if not any(e.options.get(CONF_SHARE_USAGE, False) for e in entries):
            return
        try:
            integration = await async_get_integration(self._hass, DOMAIN)
            report = build_report(
                await self.async_install_id(),
                str(integration.version),
                HA_VERSION,
                collect_devices(entries),
            )
            await async_get_clientsession(self._hass).post(
                TELEMETRY_URL,
                json=report,
                timeout=aiohttp.ClientTimeout(total=TELEMETRY_TIMEOUT),
            )
        except Exception as err:  # noqa: BLE001 — never bother the user with it
            _LOGGER.debug("Usage report not sent: %r", err)

    def async_start(self) -> None:
        """Schedule the first and the daily report."""
        self._unsubs = [
            async_call_later(self._hass, TELEMETRY_FIRST_DELAY, self.async_send),
            async_track_time_interval(self._hass, self.async_send, TELEMETRY_INTERVAL),
        ]
        self._hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self.async_stop)

    def async_stop(self, _event: Any = None) -> None:
        """Cancel the scheduled reports."""
        while self._unsubs:
            self._unsubs.pop()()
