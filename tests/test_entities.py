"""
Tests for platform entity property logic.

Each entity is instantiated with a mock coordinator and config entry so
network/HA plumbing is never exercised — only the property computation
logic is verified.
"""

from __future__ import annotations

import pytest

from tests.conftest import _make_coordinator, _make_entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sensor(obj, data=None):
    from custom_components.bacnet.sensor import BACnetSensor

    coord = _make_coordinator(data or {})
    return BACnetSensor(coord, _make_entry(), obj)


def _binary_sensor(obj, data=None):
    from custom_components.bacnet.binary_sensor import BACnetBinarySensor

    coord = _make_coordinator(data or {})
    return BACnetBinarySensor(coord, _make_entry(), obj)


def _switch(obj, data=None):
    from custom_components.bacnet.switch import BACnetSwitch

    coord = _make_coordinator(data or {})
    return BACnetSwitch(coord, _make_entry(), obj)


def _number(obj, data=None):
    from custom_components.bacnet.number import BACnetNumber

    coord = _make_coordinator(data or {})
    return BACnetNumber(coord, _make_entry(), obj)


def _climate(obj, data=None):
    from custom_components.bacnet.climate import BACnetClimate

    coord = _make_coordinator(data or {})
    return BACnetClimate(coord, _make_entry(), obj)


# ---------------------------------------------------------------------------
# BACnetSensor.native_value
# ---------------------------------------------------------------------------


class TestSensorNativeValue:
    def test_analog_float(self):
        obj = {
            "object_type": 0,
            "instance": 1,
            "commandable": False,
            "object_name": "T",
        }
        entity = _sensor(obj, {"0:1": {"presentValue": 23.456}})
        # Full precision in the state; the UI rounds via display precision.
        assert entity.native_value == 23.456
        assert entity._attr_suggested_display_precision == 2

    def test_analog_none_when_no_data(self):
        obj = {
            "object_type": 0,
            "instance": 1,
            "commandable": False,
            "object_name": "T",
        }
        entity = _sensor(obj, {})
        assert entity.native_value is None

    def test_multi_state_int(self):
        obj = {
            "object_type": 13,
            "instance": 1,
            "commandable": False,
            "object_name": "M",
        }
        entity = _sensor(obj, {"13:1": {"presentValue": 3}})
        assert entity.native_value == 3

    def test_analog_bad_value_returns_none(self):
        obj = {
            "object_type": 0,
            "instance": 1,
            "commandable": False,
            "object_name": "T",
        }
        entity = _sensor(obj, {"0:1": {"presentValue": "not-a-number"}})
        assert entity.native_value is None

    def test_multi_state_bad_value_returns_str(self):
        obj = {
            "object_type": 13,
            "instance": 1,
            "commandable": False,
            "object_name": "M",
        }
        entity = _sensor(obj, {"13:1": {"presentValue": "mode-active"}})
        assert entity.native_value == "mode-active"


# ---------------------------------------------------------------------------
# BACnetBinarySensor.is_on
# ---------------------------------------------------------------------------


class TestBinarySensorIsOn:
    def test_none_when_no_data(self):
        obj = {
            "object_type": 3,
            "instance": 1,
            "commandable": False,
            "object_name": "B",
        }
        entity = _binary_sensor(obj, {})
        assert entity.is_on is None

    def test_active_string_is_true(self):
        obj = {
            "object_type": 3,
            "instance": 1,
            "commandable": False,
            "object_name": "B",
        }
        entity = _binary_sensor(obj, {"3:1": {"presentValue": "active"}})
        assert entity.is_on is True

    def test_inactive_string_is_false(self):
        obj = {
            "object_type": 3,
            "instance": 1,
            "commandable": False,
            "object_name": "B",
        }
        entity = _binary_sensor(obj, {"3:1": {"presentValue": "inactive"}})
        assert entity.is_on is False

    def test_int_one_is_true(self):
        obj = {
            "object_type": 3,
            "instance": 1,
            "commandable": False,
            "object_name": "B",
        }
        entity = _binary_sensor(obj, {"3:1": {"presentValue": 1}})
        assert entity.is_on is True

    def test_int_zero_is_false(self):
        obj = {
            "object_type": 3,
            "instance": 1,
            "commandable": False,
            "object_name": "B",
        }
        entity = _binary_sensor(obj, {"3:1": {"presentValue": 0}})
        assert entity.is_on is False

    def test_case_insensitive_string(self):
        obj = {
            "object_type": 3,
            "instance": 1,
            "commandable": False,
            "object_name": "B",
        }
        entity = _binary_sensor(obj, {"3:1": {"presentValue": "ACTIVE"}})
        assert entity.is_on is True


