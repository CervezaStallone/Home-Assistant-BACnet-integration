"""Entities use the richer metadata: units, limits, resolution, state texts."""

from __future__ import annotations

import asyncio
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import _make_coordinator, _make_entry


def _make(module, cls, obj, data=None):
    import importlib

    klass = getattr(importlib.import_module(f"custom_components.bacnet.{module}"), cls)
    return klass(_make_coordinator(data or {}), _make_entry(), obj)


class TestUnits:
    @pytest.mark.parametrize(
        ("bacnet", "unit", "device_class"),
        [
            ("degrees-kelvin", "K", "temperature"),
            ("luxes", "lx", "illuminance"),
            ("cubic-meters-per-hour", "m³/h", "volume_flow_rate"),
            ("megawatt-hours", "MWh", "energy"),
            ("parts-per-million", "ppm", None),
            ("percent", "%", None),
        ],
    )
    def test_sensor_unit_and_device_class(self, bacnet, unit, device_class):
        obj = {"object_type": 0, "instance": 1, "object_name": "X", "units": bacnet}
        entity = _make("sensor", "BACnetSensor", obj)
        assert entity._attr_native_unit_of_measurement == unit
        assert entity._attr_device_class == device_class

    def test_number_uses_same_unit_table(self):
        obj = {"object_type": 1, "instance": 1, "object_name": "X", "units": "luxes"}
        assert (
            _make("number", "BACnetNumber", obj)._attr_native_unit_of_measurement
            == "lx"
        )


class TestNumberLimits:
    def test_analog_limits_and_step_from_device(self):
        obj = {
            "object_type": 1,
            "instance": 1,
            "object_name": "Damper",
            "min_value": 0.0,
            "max_value": 100.0,
            "resolution": 0.5,
        }
        entity = _make("number", "BACnetNumber", obj)
        assert entity._attr_native_min_value == 0.0
        assert entity._attr_native_max_value == 100.0
        assert entity._attr_native_step == 0.5

    def test_multistate_max_from_number_of_states(self):
        obj = {
            "object_type": 14,
            "instance": 1,
            "object_name": "M",
            "number_of_states": 4,
        }
        entity = _make("number", "BACnetNumber", obj)
        assert (entity._attr_native_min_value, entity._attr_native_max_value) == (1, 4)

    def test_defaults_without_metadata(self):
        obj = {"object_type": 1, "instance": 1, "object_name": "X"}
        entity = _make("number", "BACnetNumber", obj)
        assert entity._attr_native_min_value == -1_000_000


class TestClimateLimits:
    def test_setpoint_limits_from_device(self):
        obj = {
            "object_type": 2,
            "instance": 1,
            "object_name": "SP",
            "commandable": True,
            "min_value": 15.0,
            "max_value": 28.0,
        }
        entity = _make("climate", "BACnetClimate", obj)
        assert (entity._attr_min_temp, entity._attr_max_temp) == (15.0, 28.0)


_MODE_OBJ = {
    "object_type": 19,
    "instance": 2,
    "object_name": "Mode",
    "commandable": True,
    "state_text": ["Off", "Heat", "Cool"],
    "number_of_states": 3,
}


class TestMultiStateText:
    def test_sensor_exposes_current_state_text(self):
        entity = _make(
            "sensor", "BACnetSensor", _MODE_OBJ, {"19:2": {"presentValue": 2}}
        )
        assert entity.native_value == 2
        assert entity.extra_state_attributes["bacnet_state_text"] == "Heat"

    def test_select_uses_state_texts(self):
        entity = _make(
            "select", "BACnetMultiStateSelect", _MODE_OBJ, {"19:2": {"presentValue": 3}}
        )
        assert entity.options == ["Off", "Heat", "Cool"]
        assert entity.current_option == "Cool"

    def test_select_without_state_text_uses_numbers(self):
        obj = {**_MODE_OBJ, "state_text": None}
        entity = _make(
            "select", "BACnetMultiStateSelect", obj, {"19:2": {"presentValue": 1}}
        )
        assert entity.options == ["1", "2", "3"]
        assert entity.current_option == "1"

    def test_select_writes_one_based_state(self):
        entity = _make("select", "BACnetMultiStateSelect", _MODE_OBJ)
        client = MagicMock()
        client.write_property = AsyncMock(return_value=True)
        entity.coordinator.client = client
        entity.coordinator.async_refresh_object = AsyncMock()
        asyncio.run(entity.async_select_option("Heat"))
        assert client.write_property.await_args.kwargs["value"] == 2

    def test_select_is_an_available_domain(self):
        from custom_components.bacnet.const import SUPPORTED_DOMAINS

        assert "select" in SUPPORTED_DOMAINS


class TestClimateTemperatureSourceEntity:
    _SP: ClassVar[dict] = {
        "object_type": 2,
        "instance": 4,
        "commandable": True,
        "object_name": "SP",
    }

    def _climate(self, sources):
        data = {"2:4": {"presentValue": 21.0}, "0:3": {"presentValue": 19.26}}
        entity = _make("climate", "BACnetClimate", self._SP, data)
        entity.coordinator.climate_temperature_sources = sources
        return entity

    def test_current_temperature_from_source_object(self):
        entity = self._climate({"2:4": "0:3"})
        assert entity.current_temperature == 19.3
        assert entity.target_temperature == 21.0

    def test_without_source_setpoint_is_shown(self):
        entity = self._climate({})
        assert entity.current_temperature == 21.0

    def test_listens_to_source_object_updates(self):
        entity = self._climate({"2:4": "0:3"})
        entity.async_write_ha_state = MagicMock()
        asyncio.run(entity.async_added_to_hass())
        keys = [
            c.args[0]
            for c in entity.coordinator.async_add_object_listener.call_args_list
        ]
        assert keys == ["2:4", "0:3"]
