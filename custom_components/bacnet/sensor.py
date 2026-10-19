"""
Sensor platform for BACnet IP integration.

Creates HA sensor entities for BACnet objects mapped to the "sensor" domain.
Typically: Analog Input, Analog Value (read-only), Multi-State Input,
Multi-State Value (read-only).

Sensors are read-only and display the presentValue.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    BACNET_UNITS,
    DOMAIN,
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
    OBJECT_TYPE_SCHEDULE,
    OBJECT_TYPE_TREND_LOG,
)
from .coordinator import BACnetCoordinator
from .entity import BACnetEntity, bacnet_device_info
from .property_entity import property_entities

_LOGGER = logging.getLogger(__name__)

# Alarms listed in the Active alarms attribute (recorder limit: 16 KB).
MAX_ALARMS_ATTRIBUTE = 50


@dataclass(frozen=True)
class _DiagnosticSensor:
    key: str
    value_fn: Callable[[BACnetCoordinator], Any]
    device_class: SensorDeviceClass | None = None
    enabled_default: bool = True


DIAGNOSTIC_SENSORS: tuple[_DiagnosticSensor, ...] = (
    _DiagnosticSensor(
        "last_successful_poll",
        lambda c: c.last_successful_poll,
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    _DiagnosticSensor(
        "cov_subscriptions", lambda c: len(c._cov_subscriptions), enabled_default=False
    ),
    _DiagnosticSensor(
        "consecutive_failed_polls",
        lambda c: c._consecutive_failures,
        enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BACnet sensor entities from a config entry."""
    coordinator: BACnetCoordinator = entry.runtime_data.coordinator
    objects: list[dict[str, Any]] = coordinator.objects

    entities: list[Any] = []
    for obj in objects:
        domain = coordinator.get_domain_for_object(obj)
        if obj["object_type"] == OBJECT_TYPE_TREND_LOG:
            entities.append(BACnetTrendLogSensor(coordinator, entry, obj))
        elif domain == "sensor":
            entities.append(BACnetSensor(coordinator, entry, obj))
    entities.extend(property_entities(coordinator, entry, "sensor"))

    entities.extend(
        BACnetDiagnosticSensor(coordinator, entry, description)
        for description in DIAGNOSTIC_SENSORS
    )
    entities.append(BACnetActiveAlarmsSensor(coordinator, entry))
    async_add_entities(entities)
    _LOGGER.debug("Added %d BACnet sensor entities", len(entities))


class BACnetSensor(BACnetEntity, SensorEntity):
    """Representation of a read-only BACnet object as a HA sensor."""

    def __init__(
        self,
        coordinator: BACnetCoordinator,
        entry: ConfigEntry,
        obj: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, entry, obj)

        # Determine device class and native unit from BACnet units
        unit, device_class = BACNET_UNITS.get(obj.get("units") or "", (None, None))
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = (
            SensorDeviceClass(device_class) if device_class else None
        )

        # Analog types get measurement state class for statistics support
        if obj["object_type"] in {
            OBJECT_TYPE_ANALOG_INPUT,
            OBJECT_TYPE_ANALOG_OUTPUT,
            OBJECT_TYPE_ANALOG_VALUE,
        }:
            self._attr_state_class = SensorStateClass.MEASUREMENT
            # Display hint only — the state keeps full precision (user can
            # change it per entity in the UI).
            self._attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | int | str | None:
        """Return the sensor's current value from the coordinator."""
        value = self.get_present_value()
        if value is None:
            return None
        # Analog values are float, multi-state are int
        if self.object_type in {
            OBJECT_TYPE_ANALOG_INPUT,
            OBJECT_TYPE_ANALOG_OUTPUT,
            OBJECT_TYPE_ANALOG_VALUE,
        }:
            try:
                # BACnet REAL is float32: keep its ~7 significant digits and
                # drop the float64 conversion noise (23.456000328…).
                return float(f"{float(value):.7g}")
            except (ValueError, TypeError):
                return None
        if self.object_type == OBJECT_TYPE_SCHEDULE:
            # A schedule's value can be any primitive type (real, enumerated…)
            return float(f"{value:.7g}") if isinstance(value, float) else value
        # Multi-state values are integers
        try:
            return int(value)
        except (ValueError, TypeError):
            return str(value)


class BACnetTrendLogSensor(BACnetEntity, SensorEntity):
    """A Trend Log object: the number of records it holds.

    The records themselves are read with bacnet.read_trend_log, which can
    target this entity.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        return self.coordinator.get_object_value(self._obj_key, "recordCount")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            **super().extra_state_attributes,
            "bacnet_log_enabled": self.coordinator.get_object_value(
                self._obj_key, "logEnable"
            ),
        }


class BACnetDiagnosticSensor(CoordinatorEntity[BACnetCoordinator], SensorEntity):
    """Device-level health figure (last poll, COV subscriptions, failures)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: BACnetCoordinator,
        entry: ConfigEntry,
        description: _DiagnosticSensor,
    ) -> None:
        super().__init__(coordinator)
        self._description = description
        self._attr_translation_key = description.key
        self._attr_device_class = description.device_class
        self._attr_entity_registry_enabled_default = description.enabled_default
        self._attr_unique_id = (
            f"{DOMAIN}_{entry.data.get('device_id', 'unknown')}_{description.key}"
        )
        self._attr_device_info = bacnet_device_info(entry)

    @property
    def available(self) -> bool:
        """Always available — these matter most while the device is down."""
        return True

    @property
    def native_value(self) -> Any:
        return self._description.value_fn(self.coordinator)


class BACnetActiveAlarmsSensor(CoordinatorEntity[BACnetCoordinator], SensorEntity):
    """Number of active alarms on the device (GetEventInformation).

    The alarms themselves are in the "alarms" attribute; changes also fire
    a bacnet_alarm event. Unknown for devices without the service.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "active_alarms"

    def __init__(self, coordinator: BACnetCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{DOMAIN}_{entry.data.get('device_id', 'unknown')}_active_alarms"
        )
        self._attr_device_info = bacnet_device_info(entry)

    @property
    def native_value(self) -> int | None:
        alarms = self.coordinator.active_alarms
        if alarms is None:
            return None
        # Entries back to normal that only wait for an acknowledgement are
        # listed by the device too; they stay in the attribute only.
        return sum(a["event_state"] != "normal" for a in alarms)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # Active alarms first; capped so the attributes stay under the
        # recorder's 16 KB limit. The state still counts all of them.
        alarms = sorted(
            self.coordinator.active_alarms or [],
            key=lambda a: a["event_state"] == "normal",
        )
        attrs: dict[str, Any] = {"alarms": alarms[:MAX_ALARMS_ATTRIBUTE]}
        if len(alarms) > MAX_ALARMS_ATTRIBUTE:
            attrs["alarms_truncated"] = True
        return attrs
