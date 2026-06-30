"""Select platform for BACnet IP integration — write priority selector.

One entity per BACnet device. Disabled by default (entity_registry_enabled_default=False).
When enabled, the user can change the BACnet write priority used by all writable entities
(switch, number, climate) on this device.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DATA_COORDINATOR,
    DEFAULT_WRITE_PRIORITY,
    DOMAIN,
    WRITE_PRIORITY_OPTIONS,
)
from .coordinator import BACnetCoordinator

_LOGGER = logging.getLogger(__name__)

_PRIORITY_OPTIONS: list[str] = [str(p) for p in WRITE_PRIORITY_OPTIONS]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the write priority select entity for a BACnet device."""
    coordinator: BACnetCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities([BACnetWritePrioritySelect(coordinator, entry)])


class BACnetWritePrioritySelect(
    CoordinatorEntity[BACnetCoordinator], SelectEntity, RestoreEntity
):
    """Select entity to control the BACnet write priority for this device.

    Disabled by default — the user must enable it manually in the HA entity registry.
    Changing the selection updates coordinator.write_priority which is then used by all
    writable entities (switch, number, climate) on this device.
    """

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:priority-high"
    _attr_options = _PRIORITY_OPTIONS

    def __init__(
        self,
        coordinator: BACnetCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry

        device_id = entry.data.get("device_id", "unknown")
        device_name = entry.data.get("device_name", "BACnet Device")
        vendor_name = entry.data.get("vendor_name", "BACnet")
        model_name = entry.data.get("model_name", "")
        fw_version = entry.data.get("firmware_version", "")
        sw_version = entry.data.get("software_version", "")

        self._attr_unique_id = f"{DOMAIN}_{device_id}_write_priority"
        self._attr_name = "Write Priority"
        self._attr_current_option = str(DEFAULT_WRITE_PRIORITY)

        device_info = DeviceInfo(
            identifiers={(DOMAIN, str(device_id))},
            name=device_name,
            manufacturer=vendor_name,
        )
        device_info["model"] = model_name if model_name else f"BACnet Device {device_id}"
        if fw_version and sw_version:
            device_info["sw_version"] = f"{fw_version} / {sw_version}"
        elif fw_version:
            device_info["sw_version"] = fw_version
        elif sw_version:
            device_info["sw_version"] = sw_version
        self._attr_device_info = device_info

    async def async_added_to_hass(self) -> None:
        """Restore last priority on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state in _PRIORITY_OPTIONS:
            self._attr_current_option = last_state.state
            self.coordinator.write_priority = int(last_state.state)
            _LOGGER.debug(
                "Restored write priority %s for device %s",
                last_state.state,
                self._entry.data.get("device_name", "unknown"),
            )

    @property
    def current_option(self) -> str:
        """Return the currently selected priority level."""
        return self._attr_current_option

    async def async_select_option(self, option: str) -> None:
        """Update write priority when the user changes the dropdown."""
        self._attr_current_option = option
        self.coordinator.write_priority = int(option)
        self.async_write_ha_state()
        _LOGGER.debug(
            "Write priority changed to %s for device %s",
            option,
            self._entry.data.get("device_name", "unknown"),
        )
