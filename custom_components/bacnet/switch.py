"""
Switch platform for BACnet IP integration.

Creates HA switch entities for BACnet objects mapped to "switch".
Typically: Binary Output, Binary Value (when commandable).

Switches support on/off with proper Priority Array handling:
  - Turn ON  → write active (1) at the configured priority
  - Turn OFF → write Null (relinquish) at the same priority to release the override
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import BACnetCoordinator
from .entity import BACnetEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BACnet switch entities from a config entry."""
    coordinator: BACnetCoordinator = entry.runtime_data.coordinator
    objects: list[dict[str, Any]] = coordinator.objects

    entities: list[BACnetSwitch] = []
    for obj in objects:
        domain = coordinator.get_domain_for_object(obj)
        if domain == "switch":
            entities.append(BACnetSwitch(coordinator, entry, obj))

    if entities:
        async_add_entities(entities)
        _LOGGER.debug("Added %d BACnet switch entities", len(entities))


class BACnetSwitch(BACnetEntity, SwitchEntity):
    """Representation of a commandable BACnet binary object as a HA switch.

    Write strategy (BACnet standard compliant):
      - turn_on:  Write presentValue = 1 (active) at priority level
      - turn_off: Write presentValue = 0 (inactive) at same priority level
                  This explicitly commands the output off.  To fully release
                  HA's override and let lower-priority sources or the
                  Relinquish Default take effect, use client.relinquish().
    """

    def __init__(
        self,
        coordinator: BACnetCoordinator,
        entry: ConfigEntry,
        obj: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, entry, obj)

    @property
    def is_on(self) -> bool | None:
        """Return True if the switch is on (presentValue = active/1)."""
        value = self.get_present_value()
        if value is None:
            return None
        if isinstance(value, str):
            return value.lower() in ("active", "1", "true", "on")
        return bool(int(value))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on by writing active (1) at the configured priority."""
        await self.async_write_present_value(1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off by writing inactive (0) at the configured priority.

        This commands the output off. To release HA's override instead, use
        the bacnet.relinquish service.
        """
        await self.async_write_present_value(0)
