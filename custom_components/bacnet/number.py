"""
Number platform for BACnet IP integration.

Creates HA number entities for BACnet objects mapped to "number".
Typically: Analog Output, Analog Value (when writable), Multi-State Output,
Multi-State Value (when writable).

Number entities allow the user to read and set numeric values (setpoints,
override values, etc.) with proper Priority Array handling.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    BACNET_UNITS,
    OBJECT_TYPE_MULTI_STATE_INPUT,
    OBJECT_TYPE_MULTI_STATE_OUTPUT,
    OBJECT_TYPE_MULTI_STATE_VALUE,
)
from .coordinator import BACnetCoordinator
from .entity import BACnetEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BACnet number entities from a config entry."""
    coordinator: BACnetCoordinator = entry.runtime_data.coordinator
    objects: list[dict[str, Any]] = coordinator.objects

    entities: list[BACnetNumber] = []
    for obj in objects:
        domain = coordinator.get_domain_for_object(obj)
        if domain == "number":
            entities.append(BACnetNumber(coordinator, entry, obj))

    if entities:
        async_add_entities(entities)
        _LOGGER.debug("Added %d BACnet number entities", len(entities))


class BACnetNumber(BACnetEntity, NumberEntity):
    """Representation of a writable BACnet analog/multi-state object as HA number.

    Write strategy:
      - set_native_value: Write the value to presentValue at the configured
        priority level. For commandable objects this uses the Priority Array.
      - Relinquish is possible via a service call or automation writing None.
    """

    _attr_mode = NumberMode.BOX  # Allow free numeric input

    def __init__(
        self,
        coordinator: BACnetCoordinator,
        entry: ConfigEntry,
        obj: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, entry, obj)

        # Set native unit from BACnet units
        self._attr_native_unit_of_measurement = BACNET_UNITS.get(
            obj.get("units") or "", (None, None)
        )[0]

        # Limits: the device's own minPresValue/maxPresValue/resolution or
        # numberOfStates when known, otherwise wide type-based defaults.
        if obj["object_type"] in {
            OBJECT_TYPE_MULTI_STATE_INPUT,
            OBJECT_TYPE_MULTI_STATE_OUTPUT,
            OBJECT_TYPE_MULTI_STATE_VALUE,
        }:
            # Multi-state values are 1-based unsigned integers
            self._attr_native_min_value = 1
            self._attr_native_max_value = obj.get("number_of_states") or 255
            self._attr_native_step = 1.0
        else:
            # Analog values — BACnet uses IEEE 754 floats
            min_value = obj.get("min_value")
            max_value = obj.get("max_value")
            self._attr_native_min_value = (
                min_value if min_value is not None else -1_000_000
            )
            self._attr_native_max_value = (
                max_value if max_value is not None else 1_000_000
            )
            self._attr_native_step = obj.get("resolution") or 0.1

    @property
    def native_value(self) -> float | None:
        """Return the current value from the coordinator."""
        value = self.get_present_value()
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Write a new value to the BACnet object's presentValue.

        For commandable objects (outputs), this writes at the configured
        priority in the Priority Array. For non-commandable writable objects,
        priority is not sent.
        """
        await self.async_write_present_value(value)
