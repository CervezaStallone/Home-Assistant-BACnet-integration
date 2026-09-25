"""Diagnostics download for the BACnet IP integration.

Settings → Devices & services → BACnet IP → ⋮ → Download diagnostics. Network
addresses are redacted; object lists, options and health figures are not
sensitive and are what a bug report needs.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BBMD_ADDRESS,
    CONF_DEVICE_ADDRESS,
    CONF_LOCAL_IP,
    CONF_TARGET_ADDRESS,
)

TO_REDACT = {CONF_LOCAL_IP, CONF_BBMD_ADDRESS, CONF_DEVICE_ADDRESS, CONF_TARGET_ADDRESS}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data.coordinator
    client = coordinator.client
    address = entry.data.get(CONF_DEVICE_ADDRESS, "")
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            **coordinator.diagnostics_summary(),
            "rpm_supported": client._rpm_supported.get(address, True),
            "rpm_chunk_size": client._rpm_chunk_size.get(address),
            "cov_subscribed_objects": sorted(coordinator._cov_subscriptions),
        },
        "data": coordinator.data,
    }
