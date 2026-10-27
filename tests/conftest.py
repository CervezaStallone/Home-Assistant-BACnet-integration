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
    """Stub for HA's @callback decorator — marks the function like HA does."""
    fn._hass_callback = True
    return fn


class _DataUpdateCoordinator:
    """Minimal DataUpdateCoordinator stub."""

    def __init__(self, hass, logger, *, name, update_interval):
        self.hass = hass
        self.name = name
        self.data = None
        self.last_update_success = True
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

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_added_to_hass(self) -> None:
        pass

    def async_on_remove(self, func) -> None:
        self.__dict__.setdefault("_on_remove", []).append(func)


class _RestoreEntity:
    """Minimal RestoreEntity stub."""

    async def async_get_last_state(self):
        return None


class _DeviceInfo(dict):
    """Stub for HA's DeviceInfo TypedDict — just a dict."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)


# ---------------------------------------------------------------------------
# Platform stubs
# ---------------------------------------------------------------------------


class _SensorEntity:
    _attr_device_class = None
    _attr_native_unit_of_measurement = None
    _attr_state_class = None


class _SensorDeviceClass(str, Enum):
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    PRESSURE = "pressure"
    POWER = "power"
    APPARENT_POWER = "apparent_power"
    ENERGY = "energy"
    CURRENT = "current"
    VOLTAGE = "voltage"
    FREQUENCY = "frequency"
    VOLUME_FLOW_RATE = "volume_flow_rate"
    VOLUME = "volume"
    SPEED = "speed"
    ILLUMINANCE = "illuminance"
    WEIGHT = "weight"
    DURATION = "duration"
    TIMESTAMP = "timestamp"


class _SensorStateClass:
    MEASUREMENT = "measurement"


class _BinarySensorEntity:
    # Like HA: a platform entity, not a CoordinatorEntity, so
    # (CoordinatorEntity, BinarySensorEntity) has a valid MRO.
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


class _EntityCategory(str, Enum):
    CONFIG = "config"
    DIAGNOSTIC = "diagnostic"


class _Platform(str, Enum):
    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    SWITCH = "switch"
    NUMBER = "number"
    CLIMATE = "climate"
    SELECT = "select"
    BUTTON = "button"


class _ButtonEntity:
    pass


class _SelectEntity:
    _attr_options: list | None = None
    _attr_current_option: str | None = None


# ---------------------------------------------------------------------------
# Build mock modules
# ---------------------------------------------------------------------------


class _ModuleStub(MagicMock):
    """MagicMock for a stubbed module that tests may monkeypatch.

    pytest >= 9.2 takes the value to restore from the instance ``__dict__``,
    where a MagicMock never keeps its auto-created attributes, so undo()
    deletes them, and a MagicMock remembers a deleted attribute: every later
    test then got AttributeError for e.g. ``vol.Optional``. Deleting here
    only drops the override, so the attribute is auto-created again.
    """

    def __delattr__(self, name):
        self.__dict__.pop(name, None)
        self._mock_children.pop(name, None)


_ha_core = _ModuleStub()
_ha_core.callback = _noop_callback
_ha_core.HomeAssistant = MagicMock
_ha_core.CALLBACK_TYPE = MagicMock

_ha_config_entries = _ModuleStub()
_ha_config_entries.ConfigEntry = MagicMock
_ha_config_entries.OptionsFlow = object  # options_flow inherits from this


class _ConfigFlow:
    """Minimal ConfigFlow stub (accepts the domain= class keyword)."""

    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)


_ha_config_entries.ConfigFlow = _ConfigFlow

_ha_const = _ModuleStub()
_ha_const.Platform = _Platform
_ha_const.ATTR_TEMPERATURE = "temperature"
_ha_const.__version__ = "2026.10.1"
_ha_const.UnitOfTemperature = _UnitOfTemperature
_ha_const.EntityCategory = _EntityCategory


class _HomeAssistantError(Exception):
    """Stub accepting HA's translation kwargs."""

    def __init__(
        self,
        *args,
        translation_domain=None,
        translation_key=None,
        translation_placeholders=None,
    ):
        super().__init__(*args)
        self.translation_key = translation_key
        self.translation_placeholders = translation_placeholders


_ha_exceptions = _ModuleStub()
_ha_exceptions.ConfigEntryNotReady = Exception
_ha_exceptions.HomeAssistantError = _HomeAssistantError


class _Unauthorized(_HomeAssistantError):
    """Stub accepting HA's context / entity_id / permission kwargs."""

    def __init__(self, context=None, entity_id=None, permission=None):
        super().__init__()
        self.entity_id = entity_id
        self.permission = permission


_ha_exceptions.Unauthorized = _Unauthorized

_ha_coordinator_mod = _ModuleStub()
_ha_coordinator_mod.CoordinatorEntity = _CoordinatorEntity
_ha_coordinator_mod.DataUpdateCoordinator = _DataUpdateCoordinator
_ha_coordinator_mod.UpdateFailed = Exception


