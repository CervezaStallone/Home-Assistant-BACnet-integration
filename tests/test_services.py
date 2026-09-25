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
