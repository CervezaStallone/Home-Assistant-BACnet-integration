"""Entity services for the BACnet IP integration.

bacnet.relinquish   — release HA's command (write Null) at a priority level,
                      so lower priorities or the Relinquish Default take over.
bacnet.write_value  — write a presentValue once at a chosen priority,
                      without changing the device's Write Priority setting.

bacnet.read_schedule  — return a Schedule object (weekly schedule, exceptions,
                        default, controlled objects) as response data.
bacnet.write_schedule — change parts of a Schedule object; the device is read
                        back afterwards to verify it accepted the change.

Registered once in async_setup and resolved through the entity registry,
so one service covers the switch, number and climate platforms alike.
"""

from __future__ import annotations

from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, Unauthorized, UnknownUser
from homeassistant.helpers import entity_registry as er

try:  # Newer HA; the helpers.service variant is removed in HA 2026.8
    from homeassistant.helpers.target import (
        TargetSelection,
        async_extract_referenced_entity_ids,
    )

    def _referenced(hass: HomeAssistant, call: ServiceCall):
        return async_extract_referenced_entity_ids(hass, TargetSelection(call.data))

except ImportError:  # older Home Assistant (minimum supported: 2024.4)
    from homeassistant.helpers.service import async_extract_referenced_entity_ids

    def _referenced(hass: HomeAssistant, call: ServiceCall):
        return async_extract_referenced_entity_ids(hass, call)


from .bacnet_client import ScheduleError
from .const import DOMAIN, OBJECT_TYPE_SCHEDULE
from .entity import async_write_object_value
from .helpers import object_key

SERVICE_RELINQUISH = "relinquish"
SERVICE_WRITE_VALUE = "write_value"
SERVICE_READ_SCHEDULE = "read_schedule"
SERVICE_WRITE_SCHEDULE = "write_schedule"
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
READ_SCHEDULE_SCHEMA = cv.make_entity_service_schema({})
# Only the outer shape is checked here; the client validates the contents
# while converting them to BACnet types.
WRITE_SCHEDULE_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Optional("weekly_schedule"): dict,
        vol.Optional("exception_schedule"): list,
        vol.Optional("schedule_default"): vol.Any(None, bool, int, float, str),
        vol.Optional("effective_period"): dict,
    }
)
_SCHEDULE_FIELDS = (
    "weekly_schedule",
    "exception_schedule",
    "schedule_default",
    "effective_period",
)


def _targets(hass: HomeAssistant, call: ServiceCall) -> list[tuple[Any, dict, str]]:
    """Resolve the call's entities to (coordinator, object, entity_id)."""
    selected = _referenced(hass, call)
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


async def async_handle_schedule(
    hass: HomeAssistant, call: ServiceCall, *, write: bool
) -> dict[str, Any]:
    """Handle bacnet.read_schedule / bacnet.write_schedule.

    Returns {entity_id: schedule}; after a write that is the read-back.
    """
    targets = _targets(hass, call)
    for _, obj, entity_id in targets:
        if obj["object_type"] != OBJECT_TYPE_SCHEDULE:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_a_schedule",
                translation_placeholders={"entity_id": entity_id},
            )
    # A handler registered with async_register gets no permission check from
    # Home Assistant, unlike a platform entity service.
    if call.context.user_id:
        user = await hass.auth.async_get_user(call.context.user_id)
        if user is None:
            raise UnknownUser(context=call.context)
        policy = POLICY_CONTROL if write else POLICY_READ
        for _, _, entity_id in targets:
            if not user.permissions.check_entity(entity_id, policy):
                raise Unauthorized(
                    context=call.context, entity_id=entity_id, permission=policy
                )
    changes = {key: call.data[key] for key in _SCHEDULE_FIELDS if key in call.data}

    response: dict[str, Any] = {}
    for coordinator, obj, entity_id in targets:
        client, address = coordinator.client, coordinator.device_address
        try:
            if write:
                response[entity_id] = await client.write_schedule(
                    address, obj["instance"], changes
                )
                await coordinator.async_refresh_object(obj)
            else:
                response[entity_id] = await client.read_schedule(
                    address, obj["instance"]
                )
        except ScheduleError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="schedule_failed",
                translation_placeholders={"entity_id": entity_id, "error": str(err)},
            ) from err
    return response


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's services (once, from async_setup)."""

    async def _relinquish(call: ServiceCall) -> None:
        await async_handle_relinquish(hass, call)

    async def _write_value(call: ServiceCall) -> None:
        await async_handle_write_value(hass, call)

    async def _read_schedule(call: ServiceCall) -> dict[str, Any]:
        return await async_handle_schedule(hass, call, write=False)

    async def _write_schedule(call: ServiceCall) -> dict[str, Any]:
        return await async_handle_schedule(hass, call, write=True)

    hass.services.async_register(
        DOMAIN,
        SERVICE_READ_SCHEDULE,
        _read_schedule,
        schema=READ_SCHEDULE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_WRITE_SCHEDULE,
        _write_schedule,
        schema=WRITE_SCHEDULE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RELINQUISH, _relinquish, schema=RELINQUISH_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_WRITE_VALUE, _write_value, schema=WRITE_VALUE_SCHEMA
    )
