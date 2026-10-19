"""Trend Log objects can be imported (setup wizard, rescan).

They have no presentValue: the entity shows how many records the log
holds, and bacnet.read_trend_log can target it instead of a device plus
instance number.
"""

from __future__ import annotations

from custom_components.bacnet import bacnet_client as bc
from custom_components.bacnet.helpers import default_domain_for, object_label


def test_trend_log_is_a_supported_sensor_object():
    assert 20 in bc.SUPPORTED_OBJECT_TYPES
    assert default_domain_for({"object_type": 20, "instance": 4}) == "sensor"
    assert object_label(
        {"object_type": 20, "instance": 4, "object_name": "TL Supply"}
    ).startswith("Trend Log (4)")


def test_coordinator_polls_record_count_not_present_value():
    import asyncio
    from unittest.mock import AsyncMock

    from tests.test_coordinator import _make_coordinator

    ai = {"object_type": 0, "instance": 1}
    tl = {"object_type": 20, "instance": 4}
    coord = _make_coordinator(objects=[ai, tl])
    coord._polled_objects = [ai]
    calls = []

    async def _poll(device_address, objects, property_names, device_id=None, **kw):
        calls.append(([o["object_type"] for o in objects], property_names))
        if property_names == ["presentValue", "statusFlags"]:
            return {"0:1": {"presentValue": 21.0, "statusFlags": [False] * 4}}
        return {"20:4": {"recordCount": 1440, "logEnable": True}}

    coord.client.poll_objects = AsyncMock(side_effect=_poll)
    data = asyncio.run(coord._async_update_data())
    assert calls == [
        ([0], ["presentValue", "statusFlags"]),
        ([20], ["recordCount", "logEnable"]),
    ]
    assert data["20:4"] == {"recordCount": 1440, "logEnable": True}


def test_trend_log_sensor_shows_record_count():
    import asyncio
    from unittest.mock import MagicMock

    from custom_components.bacnet import sensor
    from tests.conftest import _make_coordinator, _make_entry

    tl = {"object_type": 20, "instance": 4, "object_name": "TL Supply"}
    coord = _make_coordinator({"20:4": {"recordCount": 1440, "logEnable": True}})
    coord.objects = [tl]
    coord.get_domain_for_object.return_value = "sensor"
    coord.extra_properties = {}
    entry = _make_entry()
    entry.runtime_data.coordinator = coord
    added = []
    asyncio.run(sensor.async_setup_entry(MagicMock(), entry, added.extend))
    (trend,) = [e for e in added if getattr(e, "_obj_key", None) == "20:4"]
    assert trend.native_value == 1440
    assert trend.extra_state_attributes["bacnet_log_enabled"] is True


def test_trend_log_ignores_type_override():
    from custom_components.bacnet.__init__ import _domain_for_object
    from tests.test_coordinator import _make_coordinator

    tl = {"object_type": 20, "instance": 4}
    coord = _make_coordinator(objects=[tl], domain_overrides={"20:4": "number"})
    assert coord.get_domain_for_object(tl) == "sensor"
    assert _domain_for_object(tl, {"20:4": "number"}) == "sensor"
