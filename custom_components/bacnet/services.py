"""Entity services for the BACnet IP integration.

bacnet.relinquish   — release HA's command (write Null) at a priority level,
                      so lower priorities or the Relinquish Default take over.
bacnet.write_value  — write a presentValue once at a chosen priority,
                      without changing the device's Write Priority setting,
                      or any other property (property field).

bacnet.read_property — return any property of an object (by name or number).
                       Targets object entities, or a device plus
                       object_type/instance for objects not imported.

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
from homeassistant.helpers import device_registry as dr
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


from .bacnet_client import BACnetClient, BACnetPropertyError, ScheduleError
from .const import DOMAIN, OBJECT_TYPE_SCHEDULE
from .entity import async_write_object_value
from .helpers import object_key

SERVICE_RELINQUISH = "relinquish"
SERVICE_WRITE_VALUE = "write_value"
SERVICE_READ_PROPERTY = "read_property"
SERVICE_READ_SCHEDULE = "read_schedule"
SERVICE_WRITE_SCHEDULE = "write_schedule"
ATTR_PRIORITY = "priority"
ATTR_VALUE = "value"
ATTR_PROPERTY = "property"
ATTR_ARRAY_INDEX = "array_index"
ATTR_OBJECT_TYPE = "object_type"
ATTR_INSTANCE = "instance"
PRESENT_VALUE = "presentValue"

_PRIORITY = vol.All(vol.Coerce(int), vol.Range(min=1, max=16))
# Only needed when targeting a device: the object to use on that device.
_OBJECT_FIELDS = {
    vol.Optional(ATTR_OBJECT_TYPE): vol.Any(vol.Coerce(int), cv.string),
    vol.Optional(ATTR_INSTANCE): vol.All(vol.Coerce(int), vol.Range(min=0)),
}

RELINQUISH_SCHEMA = cv.make_entity_service_schema(
    {vol.Optional(ATTR_PRIORITY): _PRIORITY}
)
WRITE_VALUE_SCHEMA = cv.make_entity_service_schema(
    {
        # bool first: vol.Coerce(float) would turn True into 1.0.
        vol.Required(ATTR_VALUE): vol.Any(bool, vol.Coerce(float), cv.string),
        vol.Optional(ATTR_PRIORITY): _PRIORITY,
        vol.Optional(ATTR_PROPERTY): vol.Any(vol.Coerce(int), cv.string),
        **_OBJECT_FIELDS,
    }
)
READ_PROPERTY_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required(ATTR_PROPERTY): vol.Any(vol.Coerce(int), cv.string),
        vol.Optional(ATTR_ARRAY_INDEX): vol.All(vol.Coerce(int), vol.Range(min=0)),
        **_OBJECT_FIELDS,
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
        vol.Optional("allow_removal", default=False): cv.boolean,
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


def _device_targets(
    hass: HomeAssistant, call: ServiceCall
) -> list[tuple[Any, dict, str]]:
    """Resolve a device target plus object_type/instance to (coordinator, object, device_id).

    Lets a user reach objects that were never imported as entities.
    """
    type_value = call.data[ATTR_OBJECT_TYPE]
    object_type = BACnetClient._object_type_str_to_int(
        int(type_value) if str(type_value).isdigit() else type_value
    )
    if object_type is None or ATTR_INSTANCE not in call.data:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="invalid_object"
        )
    obj = {"object_type": object_type, "instance": call.data[ATTR_INSTANCE]}
    registry = dr.async_get(hass)
    targets = []
    for device_id in sorted(_referenced(hass, call).referenced_devices):
        device = registry.async_get(device_id)
        if device is None or not any(i[0] == DOMAIN for i in device.identifiers):
            continue
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            coordinator = getattr(
                getattr(entry, "runtime_data", None), "coordinator", None
            )
            if coordinator is not None:
                known = next(
                    (
                        o
                        for o in coordinator.objects
                        if object_key(o) == object_key(obj)
                    ),
                    obj,
                )
                targets.append((coordinator, known, device_id))
    if not targets:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="no_bacnet_device"
        )
    return targets


async def _check_permissions(
    hass: HomeAssistant,
    call: ServiceCall,
    policies: tuple[str, ...],
    *,
    admin_only: bool = False,
) -> None:
    """Check the calling user may use every targeted entity.

    A handler registered with async_register gets no permission check from
    Home Assistant, unlike a platform entity service. Checked before the
    targets are resolved, so the errors there reveal nothing about an
    entity the user may not access. Device targets reach any object on
    the device, so they are for administrators only, as is *admin_only*
    (writing an object's configuration rather than its value).
    """
    if not call.context.user_id:
        return
    user = await hass.auth.async_get_user(call.context.user_id)
    if user is None:
        raise UnknownUser(context=call.context)
    if admin_only or ATTR_OBJECT_TYPE in call.data:
        if not user.is_admin:
            raise Unauthorized(context=call.context)
        return
    selected = _referenced(hass, call)
    for entity_id in sorted(selected.referenced | selected.indirectly_referenced):
        for policy in policies:
            if not user.permissions.check_entity(entity_id, policy):
                raise Unauthorized(
                    context=call.context, entity_id=entity_id, permission=policy
                )


def _resolve(hass: HomeAssistant, call: ServiceCall) -> list[tuple[Any, dict, str]]:
    """Device + object fields when given, otherwise the targeted entities."""
    if ATTR_OBJECT_TYPE in call.data:
        return _device_targets(hass, call)
    return _targets(hass, call)


def _property_name(call: ServiceCall) -> str:
    try:
        return BACnetClient.normalize_property(
            call.data.get(ATTR_PROPERTY, PRESENT_VALUE)
        )
    except ValueError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="unknown_property",
            translation_placeholders={"property": str(call.data[ATTR_PROPERTY])},
        ) from err


async def async_handle_read_property(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, Any]:
    """Handle bacnet.read_property: {target: {object, property, value}}."""
    await _check_permissions(hass, call, (POLICY_READ,))
    property_name = _property_name(call)
    response: dict[str, Any] = {}
    for coordinator, obj, target in _resolve(hass, call):
        try:
            value = await coordinator.client.read_any_property(
                coordinator.device_address,
                obj["object_type"],
                obj["instance"],
                property_name,
                call.data.get(ATTR_ARRAY_INDEX),
            )
        except BACnetPropertyError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="property_read_failed",
                translation_placeholders={
                    "property": property_name,
                    "object": object_key(obj),
                    "error": str(err),
                },
            ) from err
        response[target] = {
            "object": object_key(obj),
            "property": property_name,
            "value": value,
        }
    return response


async def async_handle_relinquish(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle bacnet.relinquish."""
    await _check_permissions(hass, call, (POLICY_CONTROL,))
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
    property_name = _property_name(call)
    await _check_permissions(
        hass, call, (POLICY_CONTROL,), admin_only=property_name != PRESENT_VALUE
    )
    value = call.data[ATTR_VALUE]
    if property_name == PRESENT_VALUE and not isinstance(value, bool):
        value = float(value)
    for coordinator, obj, target in _resolve(hass, call):
        await async_write_object_value(
            coordinator,
            obj,
            value,
            priority=call.data.get(ATTR_PRIORITY),
            label=target,
            property_name=property_name,
        )


async def async_handle_schedule(
    hass: HomeAssistant, call: ServiceCall, *, write: bool
) -> dict[str, Any]:
    """Handle bacnet.read_schedule / bacnet.write_schedule.

    Returns {entity_id: schedule}; after a write that is the read-back.
    """
    # A write returns the schedule as well, so it needs read permission too.
    await _check_permissions(
        hass, call, (POLICY_READ, POLICY_CONTROL) if write else (POLICY_READ,)
    )

    targets = _targets(hass, call)
    for _, obj, entity_id in targets:
        if obj["object_type"] != OBJECT_TYPE_SCHEDULE:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_a_schedule",
                translation_placeholders={"entity_id": entity_id},
            )
    changes = {key: call.data[key] for key in _SCHEDULE_FIELDS if key in call.data}

    response: dict[str, Any] = {}
    for coordinator, obj, entity_id in targets:
        client, address = coordinator.client, coordinator.device_address
        try:
            if write:
                response[entity_id] = await client.write_schedule(
                    address,
                    obj["instance"],
                    changes,
                    allow_removal=call.data.get("allow_removal", False),
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

    async def _read_property(call: ServiceCall) -> dict[str, Any]:
        return await async_handle_read_property(hass, call)

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
        DOMAIN,
        SERVICE_READ_PROPERTY,
        _read_property,
        schema=READ_PROPERTY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RELINQUISH, _relinquish, schema=RELINQUISH_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_WRITE_VALUE, _write_value, schema=WRITE_VALUE_SCHEMA
    )
