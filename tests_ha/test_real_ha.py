"""Smoke tests against a REAL Home Assistant (pytest-homeassistant-custom-component).

Only the BACnet network layer (BACnetClient) is faked; everything else —
config entries, entity platforms, registries, repairs, services, flows —
is the real Home Assistant. The unit tests in tests/ run against stubbed
HA modules and can't see API changes; these catch them (e.g. a helper
removed in a new HA release).

Run with:  cd tests_ha && pytest
CI runs them against the minimum supported and the latest Home Assistant.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "bacnet"

OBJECTS = [
    {
        "object_type": 0,
        "instance": 1,
        "object_name": "Room Temp",
        "units": "degrees-celsius",
        "description": "",
        "commandable": False,
    },
    {
        "object_type": 4,
        "instance": 2,
        "object_name": "Fan",
        "units": None,
        "description": "",
        "commandable": True,
    },
    {
        "object_type": 2,
        "instance": 3,
        "object_name": "Setpoint",
        "units": "degrees-celsius",
        "description": "",
        "commandable": True,
        "min_value": 15.0,
        "max_value": 28.0,
    },
    {
        "object_type": 19,
        "instance": 4,
        "object_name": "Mode",
        "units": None,
        "description": "",
        "commandable": True,
        "state_text": ["Off", "Heat", "Cool"],
        "number_of_states": 3,
    },
    {
        "object_type": 5,
        "instance": 5,
        "object_name": "Alarm",
        "units": None,
        "description": "",
        "commandable": False,
    },
]

VALUES = {"0:1": 21.46, "4:2": 1, "2:3": 20.0, "19:4": 2, "5:5": 0}

DATA = {
    "local_ip": "",
    "local_port": 47808,
    "use_bbmd": False,
    "bbmd_address": "",
    "bbmd_ttl": 900,
    "device_id": 1001,
    "device_name": "AHU 1",
    "device_address": "192.168.1.50",
    "vendor_name": "ACME",
    "model_name": "X",
    "firmware_version": "1",
    "software_version": "2",
    "selected_objects": OBJECTS,
}

OPTIONS = {
    "polling_interval": 30,
    # 2:3 → climate with room temperature from 0:1; 19:4 → select;
    # 5:5 → the pre-1.0.47 bug signature (non-commandable BV forced to switch).
    "domain_mapping": {"2:3": "climate", "19:4": "select", "5:5": "switch"},
    "climate_temperature_sources": {"2:3": "0:1"},
}


class FakeClient:
    instances: ClassVar[list[FakeClient]] = []
    offline = False
    write_ok = True

    def __init__(self, local_ip="", local_port=47808, device_instance=None):
        self.local_port = local_port
        self._app = object()
        self._rpm_supported: dict[str, bool] = {}
        self._rpm_chunk_size: dict[str, int] = {}
        self.writes: list[dict[str, Any]] = []
        self.relinquishes: list[dict[str, Any]] = []
        self.disconnected = False
        FakeClient.instances.append(self)

    async def connect(self, bbmd_address=None, bbmd_ttl=900):
        return None

    async def disconnect(self, *, graceful=True):
        self.disconnected = True

    async def reconnect(self):
        return None

    async def poll_objects(
        self, device_address, objects, property_names=None, device_id=None
    ):
        if FakeClient.offline:
            return {
                f"{o['object_type']}:{o['instance']}": {
                    "presentValue": None,
                    "statusFlags": None,
                }
                for o in objects
            }
        return {
            f"{o['object_type']}:{o['instance']}": {
                "presentValue": VALUES[f"{o['object_type']}:{o['instance']}"],
                "statusFlags": [False, False, False, False],
            }
            for o in objects
        }

    async def subscribe_cov(self, **kwargs):
        return None

    async def subscribe_cov_property(self, **kwargs):
        return None

    async def unsubscribe_cov(self, sub_key):
        return None

    async def unsubscribe_cov_property(self, sub_key):
        return None

    async def read_property(self, **kwargs):
        return None

    async def write_property(self, **kwargs):
        self.writes.append(kwargs)
        return FakeClient.write_ok

    async def relinquish(self, **kwargs):
        self.relinquishes.append(kwargs)
        return FakeClient.write_ok

    async def read_objects_metadata(self, device_address, objects):
        return {f"{o['object_type']}:{o['instance']}": dict(o) for o in objects}

    async def refresh_object_metadata(self, **kwargs):
        return None


@pytest.fixture(autouse=True)
def _fake_client(enable_custom_integrations):
    FakeClient.instances.clear()
    FakeClient.offline = False
    FakeClient.write_ok = True
    # The harness's own testing_config/custom_components is a regular
    # package that shadows this repo's namespace package — add our path.
    import custom_components

    repo = str(Path(__file__).parent.parent / "custom_components")
    if repo not in list(custom_components.__path__):
        custom_components.__path__.append(repo)
    import custom_components.bacnet.bacnet_client  # patch target must be importable

    with patch("custom_components.bacnet.bacnet_client.BACnetClient", FakeClient):
        yield


async def _setup(hass: HomeAssistant, options=OPTIONS) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, options=options, title="AHU 1")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity_id(hass, unique_suffix):
    return er.async_get(hass).async_get_entity_id(
        unique_suffix[0], DOMAIN, f"{DOMAIN}_1001_{unique_suffix[1]}"
    )


async def test_setup_creates_all_entity_types(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    sensor = hass.states.get(_entity_id(hass, ("sensor", "0_1")))
    assert float(sensor.state) == 21.46
    assert sensor.attributes["unit_of_measurement"] == "°C"

    assert hass.states.get(_entity_id(hass, ("switch", "4_2"))).state == "on"

    climate = hass.states.get(_entity_id(hass, ("climate", "2_3")))
    assert climate.attributes["current_temperature"] == 21.5
    assert climate.attributes["temperature"] == 20.0
    assert climate.attributes["min_temp"] == 15.0

    select = hass.states.get(_entity_id(hass, ("select", "19_4")))
    assert select.state == "Heat"
    assert select.attributes["options"] == ["Off", "Heat", "Cool"]

    last_poll = hass.states.get(_entity_id(hass, ("sensor", "last_successful_poll")))
    assert last_poll is not None and last_poll.state not in ("unknown", "unavailable")

    reg = er.async_get(hass)
    prio = reg.async_get(_entity_id(hass, ("select", "write_priority")))
    assert prio.disabled_by is not None
    assert prio.entity_category == "config"


async def test_stale_override_repair_flow(hass: HomeAssistant) -> None:
    from homeassistant.setup import async_setup_component

    try:  # older HA; newer HA loads repairs platforms lazily
        from homeassistant.components.repairs.issue_handler import (
            async_process_repairs_platforms,
        )
    except ImportError:
        async_process_repairs_platforms = None

    assert await async_setup_component(hass, "repairs", {})
    entry = await _setup(hass)
    issue_id = f"stale_domain_overrides_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    if async_process_repairs_platforms is not None:
        await async_process_repairs_platforms(hass)
    manager = hass.data["repairs"]["flow_manager"]
    flow = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
    assert flow["step_id"] == "confirm"
    result = await manager.async_configure(flow["flow_id"], {})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()

    assert "5:5" not in entry.options["domain_mapping"]
    assert entry.options["domain_mapping"]["2:3"] == "climate"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    # After the reload the BV is a binary_sensor again.
    assert _entity_id(hass, ("binary_sensor", "5_5")) is not None


async def test_services(hass: HomeAssistant) -> None:
    await _setup(hass)
    switch = _entity_id(hass, ("switch", "4_2"))
    await hass.services.async_call(
        DOMAIN, "relinquish", {"entity_id": switch, "priority": 8}, blocking=True
    )
    await hass.services.async_call(
        DOMAIN, "write_value", {"entity_id": switch, "value": 0}, blocking=True
    )
    client = FakeClient.instances[-1]
    assert client.relinquishes[-1]["priority"] == 8
    assert client.writes[-1]["value"] == 0
    assert client.writes[-1]["priority"] == 16

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "relinquish",
            {"entity_id": _entity_id(hass, ("select", "write_priority"))},
            blocking=True,
        )


async def test_rejected_write_raises(hass: HomeAssistant) -> None:
    await _setup(hass)
    FakeClient.write_ok = False
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": _entity_id(hass, ("switch", "4_2"))},
            blocking=True,
        )


async def test_select_option_writes_state(hass: HomeAssistant) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": _entity_id(hass, ("select", "19_4")), "option": "Cool"},
        blocking=True,
    )
    assert FakeClient.instances[-1].writes[-1]["value"] == 3


async def test_climate_off_relinquishes(hass: HomeAssistant) -> None:
    await _setup(hass)
    climate = _entity_id(hass, ("climate", "2_3"))
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": climate, "hvac_mode": "off"},
        blocking=True,
    )
    assert FakeClient.instances[-1].relinquishes
    assert hass.states.get(climate).state == "off"


async def test_diagnostics(hass: HomeAssistant) -> None:
    from custom_components.bacnet.diagnostics import async_get_config_entry_diagnostics

    entry = await _setup(hass)
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["entry"]["data"]["device_address"] == "**REDACTED**"
    assert result["coordinator"]["objects"] == len(OBJECTS)


async def test_options_flow_roundtrip(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "enable_cov": True,
            "cov_increment": 0.1,
            "polling_interval": 60,
            "use_description": False,
            "live_metadata_properties": [],
            "rescan_objects": False,
        },
    )
    assert result["step_id"] == "domain_mapping"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"customize_objects": ["2:3"]}
    )
    assert result["step_id"] == "customize_objects"
    fields = [str(k) for k in result["data_schema"].schema]
    assert any("temperature sensor" in f for f in fields)
    user_input = {}
    for key in result["data_schema"].schema:
        name = str(key)
        if name.endswith("— type"):
            user_input[name] = "climate"
        elif name.endswith("— COV"):
            user_input[name] = True
        elif name.endswith("— temperature sensor"):
            user_input[name] = "0:1"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.options["polling_interval"] == 60
    # 19:4 and 5:5 were not picked → reset to defaults.
    assert entry.options["domain_mapping"] == {"2:3": "climate"}
    assert entry.options["climate_temperature_sources"] == {"2:3": "0:1"}
    assert entry.state is ConfigEntryState.LOADED


async def test_polling_floor_in_options(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(vol.Invalid):  # vol.Range rejects before the step runs
        await hass.config_entries.options.async_configure(
            result["flow_id"], {"polling_interval": 5}
        )


async def test_reconfigure(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    from homeassistant.config_entries import SOURCE_RECONFIGURE

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "local_ip": "",
            "local_port": 47809,
            "device_address": "192.168.1.77",
            "use_bbmd": False,
            "bbmd_address": "",
            "bbmd_ttl": 900,
        },
    )
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data["device_address"] == "192.168.1.77"
    assert entry.data["local_port"] == 47809
    assert entry.state is ConfigEntryState.LOADED
    # The old client (port 47808) was released and closed.
    assert FakeClient.instances[0].disconnected
    assert list(hass.data[DOMAIN]["_port_clients"]) == [47809]


async def test_unload_releases_client(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert FakeClient.instances[-1].disconnected
    assert hass.data[DOMAIN]["_port_clients"] == {}


async def test_offline_at_startup_retries_without_leak(hass: HomeAssistant) -> None:
    FakeClient.offline = True
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, options=OPTIONS)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # First refresh with no data still succeeds (stale-tolerant), so the
    # entry loads but its object entities are not available.
    if entry.state is ConfigEntryState.LOADED:
        state = hass.states.get(_entity_id(hass, ("sensor", "0_1")))
        assert state.state in ("unknown", "unavailable")
    else:
        assert entry.state is ConfigEntryState.SETUP_RETRY
        assert FakeClient.instances[-1].disconnected
        assert hass.data[DOMAIN]["_port_clients"] == {}
