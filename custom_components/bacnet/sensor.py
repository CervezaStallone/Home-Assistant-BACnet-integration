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
    DATA_COORDINATOR,
    DATA_OBJECTS,
    DOMAIN,
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
)
from .coordinator import BACnetCoordinator
from .entity import BACnetEntity

_LOGGER = logging.getLogger(__name__)

# BACnet engineering units → HA sensor device class mapping (subset)
_UNIT_DEVICE_CLASS: dict[str, SensorDeviceClass] = {
    "degreesCelsius": SensorDeviceClass.TEMPERATURE,
    "degreesFahrenheit": SensorDeviceClass.TEMPERATURE,
    "percent": SensorDeviceClass.HUMIDITY,
    "percentRelativeHumidity": SensorDeviceClass.HUMIDITY,
    "pascals": SensorDeviceClass.PRESSURE,
    "hectopascals": SensorDeviceClass.PRESSURE,
    "kiloPascals": SensorDeviceClass.PRESSURE,
    "watts": SensorDeviceClass.POWER,
    "kilowatts": SensorDeviceClass.POWER,
    "kilowattHours": SensorDeviceClass.ENERGY,
    "amperes": SensorDeviceClass.CURRENT,
    "volts": SensorDeviceClass.VOLTAGE,
    "hertz": SensorDeviceClass.FREQUENCY,
    "litersPerSecond": SensorDeviceClass.VOLUME_FLOW_RATE,
}

# BACnet units → HA native unit string
_UNIT_NATIVE: dict[str, str] = {
    "degreesCelsius": "°C",
    "degreesFahrenheit": "°F",
    "percent": "%",
    "percentRelativeHumidity": "%",
    "pascals": "Pa",
    "hectopascals": "hPa",
    "kiloPascals": "kPa",
    "watts": "W",
    "kilowatts": "kW",
    "kilowattHours": "kWh",
    "amperes": "A",
    "volts": "V",
    "hertz": "Hz",
    "litersPerSecond": "L/s",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BACnet sensor entities from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: BACnetCoordinator = data[DATA_COORDINATOR]
    objects: list[dict[str, Any]] = data[DATA_OBJECTS]

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
        units = obj.get("units")
        if units:
            self._attr_device_class = _UNIT_DEVICE_CLASS.get(units)
            self._attr_native_unit_of_measurement = _UNIT_NATIVE.get(units)

        # Analog types get measurement state class for statistics support
        if obj["object_type"] in {
            OBJECT_TYPE_ANALOG_INPUT,
            OBJECT_TYPE_ANALOG_OUTPUT,
            OBJECT_TYPE_ANALOG_VALUE,
        }:
            self._attr_state_class = SensorStateClass.MEASUREMENT

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
                return round(float(value), 2)
            except (ValueError, TypeError):
                return None
        # Multi-state values are integers
        try:
            return int(value)
        except (ValueError, TypeError):
            return str(value)
