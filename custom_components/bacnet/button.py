"""Button platform for BACnet IP integration — manual metadata refresh.

One entity per BACnet device. Pressing it forces an immediate re-read of
objectName/description/units/commandable for every selected object, instead
of waiting for the next periodic refresh (issue #26).
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import BACnetCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the metadata refresh button for a BACnet device."""
    coordinator: BACnetCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities([BACnetRefreshMetadataButton(coordinator, entry)])


class BACnetRefreshMetadataButton(CoordinatorEntity[BACnetCoordinator], ButtonEntity):
    """Button that forces an immediate object-metadata refresh from the device."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: BACnetCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry

        device_id = entry.data.get("device_id", "unknown")
        device_name = entry.data.get("device_name", "BACnet Device")
        vendor_name = entry.data.get("vendor_name", "BACnet")
        model_name = entry.data.get("model_name", "")
        fw_version = entry.data.get("firmware_version", "")
        sw_version = entry.data.get("software_version", "")

        self._attr_unique_id = f"{DOMAIN}_{device_id}_refresh_metadata"
        self._attr_name = "Refresh Object Metadata"

        device_info = DeviceInfo(
            identifiers={(DOMAIN, str(device_id))},
            name=device_name,
            manufacturer=vendor_name,
        )
        device_info["model"] = (
            model_name if model_name else f"BACnet Device {device_id}"
        )
        if fw_version and sw_version:
            device_info["sw_version"] = f"{fw_version} / {sw_version}"
        elif fw_version:
            device_info["sw_version"] = fw_version
        elif sw_version:
            device_info["sw_version"] = sw_version
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        """Force an immediate metadata refresh, bypassing the interval timer."""
        _LOGGER.info(
            "Manual metadata refresh triggered for device %s",
            self._entry.data.get("device_name", "unknown"),
        )
        await self.coordinator.async_refresh_metadata()
