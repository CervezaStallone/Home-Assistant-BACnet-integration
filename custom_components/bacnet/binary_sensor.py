"""
Binary sensor platform for BACnet IP integration.

Creates HA binary_sensor entities for BACnet objects mapped to "binary_sensor".
Typically: Binary Input.

Binary sensors are read-only and reflect the presentValue as on/off.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BACnetCoordinator
from .entity import BACnetEntity, bacnet_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BACnet binary sensor entities from a config entry."""
    coordinator: BACnetCoordinator = entry.runtime_data.coordinator
    objects: list[dict[str, Any]] = coordinator.objects

    entities: list[Any] = [BACnetDeviceAlarmSensor(coordinator, entry)]
    for obj in objects:
        domain = coordinator.get_domain_for_object(obj)
        if domain == "binary_sensor":
            entities.append(BACnetBinarySensor(coordinator, entry, obj))

    async_add_entities(entities)
    _LOGGER.debug("Added %d BACnet binary sensor entities", len(entities))


class BACnetDeviceAlarmSensor(CoordinatorEntity[BACnetCoordinator], BinarySensorEntity):
    """On while the device reports any alarm.

    Either an object's statusFlags IN_ALARM bit or an active alarm from
    GetEventInformation (which also covers objects that weren't imported).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "alarm"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: BACnetCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{DOMAIN}_{entry.data.get('device_id', 'unknown')}_alarm"
        )
        self._attr_device_info = bacnet_device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Also follow COV pushes: they only notify the object's own listeners."""
        await super().async_added_to_hass()
        for obj in self.coordinator.objects:
            self.async_on_remove(
                self.coordinator.async_add_object_listener(
                    f"{obj['object_type']}:{obj['instance']}",
                    self.async_write_ha_state,
                )
            )

    @property
    def is_on(self) -> bool:
        if any(
            a["event_state"] != "normal" for a in self.coordinator.active_alarms or []
        ):
            return True
        return any(
            bool(flags and flags[0])
            for flags in (
                values.get("statusFlags")
                for values in (self.coordinator.data or {}).values()
            )
        )


class BACnetBinarySensor(BACnetEntity, BinarySensorEntity):
    """Representation of a BACnet binary input as a HA binary sensor.

    BACnet binary presentValue semantics:
      0 / "inactive" → OFF
      1 / "active"   → ON
    Polarity inversion is handled by the BACnet device itself.
    """

    @property
    def is_on(self) -> bool | None:
        """Return True if the binary object is active (presentValue = 1/active)."""
        value = self.get_present_value()
        if value is None:
            return None
        # BACnet binary PV: 0 = inactive, 1 = active
        # Also handle string representations from some devices
        if isinstance(value, str):
            return value.lower() in ("active", "1", "true", "on")
        return bool(int(value))
