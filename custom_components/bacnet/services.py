"""Entity services for the BACnet IP integration.

bacnet.relinquish   — release HA's command (write Null) at a priority level,
                      so lower priorities or the Relinquish Default take over.
bacnet.write_value  — write a presentValue once at a chosen priority,
                      without changing the device's Write Priority setting.

Registered once in async_setup and resolved through the entity registry,
so one service covers the switch, number and climate platforms alike.
"""

from __future__ import annotations

from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_extract_referenced_entity_ids

from .const import DOMAIN
from .entity import async_write_object_value
from .helpers import object_key

SERVICE_RELINQUISH = "relinquish"
SERVICE_WRITE_VALUE = "write_value"
ATTR_PRIORITY = "priority"
ATTR_VALUE = "value"

_PRIORITY = vol.All(vol.Coerce(int), vol.Range(min=1, max=16))

RELINQUISH_SCHEMA = cv.make_entity_service_schema(
    {vol.Optional(ATTR_PRIORITY): _PRIORITY}
)
WRITE_VALUE_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required(ATTR_VALUE): vol.Coerce(float),
        vol.Optional(ATTR_PRIORITY): _PRIORITY,
    }
)


def _targets(hass: HomeAssistant, call: ServiceCall) -> list[tuple[Any, dict, str]]:
    """Resolve the call's entities to (coordinator, object, entity_id)."""
    selected = async_extract_referenced_entity_ids(hass, call)
    registry = er.async_get(hass)
    targets = []
    for entity_id in sorted(selected.referenced | selected.indirectly_referenced):
        reg_entry = registry.async_get(entity_id)
        # Object entities use f"{DOMAIN}_{device_id}_{type}_{instance}".
        parts = reg_entry.unique_id.rsplit("_", 2) if reg_entry else []
        config_entry = (
            hass.config_entries.async_get_entry(reg_entry.config_entry_id)
            if reg_entry and reg_entry.platform == DOMAIN
            else None
        )
        coordinator = getattr(
            getattr(config_entry, "runtime_data", None), "coordinator", None
        )
        obj = None
        if (
            coordinator is not None
            and len(parts) == 3
            and all(p.isdigit() for p in parts[1:])
        ):
            key = f"{parts[1]}:{parts[2]}"
            obj = next((o for o in coordinator.objects if object_key(o) == key), None)
        if obj is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_a_bacnet_object",
                translation_placeholders={"entity_id": entity_id},
            )
        targets.append((coordinator, obj, entity_id))
    return targets


async def async_handle_relinquish(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle bacnet.relinquish."""
    for coordinator, obj, entity_id in _targets(hass, call):
        await async_write_object_value(
            coordinator,
            obj,
            None,
            priority=call.data.get(ATTR_PRIORITY),
            label=entity_id,
        )


async def async_handle_write_value(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle bacnet.write_value."""
    for coordinator, obj, entity_id in _targets(hass, call):
        await async_write_object_value(
            coordinator,
            obj,
            call.data[ATTR_VALUE],
            priority=call.data.get(ATTR_PRIORITY),
            label=entity_id,
        )


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's services (once, from async_setup)."""

    async def _relinquish(call: ServiceCall) -> None:
        await async_handle_relinquish(hass, call)

    async def _write_value(call: ServiceCall) -> None:
        await async_handle_write_value(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_RELINQUISH, _relinquish, schema=RELINQUISH_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_WRITE_VALUE, _write_value, schema=WRITE_VALUE_SCHEMA
    )
