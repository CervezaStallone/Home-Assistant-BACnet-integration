"""
Lightweight HA stubs so tests run without Home Assistant installed.

All HA modules are registered in sys.modules BEFORE any integration code
is imported, so every 'from homeassistant…' inside the integration gets
these stubs instead of the real HA package.
"""

from __future__ import annotations

import sys
from enum import Enum
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Core stubs
# ---------------------------------------------------------------------------


def _noop_callback(fn):
    """Stub for HA's @callback decorator — returns the function unchanged."""
    return fn


class _DataUpdateCoordinator:
    """Minimal DataUpdateCoordinator stub."""

    def __init__(self, hass, logger, *, name, update_interval):
        self.hass = hass
        self.name = name
        self.data = None
        self._listeners: dict = {}

    # Allow DataUpdateCoordinator[SomeType] generic syntax (Python 3.9+)
    def __class_getitem__(cls, item):
        return cls

    def async_update_listeners(self) -> None:
        pass

    async def async_config_entry_first_refresh(self) -> None:
        pass

    async def async_request_refresh(self) -> None:
        pass


class _CoordinatorEntity:
    """Minimal CoordinatorEntity stub."""

    _attr_has_entity_name = False
    _attr_unique_id: str | None = None
    _attr_name: str | None = None
    _attr_device_info: dict | None = None
    hass: Any = None

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator

    def __class_getitem__(cls, item):
        return cls


