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
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_COORDINATOR, DATA_OBJECTS, DOMAIN
from .coordinator import BACnetCoordinator
from .entity import BACnetEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BACnet binary sensor entities from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: BACnetCoordinator = data[DATA_COORDINATOR]
    objects: list[dict[str, Any]] = data[DATA_OBJECTS]

    entities: list[BACnetBinarySensor] = []
    for obj in objects:
        domain = coordinator.get_domain_for_object(obj)
        if domain == "binary_sensor":
            entities.append(BACnetBinarySensor(coordinator, entry, obj))

    if entities:
        async_add_entities(entities)
        _LOGGER.debug("Added %d BACnet binary sensor entities", len(entities))


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
