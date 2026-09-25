"""Select platform for BACnet IP integration — write priority selector.

One entity per BACnet device. Disabled by default (entity_registry_enabled_default=False).
When enabled, the user can change the BACnet write priority used by all writable entities
(switch, number, climate) on this device.
"""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .bacnet_client import MULTI_STATE_TYPES
from .const import (
    DEFAULT_WRITE_PRIORITY,
    DOMAIN,
    WRITE_PRIORITY_OPTIONS,
)
from .coordinator import BACnetCoordinator
from .entity import BACnetEntity, bacnet_device_info

_LOGGER = logging.getLogger(__name__)

_PRIORITY_OPTIONS: list[str] = [str(p) for p in WRITE_PRIORITY_OPTIONS]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the write priority select entity for a BACnet device."""
    coordinator: BACnetCoordinator = entry.runtime_data.coordinator
    entities: list[SelectEntity] = [BACnetWritePrioritySelect(coordinator, entry)]
    for obj in coordinator.objects:
        if coordinator.get_domain_for_object(obj) != "select":
            continue
        if obj["object_type"] not in MULTI_STATE_TYPES:
            _LOGGER.warning(
                "Object %s:%s is mapped to 'select' but is not a multi-state "
                "object — skipped",
                obj["object_type"],
                obj["instance"],
            )
            continue
        entities.append(BACnetMultiStateSelect(coordinator, entry, obj))
    async_add_entities(entities)


class BACnetWritePrioritySelect(
    CoordinatorEntity[BACnetCoordinator], SelectEntity, RestoreEntity
):
    """Select entity to control the BACnet write priority for this device.

    Disabled by default — the user must enable it manually in the HA entity registry.
    Changing the selection updates coordinator.write_priority which is then used by all
    writable entities (switch, number, climate) on this device.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "write_priority"
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
        self._attr_unique_id = f"{DOMAIN}_{device_id}_write_priority"
        self._attr_current_option = str(DEFAULT_WRITE_PRIORITY)

        self._attr_device_info = bacnet_device_info(entry)

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


class BACnetMultiStateSelect(BACnetEntity, SelectEntity):
    """A multi-state object as a select: one option per state.

    Options are the object's stateText labels, or "1".."numberOfStates"
    when the device has no stateText. BACnet states are 1-based.
    """

    @property
    def options(self) -> list[str]:
        texts = self._obj.get("state_text")
        if texts:
            return list(texts)
        return [str(i) for i in range(1, (self._obj.get("number_of_states") or 0) + 1)]

    @property
    def current_option(self) -> str | None:
        value = self.get_present_value()
        try:
            index = int(value) - 1
        except (TypeError, ValueError):
            return None
        options = self.options
        return options[index] if 0 <= index < len(options) else None

    async def async_select_option(self, option: str) -> None:
        await self.async_write_present_value(self.options.index(option) + 1)
