"""Extra properties as entities (Reddit feedback: minimumOnTime and friends).

Picked per object in the options flow, polled with the regular poll and
shown as their own number / switch / sensor entity, so nobody needs a
template sensor to see or change e.g. a fan's minimum run time.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from tests.test_coordinator import _make_coordinator

_BO = {"object_type": 4, "instance": 3, "object_name": "Fan", "commandable": True}
_AV = {
    "object_type": 2,
    "instance": 1,
    "object_name": "SP",
    "units": "degrees-celsius",
}


def _coordinator(extras, objects=(_BO, _AV)):
    coord = _make_coordinator(objects=list(objects))
    coord.extra_properties = extras
    coord.device_address = "192.168.1.100"
    coord._polled_objects = list(objects)  # skip first-run COV setup
    return coord


class TestPolling:
    def test_extras_polled_per_property_list_and_merged(self):
        coord = _coordinator({"4:3": ["minimumOnTime"], "2:1": ["highLimit"]})

        async def _poll(device_address, objects, property_names, device_id=None, **_):
            if property_names == ["presentValue", "statusFlags"]:
                return {
                    "4:3": {"presentValue": 1, "statusFlags": [False] * 4},
                    "2:1": {"presentValue": 21.0, "statusFlags": [False] * 4},
                }
            key = f"{objects[0]['object_type']}:{objects[0]['instance']}"
            return {key: {property_names[0]: 300 if key == "4:3" else 26.0}}

        coord.client.poll_objects = AsyncMock(side_effect=_poll)
        data = asyncio.run(coord._async_update_data())
        assert data["4:3"] == {
            "presentValue": 1,
            "statusFlags": [False] * 4,
            "minimumOnTime": 300,
        }
        assert data["2:1"]["highLimit"] == 26.0
        # one main poll + one per distinct property list
        assert coord.client.poll_objects.await_count == 3


def _entities(platform, extras, objects=(_BO, _AV)):
    from custom_components.bacnet.property_entity import property_entities
    from tests.conftest import _make_entry

    return property_entities(_coordinator(extras, objects), _make_entry(), platform)


def test_platform_per_property():
    extras = {"4:3": ["minimumOnTime", "outOfService", "eventState", "9999"]}
    assert [e.property_name for e in _entities("number", extras)] == ["minimumOnTime"]
    assert [e.property_name for e in _entities("switch", extras)] == ["outOfService"]
    # unknown/proprietary properties are read-only sensors
    assert [e.property_name for e in _entities("sensor", extras)] == [
        "eventState",
        "9999",
    ]


def test_unique_id_name_and_unit():
    (number,) = _entities("number", {"2:1": ["highLimit"]})
    assert number._attr_unique_id == "bacnet_1001_2_1_highLimit"
    assert number._attr_name == "SP High Limit"
    assert number._attr_native_unit_of_measurement == "°C"


def test_number_write_sends_property_and_refreshes_it():
    (number,) = _entities("number", {"4:3": ["minimumOnTime"]})
    coord = number.coordinator
    coord.client.write_property = AsyncMock(return_value=True)
    coord.client.poll_objects = AsyncMock(return_value={"4:3": {"minimumOnTime": 600}})
    coord.data = {"4:3": {"presentValue": 1, "minimumOnTime": 300}}
    asyncio.run(number.async_set_native_value(600))
    kwargs = coord.client.write_property.await_args.kwargs
    assert kwargs["property_name"] == "minimumOnTime"
    assert kwargs["value"] == 600
    assert coord.client.poll_objects.await_args.kwargs["property_names"] == [
        "minimumOnTime"
    ]
    assert coord.data["4:3"] == {"presentValue": 1, "minimumOnTime": 600}


def test_platforms_in_use_include_extra_property_platforms():
    from custom_components.bacnet.__init__ import _get_platforms_in_use

    platforms = _get_platforms_in_use(
        [{"object_type": 0, "instance": 1}],
        {},
        {"0:1": ["highLimit", "outOfService"], "0:9": ["minimumOnTime"]},
    )
    assert {p.value for p in platforms} == {"sensor", "number", "switch"}


def test_number_platform_adds_property_entities():
    from unittest.mock import MagicMock

    from custom_components.bacnet import number
    from tests.conftest import _make_entry

    entry = _make_entry()
    entry.runtime_data.coordinator = _coordinator({"4:3": ["minimumOnTime"]}, [_BO])
    added = []
    asyncio.run(number.async_setup_entry(MagicMock(), entry, added.extend))
    assert [getattr(e, "property_name", None) for e in added] == ["minimumOnTime"]


def test_failing_extra_poll_keeps_main_data():
    coord = _coordinator({"4:3": ["minimumOnTime"]}, [_BO])

    async def _poll(device_address, objects, property_names, device_id=None, **_):
        if property_names == ["presentValue", "statusFlags"]:
            return {"4:3": {"presentValue": 1, "statusFlags": [False] * 4}}
        raise TimeoutError

    coord.client.poll_objects = AsyncMock(side_effect=_poll)
    data = asyncio.run(coord._async_update_data())
    assert data["4:3"] == {"presentValue": 1, "statusFlags": [False] * 4}
    assert coord._consecutive_failures == 0


def test_offline_device_skips_extra_poll():
    """Review: an outage poll must not also wait out the extras' timeouts."""
    coord = _coordinator({"4:3": ["minimumOnTime"]}, [_BO])
    calls = []

    async def _poll(device_address, objects, property_names, device_id=None, **kw):
        calls.append(property_names)
        return {"4:3": {"presentValue": None, "statusFlags": None}}

    coord.client.poll_objects = AsyncMock(side_effect=_poll)
    coord.client.reconnect = AsyncMock()
    asyncio.run(coord._async_update_data())
    assert calls == [["presentValue", "statusFlags"]]


def test_extra_poll_does_not_teach_rpm_state():
    coord = _coordinator({"4:3": ["minimumOnTime"]}, [_BO])
    seen = []

    async def _poll(device_address, objects, property_names, device_id=None, **kw):
        seen.append((property_names, kw.get("extra_properties", False)))
        return {"4:3": {"presentValue": 1, "statusFlags": [False] * 4}}

    coord.client.poll_objects = AsyncMock(side_effect=_poll)
    asyncio.run(coord._async_update_data())
    assert seen == [
        (["presentValue", "statusFlags"], False),
        (["minimumOnTime"], True),
    ]


def test_known_property_on_other_object_type_is_a_sensor():
    """Review: highLimit typed for a Binary Output isn't a writable number."""
    assert _entities("number", {"4:3": ["highLimit"]}) == []
    assert [e.property_name for e in _entities("sensor", {"4:3": ["highLimit"]})] == [
        "highLimit"
    ]