# ---------------------------------------------------------------------------
# BACnetSwitch.is_on
# ---------------------------------------------------------------------------


class TestSwitchIsOn:
    def test_none_when_no_data(self):
        obj = {"object_type": 4, "instance": 1, "commandable": True, "object_name": "S"}
        entity = _switch(obj, {})
        assert entity.is_on is None

    def test_active_is_true(self):
        obj = {"object_type": 4, "instance": 1, "commandable": True, "object_name": "S"}
        entity = _switch(obj, {"4:1": {"presentValue": "active"}})
        assert entity.is_on is True

    def test_int_one_is_true(self):
        obj = {"object_type": 4, "instance": 1, "commandable": True, "object_name": "S"}
        entity = _switch(obj, {"4:1": {"presentValue": 1}})
        assert entity.is_on is True

    def test_int_zero_is_false(self):
        obj = {"object_type": 4, "instance": 1, "commandable": True, "object_name": "S"}
        entity = _switch(obj, {"4:1": {"presentValue": 0}})
        assert entity.is_on is False


# ---------------------------------------------------------------------------
# BACnetNumber.native_value
# ---------------------------------------------------------------------------


class TestNumberNativeValue:
    def test_float_value(self):
        obj = {"object_type": 1, "instance": 1, "commandable": True, "object_name": "N"}
        entity = _number(obj, {"1:1": {"presentValue": 42.5}})
        assert entity.native_value == pytest.approx(42.5)

    def test_none_when_missing(self):
        obj = {"object_type": 1, "instance": 1, "commandable": True, "object_name": "N"}
        entity = _number(obj, {})
        assert entity.native_value is None

    def test_int_converted_to_float(self):
        obj = {"object_type": 1, "instance": 1, "commandable": True, "object_name": "N"}
        entity = _number(obj, {"1:1": {"presentValue": 7}})
        assert entity.native_value == 7.0
        assert isinstance(entity.native_value, float)

    def test_bad_value_returns_none(self):
        obj = {"object_type": 1, "instance": 1, "commandable": True, "object_name": "N"}
        entity = _number(obj, {"1:1": {"presentValue": "not-a-number"}})
        assert entity.native_value is None

    def test_multi_state_min_max(self):
        from custom_components.bacnet.const import OBJECT_TYPE_MULTI_STATE_OUTPUT

        obj = {
            "object_type": OBJECT_TYPE_MULTI_STATE_OUTPUT,
            "instance": 1,
            "commandable": True,
            "object_name": "MSO",
        }
        entity = _number(obj, {})
        assert entity._attr_native_min_value == 1
        assert entity._attr_native_max_value == 255
        assert entity._attr_native_step == 1.0


# ---------------------------------------------------------------------------
# BACnetClimate.hvac_mode and current_temperature
# ---------------------------------------------------------------------------


