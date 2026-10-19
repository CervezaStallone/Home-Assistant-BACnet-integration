"""Tests for the bacnet.relinquish / bacnet.write_value services."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

_OBJ = {"object_type": 4, "instance": 2, "commandable": True, "object_name": "Fan"}


def _setup(monkeypatch, unique_id="bacnet_1001_4_2", platform="bacnet", ok=True):
    from custom_components.bacnet import services

    client = MagicMock()
    client.relinquish = AsyncMock(return_value=ok)
    client.write_property = AsyncMock(return_value=ok)
    coordinator = MagicMock()
    coordinator.client = client
    coordinator.objects = [_OBJ]
    coordinator.write_priority = 16
    coordinator.device_address = "192.168.1.9"
    coordinator.async_refresh_object = AsyncMock()
    coordinator.async_refresh_properties = AsyncMock()

    entry = MagicMock()
    entry.runtime_data.coordinator = coordinator
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = entry

    reg_entry = MagicMock(platform=platform, unique_id=unique_id, config_entry_id="e1")
    registry = MagicMock()
    registry.async_get.return_value = reg_entry
    monkeypatch.setattr(services.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(
        services,
        "async_extract_referenced_entity_ids",
        lambda hass, call: MagicMock(
            referenced={"switch.fan"}, indirectly_referenced=set()
        ),
    )
    return services, hass, client, coordinator


def _call(data):
    call = MagicMock()
    call.data = data
    call.context.user_id = None  # system call: no permission check
    return call


class TestRelinquish:
    def test_relinquish_uses_device_priority_by_default(self, monkeypatch):
        services, hass, client, coordinator = _setup(monkeypatch)
        asyncio.run(services.async_handle_relinquish(hass, _call({})))
        assert client.relinquish.await_args.kwargs["priority"] == 16
        assert client.relinquish.await_args.kwargs["instance"] == 2
        coordinator.async_refresh_object.assert_awaited_once_with(_OBJ)

    def test_relinquish_explicit_priority(self, monkeypatch):
        services, hass, client, _ = _setup(monkeypatch)
        asyncio.run(services.async_handle_relinquish(hass, _call({"priority": 8})))
        assert client.relinquish.await_args.kwargs["priority"] == 8


class TestWriteValue:
    def test_write_value_at_priority(self, monkeypatch):
        services, hass, client, _ = _setup(monkeypatch)
        asyncio.run(
            services.async_handle_write_value(
                hass, _call({"value": 1.0, "priority": 9})
            )
        )
        kwargs = client.write_property.await_args.kwargs
        assert kwargs["value"] == 1.0
        assert kwargs["priority"] == 9
        assert kwargs["property_name"] == "presentValue"

    def test_rejected_write_raises(self, monkeypatch):
        from homeassistant.exceptions import HomeAssistantError

        services, hass, _, _ = _setup(monkeypatch, ok=False)
        with pytest.raises(HomeAssistantError):
            asyncio.run(services.async_handle_write_value(hass, _call({"value": 1.0})))


class TestTargetValidation:
    @pytest.mark.parametrize(
        ("unique_id", "platform"),
        [("bacnet_1001_write_priority", "bacnet"), ("x_4_2", "other")],
    )
    def test_non_object_entity_is_rejected(self, monkeypatch, unique_id, platform):
        from homeassistant.exceptions import HomeAssistantError

        services, hass, client, _ = _setup(monkeypatch, unique_id, platform)
        with pytest.raises(HomeAssistantError) as err:
            asyncio.run(services.async_handle_relinquish(hass, _call({})))
        assert err.value.translation_key == "not_a_bacnet_object"
        client.relinquish.assert_not_awaited()


def test_uses_non_deprecated_target_helper():
    """helpers.service.async_extract_referenced_entity_ids is removed in HA 2026.8."""
    import inspect

    from custom_components.bacnet import services

    source = inspect.getsource(services)
    assert "from homeassistant.helpers.target import (" in source


# ---------------------------------------------------------------------------
# bacnet.read_property / write_value property field (Reddit feedback)
# ---------------------------------------------------------------------------


def _with_device(monkeypatch, services, hass, coordinator, *, is_admin=True):
    """Register HA device "dev1" as the device of *coordinator*.

    Calls pick it with the "device" field (a device selector: hassfest
    doesn't allow device filters on a service target).
    """
    device = MagicMock(config_entries={"e1"}, identifiers={("bacnet", "1001")})
    device_registry = MagicMock()
    device_registry.async_get.side_effect = lambda device_id: (
        device if device_id == "dev1" else None
    )
    monkeypatch.setattr(services.dr, "async_get", lambda hass: device_registry)
    user = MagicMock(is_admin=is_admin)
    hass.auth.async_get_user = AsyncMock(return_value=user)


class TestReadProperty:
    def test_read_by_entity(self, monkeypatch):
        services, hass, client, _ = _setup(monkeypatch)
        client.read_any_property = AsyncMock(return_value=30)
        result = asyncio.run(
            services.async_handle_read_property(hass, _call({"property": "67"}))
        )
        assert result == {
            "switch.fan": {"object": "4:2", "property": "minimumOnTime", "value": 30}
        }
        assert client.read_any_property.await_args.args == (
            "192.168.1.9",
            4,
            2,
            "minimumOnTime",
            None,
        )

    def test_read_by_device_and_object(self, monkeypatch):
        services, hass, client, coordinator = _setup(monkeypatch)
        _with_device(monkeypatch, services, hass, coordinator)
        client.read_any_property = AsyncMock(return_value="normal")
        result = asyncio.run(
            services.async_handle_read_property(
                hass,
                _call(
                    {
                        "device": "dev1",
                        "property": "eventState",
                        "object_type": "trend-log",
                        "instance": 5,
                    }
                ),
            )
        )
        assert result == {
            "dev1": {"object": "20:5", "property": "eventState", "value": "normal"}
        }

    def test_device_target_needs_admin(self, monkeypatch):
        from homeassistant.exceptions import Unauthorized

        services, hass, _, coordinator = _setup(monkeypatch)
        _with_device(monkeypatch, services, hass, coordinator, is_admin=False)
        call = _call(
            {
                "device": "dev1",
                "property": "eventState",
                "object_type": 0,
                "instance": 5,
            }
        )
        call.context.user_id = "u1"
        with pytest.raises(Unauthorized):
            asyncio.run(services.async_handle_read_property(hass, call))

    def test_unknown_property(self, monkeypatch):
        from homeassistant.exceptions import HomeAssistantError

        services, hass, _, _ = _setup(monkeypatch)
        with pytest.raises(HomeAssistantError) as err:
            asyncio.run(
                services.async_handle_read_property(hass, _call({"property": "bogus"}))
            )
        assert err.value.translation_key == "unknown_property"

    def test_device_error(self, monkeypatch):
        from homeassistant.exceptions import HomeAssistantError

        from custom_components.bacnet.bacnet_client import BACnetPropertyError

        services, hass, client, _ = _setup(monkeypatch)
        client.read_any_property = AsyncMock(
            side_effect=BACnetPropertyError("unknown-property")
        )
        with pytest.raises(HomeAssistantError) as err:
            asyncio.run(
                services.async_handle_read_property(
                    hass, _call({"property": "minimumOnTime"})
                )
            )
        assert err.value.translation_key == "property_read_failed"
        assert err.value.translation_placeholders["error"] == "unknown-property"


def test_write_value_other_property(monkeypatch):
    services, hass, client, _ = _setup(monkeypatch)
    asyncio.run(
        services.async_handle_write_value(
            hass, _call({"value": 30, "property": "minimum-on-time"})
        )
    )
    kwargs = client.write_property.await_args.kwargs
    assert kwargs["property_name"] == "minimumOnTime"
    assert kwargs["value"] == 30


def test_write_value_checks_control_permission(monkeypatch):
    from homeassistant.exceptions import Unauthorized

    services, hass, client, _ = _setup(monkeypatch)
    user = MagicMock()
    user.permissions.check_entity.return_value = False
    hass.auth.async_get_user = AsyncMock(return_value=user)
    call = _call({"value": 1.0})
    call.context.user_id = "u1"
    with pytest.raises(Unauthorized):
        asyncio.run(services.async_handle_write_value(hass, call))
    client.write_property.assert_not_awaited()


def test_write_other_property_needs_admin(monkeypatch):
    """Control permission covers the value, not the object's configuration."""
    from homeassistant.exceptions import Unauthorized

    services, hass, client, _ = _setup(monkeypatch)
    user = MagicMock(is_admin=False)
    user.permissions.check_entity.return_value = True
    hass.auth.async_get_user = AsyncMock(return_value=user)
    call = _call({"value": 600, "property": "minimumOnTime"})
    call.context.user_id = "u1"
    with pytest.raises(Unauthorized):
        asyncio.run(services.async_handle_write_value(hass, call))
    client.write_property.assert_not_awaited()


def test_read_trend_log_by_device(monkeypatch):
    services, hass, client, coordinator = _setup(monkeypatch)
    _with_device(monkeypatch, services, hass, coordinator)
    client.read_trend_log = AsyncMock(
        return_value=[{"time": "2026-10-19T10:00:00", "value": 21.5}]
    )
    result = asyncio.run(
        services.async_handle_read_trend_log(
            hass, _call({"device": "dev1", "instance": 4, "count": 10})
        )
    )
    assert result == {
        "dev1": {
            "instance": 4,
            "records": [{"time": "2026-10-19T10:00:00", "value": 21.5}],
        }
    }
    assert client.read_trend_log.await_args.kwargs == {"start": None, "count": 10}


def test_device_field_counts_as_target():
    """Without it HA's entity schema rejects a call that only names a device."""
    from custom_components.bacnet import services

    data = {"device": ["dev1"], "property": "highLimit"}
    assert services._target_or_device(data) is data
    with pytest.raises(services.vol.Invalid):
        services._target_or_device({"property": "highLimit"})
    entity_call = {"entity_id": ["switch.fan"], "property": "highLimit"}
    assert services._target_or_device(entity_call) is entity_call


def test_device_without_object_type_is_invalid_object(monkeypatch):
    from homeassistant.exceptions import HomeAssistantError

    services, hass, _, coordinator = _setup(monkeypatch)
    _with_device(monkeypatch, services, hass, coordinator)
    with pytest.raises(HomeAssistantError) as err:
        asyncio.run(
            services.async_handle_read_property(
                hass, _call({"device": "dev1", "property": "highLimit"})
            )
        )
    assert err.value.translation_key == "invalid_object"


def test_device_output_object_is_commandable(monkeypatch):
    """Review: write_value via device to an output must keep its priority."""
    services, hass, client, coordinator = _setup(monkeypatch)
    _with_device(monkeypatch, services, hass, coordinator)
    asyncio.run(
        services.async_handle_write_value(
            hass,
            _call(
                {
                    "device": "dev1",
                    "object_type": "analog-output",
                    "instance": 7,
                    "value": 50.0,
                    "priority": 8,
                }
            ),
        )
    )
    kwargs = client.write_property.await_args.kwargs
    assert kwargs["commandable"] is True
    assert kwargs["priority"] == 8


def test_write_value_bad_value_is_translated(monkeypatch):
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.bacnet.bacnet_client import BACnetPropertyError

    services, hass, client, _ = _setup(monkeypatch)
    with pytest.raises(HomeAssistantError) as err:
        asyncio.run(services.async_handle_write_value(hass, _call({"value": "abc"})))
    assert err.value.translation_key == "invalid_value"

    client.write_property = AsyncMock(
        side_effect=BACnetPropertyError("1.5 is not a valid value for minimumOnTime")
    )
    with pytest.raises(HomeAssistantError) as err:
        asyncio.run(
            services.async_handle_write_value(
                hass, _call({"value": 1.5, "property": "minimumOnTime"})
            )
        )
    assert err.value.translation_key == "invalid_value"


def test_write_value_on_extra_property_entity_writes_that_property(monkeypatch):
    services, hass, client, _ = _setup(
        monkeypatch, unique_id="bacnet_1001_4_2_minimumOnTime"
    )
    asyncio.run(services.async_handle_write_value(hass, _call({"value": 600})))
    kwargs = client.write_property.await_args.kwargs
    assert kwargs["property_name"] == "minimumOnTime"
    assert kwargs["value"] == 600


def test_read_property_on_extra_property_entity_defaults_to_it(monkeypatch):
    services, hass, client, _ = _setup(
        monkeypatch, unique_id="bacnet_1001_4_2_minimumOnTime"
    )
    client.read_any_property = AsyncMock(return_value=300)
    result = asyncio.run(services.async_handle_read_property(hass, _call({})))
    assert result["switch.fan"]["property"] == "minimumOnTime"