class _RepairsFlow:
    """Minimal RepairsFlow stub."""

    hass: Any = None

    def async_show_form(self, *, step_id, **kwargs):
        return {"type": "form", "step_id": step_id}

    def async_show_menu(self, *, step_id, menu_options, **kwargs):
        return {"type": "menu", "step_id": step_id, "menu_options": menu_options}

    def async_create_entry(self, *, data, **kwargs):
        return {"type": "create_entry", "data": data}


def _async_redact_data(data, to_redact):
    return {k: ("**REDACTED**" if k in to_redact else v) for k, v in data.items()}


_ha_diagnostics_mod = _ModuleStub()
_ha_diagnostics_mod.async_redact_data = _async_redact_data

_ha_repairs_mod = _ModuleStub()
_ha_repairs_mod.RepairsFlow = _RepairsFlow

_ha_restore_state = _ModuleStub()
_ha_restore_state.RestoreEntity = _RestoreEntity

_ha_device_registry = _ModuleStub()
_ha_device_registry.DeviceInfo = _DeviceInfo

_ha_sensor_mod = _ModuleStub()
_ha_sensor_mod.SensorEntity = _SensorEntity
_ha_sensor_mod.SensorDeviceClass = _SensorDeviceClass
_ha_sensor_mod.SensorStateClass = _SensorStateClass

_ha_binary_sensor_mod = _ModuleStub()
_ha_binary_sensor_mod.BinarySensorEntity = _BinarySensorEntity

_ha_switch_mod = _ModuleStub()
_ha_switch_mod.SwitchEntity = _SwitchEntity

_ha_number_mod = _ModuleStub()
_ha_number_mod.NumberEntity = _NumberEntity
_ha_number_mod.NumberMode = _NumberMode

_ha_climate_mod = _ModuleStub()
_ha_climate_mod.ClimateEntity = _ClimateEntity
_ha_climate_mod.ClimateEntityFeature = _ClimateEntityFeature
_ha_climate_mod.HVACMode = _HVACMode

_ha_button_mod = _ModuleStub()
_ha_button_mod.ButtonEntity = _ButtonEntity

_ha_select_mod = _ModuleStub()
_ha_select_mod.SelectEntity = _SelectEntity

_voluptuous = _ModuleStub()
_voluptuous.Schema = dict  # vol.Schema({…}) → just a dict for stub purposes
_voluptuous.Invalid = type("Invalid", (Exception,), {})

_ha_flow = _ModuleStub()

_ha_selector = _ModuleStub()
_ha_selector.SelectSelector = lambda config: ("select", config)
_ha_selector.SelectSelectorConfig = dict
_ha_selector.SelectOptionDict = dict
_ha_selector.SelectSelectorMode = MagicMock(DROPDOWN="dropdown")

_homeassistant = _ModuleStub()
# `from homeassistant import config_entries` resolves via attribute access,
# not sys.modules — point it at the same stub.
_homeassistant.config_entries = _ha_config_entries

sys.modules.update(
    {
        "homeassistant": _homeassistant,
        "homeassistant.core": _ha_core,
        "homeassistant.config_entries": _ha_config_entries,
        "homeassistant.const": _ha_const,
        "homeassistant.exceptions": _ha_exceptions,
        "homeassistant.auth": _ModuleStub(),
        "homeassistant.auth.permissions": _ModuleStub(),
        "homeassistant.auth.permissions.const": _ModuleStub(),
        "homeassistant.components": _ModuleStub(),
        "homeassistant.components.sensor": _ha_sensor_mod,
        "homeassistant.components.binary_sensor": _ha_binary_sensor_mod,
        "homeassistant.components.switch": _ha_switch_mod,
        "homeassistant.components.number": _ha_number_mod,
        "homeassistant.components.climate": _ha_climate_mod,
        "homeassistant.components.button": _ha_button_mod,
        "homeassistant.components.select": _ha_select_mod,
        "homeassistant.components.repairs": _ha_repairs_mod,
        "homeassistant.components.diagnostics": _ha_diagnostics_mod,
        "homeassistant.helpers": _ModuleStub(),
        "homeassistant.helpers.update_coordinator": _ha_coordinator_mod,
        "homeassistant.helpers.device_registry": _ha_device_registry,
        "homeassistant.helpers.restore_state": _ha_restore_state,
        "homeassistant.helpers.entity_platform": _ModuleStub(),
        "homeassistant.helpers.config_validation": _ModuleStub(),
        "homeassistant.helpers.selector": _ha_selector,
        "homeassistant.helpers.service": _ModuleStub(),
        "homeassistant.helpers.target": _ModuleStub(),
        "homeassistant.helpers.storage": _ModuleStub(),
        "homeassistant.helpers.aiohttp_client": _ModuleStub(),
        "homeassistant.helpers.event": _ModuleStub(),
        "homeassistant.loader": _ModuleStub(),
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
    coord.last_update_success = True
    coord.climate_temperature_sources = {}
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