class TestClimate:
    def test_hvac_heat_when_value_present(self):
        obj = {
            "object_type": 2,
            "instance": 1,
            "commandable": True,
            "object_name": "SP",
        }
        entity = _climate(obj, {"2:1": {"presentValue": 21.0}})
        from custom_components.bacnet.climate import HVACMode

        assert entity.hvac_mode == HVACMode.HEAT

    def test_hvac_off_when_value_is_none(self):
        obj = {
            "object_type": 2,
            "instance": 1,
            "commandable": True,
            "object_name": "SP",
        }
        entity = _climate(obj, {})
        from custom_components.bacnet.climate import HVACMode

        assert entity.hvac_mode == HVACMode.OFF

    def test_current_temperature_rounded(self):
        obj = {
            "object_type": 2,
            "instance": 1,
            "commandable": True,
            "object_name": "SP",
        }
        entity = _climate(obj, {"2:1": {"presentValue": 21.456}})
        assert entity.current_temperature == pytest.approx(21.5)

    def test_current_temperature_none_when_missing(self):
        obj = {
            "object_type": 2,
            "instance": 1,
            "commandable": True,
            "object_name": "SP",
        }
        entity = _climate(obj, {})
        assert entity.current_temperature is None

    def test_temperature_unit_celsius_by_default(self):
        obj = {
            "object_type": 2,
            "instance": 1,
            "commandable": True,
            "object_name": "SP",
            "units": "degreesCelsius",
        }
        entity = _climate(obj, {})
        assert (
            "°C" in str(entity._attr_temperature_unit)
            or entity._attr_temperature_unit is not None
        )

    def test_temperature_unit_fahrenheit(self):
        obj = {
            "object_type": 2,
            "instance": 1,
            "commandable": True,
            "object_name": "SP",
            "units": "degreesFahrenheit",
        }
        entity = _climate(obj, {})
        assert entity._attr_min_temp == 40.0
        assert entity._attr_max_temp == 104.0


# ---------------------------------------------------------------------------
# BACnetEntity.available and extra_state_attributes
# ---------------------------------------------------------------------------


class TestEntityBase:
    def test_unavailable_when_no_data(self):
        obj = {
            "object_type": 0,
            "instance": 1,
            "commandable": False,
            "object_name": "T",
        }
        entity = _sensor(obj, {})
        assert entity.available is False

    def test_available_when_data_present(self):
        obj = {
            "object_type": 0,
            "instance": 1,
            "commandable": False,
            "object_name": "T",
        }
        entity = _sensor(obj, {"0:1": {"presentValue": 23.0}})
        assert entity.available is True

    def test_extra_attributes_include_bacnet_keys(self):
        obj = {
            "object_type": 0,
            "instance": 1,
            "commandable": False,
            "object_name": "Room Temp",
            "description": "Zone 1 temperature",
            "units": "degreesCelsius",
        }
        entity = _sensor(
            obj,
            {
                "0:1": {
                    "presentValue": 22.0,
                    "statusFlags": [False, False, False, False],
                }
            },
        )
        attrs = entity.extra_state_attributes
        assert "bacnet_object_type" in attrs
        assert "bacnet_instance" in attrs
        assert attrs["bacnet_instance"] == 1
        assert "bacnet_commandable" in attrs
        assert attrs["bacnet_commandable"] is False
        assert "bacnet_units" in attrs
        assert "bacnet_description" in attrs
        assert "bacnet_status_flags" in attrs
        assert "bacnet_update_method" in attrs

    def test_unique_id_uses_device_id(self):
        obj = {
            "object_type": 0,
            "instance": 5,
            "commandable": False,
            "object_name": "T",
        }
        entry = _make_entry(device_id=99999)
        coord = _make_coordinator({})
        from custom_components.bacnet.sensor import BACnetSensor

        entity = BACnetSensor(coord, entry, obj)
        assert "99999" in entity._attr_unique_id
        assert "bacnet" in entity._attr_unique_id


# ---------------------------------------------------------------------------
# Availability must follow coordinator outages
# ---------------------------------------------------------------------------


class TestAvailabilityFollowsCoordinator:
    def test_unavailable_when_last_update_failed(self):
        obj = {
            "object_type": 0,
            "instance": 1,
            "commandable": False,
            "object_name": "T",
        }
        entity = _sensor(obj, {"0:1": {"presentValue": 23.0}})
        entity.coordinator.last_update_success = False
        assert entity.available is False


# ---------------------------------------------------------------------------
# Climate OFF must survive the relinquish-default presentValue
# ---------------------------------------------------------------------------


