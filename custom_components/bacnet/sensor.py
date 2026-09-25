"""
Sensor platform for BACnet IP integration.

Creates HA sensor entities for BACnet objects mapped to the "sensor" domain.
Typically: Analog Input, Analog Value (read-only), Multi-State Input,
Multi-State Value (read-only).

Sensors are read-only and display the presentValue.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    BACNET_UNITS,
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
)
from .coordinator import BACnetCoordinator
from .entity import BACnetEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BACnet sensor entities from a config entry."""
    coordinator: BACnetCoordinator = entry.runtime_data.coordinator
    objects: list[dict[str, Any]] = coordinator.objects

    entities: list[BACnetSensor] = []
    for obj in objects:
        domain = coordinator.get_domain_for_object(obj)
        if domain == "sensor":
            entities.append(BACnetSensor(coordinator, entry, obj))

    if entities:
        async_add_entities(entities)
        _LOGGER.debug("Added %d BACnet sensor entities", len(entities))


class BACnetSensor(BACnetEntity, SensorEntity):
    """Representation of a read-only BACnet object as a HA sensor."""

    def __init__(
        self,
        coordinator: BACnetCoordinator,
        entry: ConfigEntry,
        obj: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, entry, obj)

        # Determine device class and native unit from BACnet units
        unit, device_class = BACNET_UNITS.get(obj.get("units") or "", (None, None))
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = (
            SensorDeviceClass(device_class) if device_class else None
        )

        # Analog types get measurement state class for statistics support
        if obj["object_type"] in {
            OBJECT_TYPE_ANALOG_INPUT,
            OBJECT_TYPE_ANALOG_OUTPUT,
            OBJECT_TYPE_ANALOG_VALUE,
        }:
            self._attr_state_class = SensorStateClass.MEASUREMENT
            # Display hint only — the state keeps full precision (user can
            # change it per entity in the UI).
            self._attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | int | str | None:
        """Return the sensor's current value from the coordinator."""
        value = self.get_present_value()
        if value is None:
            return None
        # Analog values are float, multi-state are int
        if self.object_type in {
            OBJECT_TYPE_ANALOG_INPUT,
            OBJECT_TYPE_ANALOG_OUTPUT,
            OBJECT_TYPE_ANALOG_VALUE,
        }:
            try:
                # BACnet REAL is float32: keep its ~7 significant digits and
                # drop the float64 conversion noise (23.456000328…).
                return float(f"{float(value):.7g}")
            except (ValueError, TypeError):
                return None
        # Multi-state values are integers
        try:
            return int(value)
        except (ValueError, TypeError):
            return str(value)
