"""Entities for extra BACnet properties of an object (e.g. minimumOnTime).

The user picks them per object in the options flow; the coordinator polls
them with every poll into coordinator.data[obj_key][property]. Writable
known properties become number/switch entities, everything else a
read-only sensor (EXTRA_PROPERTY_SPECS).
"""

from __future__ import annotations

import json
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory

from .const import BACNET_UNITS, EXTRA_PROPERTY_SPECS
from .coordinator import BACnetCoordinator
from .entity import BACnetEntity, async_write_object_value
from .helpers import extra_property_platform, property_label


class BACnetPropertyEntity(BACnetEntity):
    """One extra property of a BACnet object."""

    def __init__(
        self,
        coordinator: BACnetCoordinator,
        entry: ConfigEntry,
        obj: dict[str, Any],
        prop: str,
    ) -> None:
        super().__init__(coordinator, entry, obj)
        self.property_name = prop
        self._attr_unique_id = f"{self._attr_unique_id}_{prop}"
        self._attr_name = f"{coordinator.get_entity_name(obj)} {property_label(prop)}"
        unit = (EXTRA_PROPERTY_SPECS.get(prop) or (None, None, None))[2]
        if unit == "object":
            unit = BACNET_UNITS.get(obj.get("units") or "", (None, None))[0]
        self._unit = unit

    def get_property_value(self) -> Any:
        return self.coordinator.get_object_value(self._obj_key, self.property_name)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            **super().extra_state_attributes,
            "bacnet_property": self.property_name,
        }

    async def _async_write(self, value: Any) -> None:
        await async_write_object_value(
            self.coordinator,
            self._obj,
            value,
            label=self._attr_name,
            property_name=self.property_name,
        )


class BACnetPropertySensor(BACnetPropertyEntity, SensorEntity):
    """Read-only extra property."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._attr_native_unit_of_measurement = self._unit

    @property
    def native_value(self) -> Any:
        value = self.get_property_value()
        if isinstance(value, list | dict):
            # A state is at most 255 characters; the full value stays in
            # bacnet.read_property.
            return json.dumps(value)[:255]
        return value


class BACnetPropertyNumber(BACnetPropertyEntity, NumberEntity):
    """Writable numeric extra property (limits, times, increments)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = -1_000_000
    _attr_native_max_value = 1_000_000

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._attr_native_unit_of_measurement = self._unit
        # Times are whole seconds (Unsigned); limits are REALs.
        self._attr_native_step = 1 if self._unit == "s" else 0.01
        if self._unit == "s":
            self._attr_native_min_value = 0

    @property
    def native_value(self) -> float | None:
        value = self.get_property_value()
        return value if isinstance(value, int | float) else None

    async def async_set_native_value(self, value: float) -> None:
        await self._async_write(int(value) if self._unit == "s" else value)


class BACnetPropertySwitch(BACnetPropertyEntity, SwitchEntity):
    """Boolean extra property (outOfService)."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool | None:
        value = self.get_property_value()
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write(False)


_CLASSES = {
    "sensor": BACnetPropertySensor,
    "number": BACnetPropertyNumber,
    "switch": BACnetPropertySwitch,
}


def property_entities(
    coordinator: BACnetCoordinator, entry: ConfigEntry, platform: str
) -> list[BACnetPropertyEntity]:
    """The extra-property entities of *platform* for this device.

    Objects no longer selected get none; their setting stays in the options
    and comes back with the object (issue #30).
    """
    entities: list[BACnetPropertyEntity] = []
    for obj in coordinator.objects:
        key = f"{obj['object_type']}:{obj['instance']}"
        for prop in coordinator.extra_properties.get(key, []):
            if extra_property_platform(prop) == platform:
                entities.append(_CLASSES[platform](coordinator, entry, obj, prop))
    return entities