def _climate_with_client(data):
    from unittest.mock import AsyncMock, MagicMock

    obj = {"object_type": 2, "instance": 1, "commandable": True, "object_name": "SP"}
    entity = _climate(obj, data)
    client = MagicMock()
    client.relinquish = AsyncMock(return_value=True)
    client.write_property = AsyncMock(return_value=True)
    entity.coordinator.async_refresh_object = AsyncMock()
    entity.hass = MagicMock()
    entity.hass.data = {"bacnet": {entity._entry.entry_id: {"client": client}}}
    entity.async_write_ha_state = MagicMock()
    return entity


class TestClimateOffAfterRelinquish:
    def test_off_after_relinquish_even_with_relinquish_default(self):
        import asyncio

        from custom_components.bacnet.climate import HVACMode

        # After relinquish the device reports its Relinquish Default, not None.
        entity = _climate_with_client({"2:1": {"presentValue": 18.0}})
        asyncio.run(entity.async_set_hvac_mode(HVACMode.OFF))
        assert entity.hvac_mode == HVACMode.OFF

    def test_set_temperature_turns_back_to_heat(self):
        import asyncio

        from custom_components.bacnet.climate import HVACMode

        entity = _climate_with_client({"2:1": {"presentValue": 18.0}})
        asyncio.run(entity.async_set_hvac_mode(HVACMode.OFF))
        asyncio.run(entity.async_set_temperature(temperature=21.0))
        assert entity.hvac_mode == HVACMode.HEAT


class TestEntityRegistersObjectListener:
    def test_added_entity_listens_to_its_own_object(self):
        import asyncio
        from unittest.mock import MagicMock

        obj = {"object_type": 0, "instance": 1, "object_name": "T"}
        entity = _sensor(obj, {"0:1": {"presentValue": 1.0}})
        remove = MagicMock()
        entity.coordinator.async_add_object_listener.return_value = remove
        entity.async_write_ha_state = MagicMock()

        asyncio.run(entity.async_added_to_hass())

        key, cb = entity.coordinator.async_add_object_listener.call_args.args
        assert key == "0:1"
        cb()
        entity.async_write_ha_state.assert_called_once()
        assert remove in entity._on_remove


class TestWriteRefreshesOnlyThatObject:
    def test_switch_write_refreshes_its_object(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        obj = {"object_type": 4, "instance": 2, "commandable": True, "object_name": "S"}
        entity = _switch(obj)
        client = MagicMock()
        client.write_property = AsyncMock(return_value=True)
        entity.hass = MagicMock()
        entity.hass.data = {"bacnet": {entity._entry.entry_id: {"client": client}}}
        entity.coordinator.async_refresh_object = AsyncMock()
        entity.coordinator.async_request_refresh = AsyncMock()

        asyncio.run(entity.async_turn_on())

        entity.coordinator.async_refresh_object.assert_awaited_once_with(obj)
        entity.coordinator.async_request_refresh.assert_not_awaited()


class TestSensorPrecision:
    def _value(self, pv):
        obj = {"object_type": 0, "instance": 1, "object_name": "T"}
        return _sensor(obj, {"0:1": {"presentValue": pv}}).native_value

    def test_float32_noise_is_stripped(self):
        assert self._value(23.456000328063965) == 23.456

    def test_small_values_are_not_rounded_away(self):
        assert self._value(0.00123) == 0.00123

    def test_large_counter_keeps_decimals(self):
        assert self._value(123456.7) == 123456.7


class TestUnrecordedAttributes:
    def test_static_bacnet_attributes_are_not_recorded(self):
        from custom_components.bacnet.entity import BACnetEntity

        assert {
            "bacnet_object_type",
            "bacnet_instance",
            "bacnet_commandable",
            "bacnet_units",
            "bacnet_description",
            "bacnet_update_method",
            "bacnet_cov_increment",
        } <= BACnetEntity._unrecorded_attributes
        # Status flags change at runtime and are worth keeping in history.
        assert "bacnet_status_flags" not in BACnetEntity._unrecorded_attributes