class _DeviceInfo(dict):
    """Stub for HA's DeviceInfo TypedDict — just a dict."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)


# ---------------------------------------------------------------------------
# Platform stubs
# ---------------------------------------------------------------------------


class _SensorEntity(_CoordinatorEntity):
    _attr_device_class = None
    _attr_native_unit_of_measurement = None
    _attr_state_class = None


class _SensorDeviceClass:
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    PRESSURE = "pressure"
    POWER = "power"
    ENERGY = "energy"
    CURRENT = "current"
    VOLTAGE = "voltage"
    FREQUENCY = "frequency"
    VOLUME_FLOW_RATE = "volume_flow_rate"


class _SensorStateClass:
    MEASUREMENT = "measurement"


class _BinarySensorEntity(_CoordinatorEntity):
    pass


class _SwitchEntity(_CoordinatorEntity):
    pass


class _NumberMode(str, Enum):
    BOX = "box"


class _NumberEntity(_CoordinatorEntity):
    _attr_mode = None
    _attr_native_min_value: float | None = None
    _attr_native_max_value: float | None = None
    _attr_native_step: float | None = None
    _attr_native_unit_of_measurement: str | None = None


class _ClimateEntityFeature(int, Enum):
    TARGET_TEMPERATURE = 1
    TURN_ON = 2
    TURN_OFF = 4


class _HVACMode(str, Enum):
    HEAT = "heat"
    OFF = "off"


class _UnitOfTemperature(str, Enum):
    CELSIUS = "°C"
    FAHRENHEIT = "°F"


class _ClimateEntity(_CoordinatorEntity):
    _attr_supported_features = None
    _attr_hvac_modes: list | None = None
    _attr_target_temperature_step: float | None = None
    _attr_min_temp: float | None = None
    _attr_max_temp: float | None = None
    _attr_temperature_unit: str | None = None


class _Platform(str, Enum):
    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    SWITCH = "switch"
    NUMBER = "number"
    CLIMATE = "climate"


# ---------------------------------------------------------------------------
# Build mock modules
# ---------------------------------------------------------------------------

_ha_core = MagicMock()
_ha_core.callback = _noop_callback
_ha_core.HomeAssistant = MagicMock
_ha_core.CALLBACK_TYPE = MagicMock

_ha_config_entries = MagicMock()
_ha_config_entries.ConfigEntry = MagicMock
_ha_config_entries.OptionsFlow = object  # options_flow inherits from this

_ha_const = MagicMock()
_ha_const.Platform = _Platform
_ha_const.ATTR_TEMPERATURE = "temperature"
_ha_const.UnitOfTemperature = _UnitOfTemperature

_ha_exceptions = MagicMock()
_ha_exceptions.ConfigEntryNotReady = Exception

_ha_coordinator_mod = MagicMock()
_ha_coordinator_mod.CoordinatorEntity = _CoordinatorEntity
_ha_coordinator_mod.DataUpdateCoordinator = _DataUpdateCoordinator
_ha_coordinator_mod.UpdateFailed = Exception

_ha_device_registry = MagicMock()
_ha_device_registry.DeviceInfo = _DeviceInfo

_ha_sensor_mod = MagicMock()
_ha_sensor_mod.SensorEntity = _SensorEntity
_ha_sensor_mod.SensorDeviceClass = _SensorDeviceClass
_ha_sensor_mod.SensorStateClass = _SensorStateClass

_ha_binary_sensor_mod = MagicMock()
_ha_binary_sensor_mod.BinarySensorEntity = _BinarySensorEntity

_ha_switch_mod = MagicMock()
_ha_switch_mod.SwitchEntity = _SwitchEntity

_ha_number_mod = MagicMock()
_ha_number_mod.NumberEntity = _NumberEntity
_ha_number_mod.NumberMode = _NumberMode

_ha_climate_mod = MagicMock()
_ha_climate_mod.ClimateEntity = _ClimateEntity
_ha_climate_mod.ClimateEntityFeature = _ClimateEntityFeature
_ha_climate_mod.HVACMode = _HVACMode

_voluptuous = MagicMock()
_voluptuous.Schema = dict  # vol.Schema({…}) → just a dict for stub purposes

_ha_flow = MagicMock()

sys.modules.update(
    {
        "homeassistant": MagicMock(),
        "homeassistant.core": _ha_core,
        "homeassistant.config_entries": _ha_config_entries,
        "homeassistant.const": _ha_const,
        "homeassistant.exceptions": _ha_exceptions,
        "homeassistant.components": MagicMock(),
        "homeassistant.components.sensor": _ha_sensor_mod,
        "homeassistant.components.binary_sensor": _ha_binary_sensor_mod,
        "homeassistant.components.switch": _ha_switch_mod,
        "homeassistant.components.number": _ha_number_mod,
        "homeassistant.components.climate": _ha_climate_mod,
        "homeassistant.helpers": MagicMock(),
        "homeassistant.helpers.update_coordinator": _ha_coordinator_mod,
        "homeassistant.helpers.device_registry": _ha_device_registry,
        "homeassistant.helpers.entity_platform": MagicMock(),
        "homeassistant.helpers.config_validation": MagicMock(),
        "homeassistant.data_entry_flow": _ha_flow,
        "voluptuous": _voluptuous,
    }
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_coordinator(data: dict | None = None) -> MagicMock:
    coord = MagicMock()
    coord.data = data or {}
    coord.device_address = "192.168.1.100"
    coord.get_object_value.side_effect = lambda key, prop="presentValue": (
        coord.data.get(key, {}).get(prop)
    )
    coord.get_entity_name.return_value = "Test Object"
    coord.get_update_method.return_value = "polling"
    coord.get_cov_increment_for.return_value = None
    coord.is_cov_subscribed.return_value = False
    return coord


def _make_entry(
    device_id: int = 1001,
    device_name: str = "Test Device",
    vendor_name: str = "ACME Corp",
    model_name: str = "Model X",
    firmware_version: str = "2.3",
    software_version: str = "1.0",
    device_address: str = "192.168.1.100",
) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.data = {
        "device_id": device_id,
        "device_name": device_name,
        "vendor_name": vendor_name,
        "model_name": model_name,
        "firmware_version": firmware_version,
        "software_version": software_version,
        "device_address": device_address,
    }
    entry.options = {}
    return entry


@pytest.fixture
def coordinator():
    return _make_coordinator()


@pytest.fixture
def entry():
    return _make_entry()


@pytest.fixture
def coordinator_with_data():
    return _make_coordinator(
        {
            "0:1": {"presentValue": 23.5, "statusFlags": [False, False, False, False]},
            "4:2": {"presentValue": 1, "statusFlags": [False, False, False, False]},
        }
    )
