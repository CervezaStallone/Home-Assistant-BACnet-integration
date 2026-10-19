"""Alarm status (statusFlags in-alarm) and active alarms (GetEventInformation).

Reddit feedback: controllers already track their alarms; show them in Home
Assistant instead of dropping them. Read-only — no acknowledging.
"""

from __future__ import annotations

import asyncio

from bacpypes3.apdu import GetEventInformationACK, RejectPDU
from bacpypes3.basetypes import (
    EventState,
    EventTransitionBits,
    GetEventInformationEventSummary,
    NotifyType,
)
from bacpypes3.primitivedata import ObjectIdentifier

from custom_components.bacnet.bacnet_client import BACnetClient

_ADDR = "192.168.1.70"


def _summary(oid, state, acked=(True, True, True)):
    return GetEventInformationEventSummary(
        objectIdentifier=ObjectIdentifier(oid),
        eventState=EventState(state),
        acknowledgedTransitions=EventTransitionBits(list(acked)),
        eventTimeStamps=[],
        notifyType=NotifyType("alarm"),
        eventEnable=EventTransitionBits([True, True, True]),
        eventPriorities=[],
    )


class _App:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def request(self, apdu):
        self.requests.append(apdu)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _client(app):
    client = BACnetClient(local_ip="127.0.0.1", local_port=47840)
    client._app = app
    return client


def test_event_information_follows_more_events():
    app = _App(
        [
            GetEventInformationACK(
                listOfEventSummaries=[_summary("analog-input,1", "high-limit")],
                moreEvents=True,
            ),
            GetEventInformationACK(
                listOfEventSummaries=[
                    _summary("binary-input,2", "offnormal", (False, True, True))
                ],
                moreEvents=False,
            ),
        ]
    )
    alarms = asyncio.run(_client(app).get_event_information(_ADDR))
    assert alarms == [
        {
            "object_type": 0,
            "instance": 1,
            "event_state": "high-limit",
            "acknowledged": {"to_offnormal": True, "to_fault": True, "to_normal": True},
        },
        {
            "object_type": 3,
            "instance": 2,
            "event_state": "offnormal",
            "acknowledged": {
                "to_offnormal": False,
                "to_fault": True,
                "to_normal": True,
            },
        },
    ]
    assert str(app.requests[1].lastReceivedObjectIdentifier) == "analog-input,1"


def test_unsupported_service_is_not_asked_again():
    app = _App([RejectPDU(reason="unrecognized-service")])
    client = _client(app)
    assert asyncio.run(client.get_event_information(_ADDR)) is None
    assert asyncio.run(client.get_event_information(_ADDR)) is None
    assert len(app.requests) == 1


def _alarm(object_type, instance, state):
    return {
        "object_type": object_type,
        "instance": instance,
        "event_state": state,
        "acknowledged": {"to_offnormal": True, "to_fault": True, "to_normal": True},
    }


def test_coordinator_tracks_alarms_and_fires_events():
    from unittest.mock import AsyncMock

    from tests.test_coordinator import _make_coordinator

    room = {"object_type": 0, "instance": 1, "object_name": "Room"}
    coord = _make_coordinator(objects=[room])
    coord._polled_objects = [room]
    coord.entry.data = {"device_address": _ADDR, "device_id": 1001}
    coord.client.poll_objects = AsyncMock(
        return_value={
            "0:1": {"presentValue": 30.0, "statusFlags": [True] + [False] * 3}
        }
    )
    coord.client.get_event_information = AsyncMock(
        side_effect=[
            [_alarm(0, 1, "high-limit"), _alarm(3, 7, "offnormal")],
            [_alarm(0, 1, "high-limit")],
        ]
    )
    asyncio.run(coord._async_update_data())
    assert [a["object_name"] for a in coord.active_alarms] == ["Room", "Binary Input 7"]
    asyncio.run(coord._async_update_data())

    fired = [c.args for c in coord.hass.bus.async_fire.call_args_list]
    # The first fetch is the baseline: a restart doesn't re-announce
    # alarms that were already active.
    assert [
        (name, d["instance"], d["event_state"], d["previous_event_state"])
        for name, d in fired
    ] == [("bacnet_alarm", 7, "normal", "offnormal")]
    assert fired[0][1]["device_id"] == 1001


def test_in_alarm_attribute_from_status_flags():
    from custom_components.bacnet.sensor import BACnetSensor
    from tests.conftest import _make_coordinator, _make_entry

    obj = {"object_type": 0, "instance": 1, "object_name": "Room"}
    coord = _make_coordinator(
        {"0:1": {"presentValue": 30.0, "statusFlags": [True, False, False, False]}}
    )
    sensor = BACnetSensor(coord, _make_entry(), obj)
    assert sensor.extra_state_attributes["bacnet_in_alarm"] is True
    coord.data["0:1"]["statusFlags"] = [False] * 4
    assert sensor.extra_state_attributes["bacnet_in_alarm"] is False


def test_active_alarms_sensor():
    from unittest.mock import MagicMock

    from custom_components.bacnet.sensor import BACnetActiveAlarmsSensor
    from tests.conftest import _make_entry

    coord = MagicMock(
        active_alarms=[{**_alarm(0, 1, "high-limit"), "object_name": "Room"}]
    )
    sensor = BACnetActiveAlarmsSensor(coord, _make_entry())
    assert sensor._attr_unique_id == "bacnet_1001_active_alarms"
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["alarms"][0]["object_name"] == "Room"
    coord.active_alarms = None  # device without GetEventInformation
    assert sensor.native_value is None


def test_device_alarm_binary_sensor():
    from custom_components.bacnet.binary_sensor import BACnetDeviceAlarmSensor
    from tests.conftest import _make_coordinator, _make_entry

    coord = _make_coordinator({"0:1": {"statusFlags": [False] * 4}})
    coord.active_alarms = None
    sensor = BACnetDeviceAlarmSensor(coord, _make_entry())
    assert sensor._attr_unique_id == "bacnet_1001_alarm"
    assert sensor.is_on is False
    coord.data["0:1"]["statusFlags"] = [True, False, False, False]
    assert sensor.is_on is True
    coord.data["0:1"]["statusFlags"] = [False] * 4
    coord.active_alarms = [_alarm(3, 7, "offnormal")]
    assert sensor.is_on is True


def test_event_information_stops_when_device_repeats_itself():
    """A device that keeps saying moreEvents with the same page must not loop."""
    page = GetEventInformationACK(
        listOfEventSummaries=[_summary("analog-input,1", "high-limit")],
        moreEvents=True,
    )
    app = _App([page] * 100)
    alarms = asyncio.run(_client(app).get_event_information(_ADDR))
    assert len(app.requests) == 2
    assert [a["instance"] for a in alarms] == [1]


def test_silent_device_is_given_up_after_three_timeouts():
    """Review: a device that drops GetEventInformation must not cost ~10 s
    on every poll forever."""
    import pytest

    from custom_components.bacnet.bacnet_client import BACnetPropertyError

    app = _App([TimeoutError()] * 5)
    client = _client(app)
    for _ in range(3):
        with pytest.raises(BACnetPropertyError):
            asyncio.run(client.get_event_information(_ADDR))
    assert asyncio.run(client.get_event_information(_ADDR)) is None
    assert len(app.requests) == 3


def test_normal_entries_waiting_for_acknowledgement_are_not_alarms():
    """GetEventInformation also lists objects back to normal with an
    unacknowledged transition (135 clause 13.12)."""
    from custom_components.bacnet.binary_sensor import BACnetDeviceAlarmSensor
    from custom_components.bacnet.sensor import BACnetActiveAlarmsSensor
    from tests.conftest import _make_coordinator, _make_entry

    coord = _make_coordinator({"0:1": {"statusFlags": [False] * 4}})
    coord.active_alarms = [{**_alarm(0, 1, "normal"), "object_name": "Room"}]
    assert BACnetDeviceAlarmSensor(coord, _make_entry()).is_on is False
    sensor = BACnetActiveAlarmsSensor(coord, _make_entry())
    assert sensor.native_value == 0
    assert len(sensor.extra_state_attributes["alarms"]) == 1


def test_device_alarm_sensor_follows_cov_of_every_object():
    """A COV statusFlags change only notifies that object's listeners."""
    from custom_components.bacnet.binary_sensor import BACnetDeviceAlarmSensor

    from tests.test_coordinator import _make_coordinator

    objects = [{"object_type": 0, "instance": 1}, {"object_type": 3, "instance": 2}]
    coord = _make_coordinator(objects=objects)
    sensor = BACnetDeviceAlarmSensor(coord, _make_entry_for_alarm())
    sensor.async_on_remove = lambda remove: None
    sensor.async_write_ha_state = lambda: None
    asyncio.run(sensor.async_added_to_hass())
    assert set(coord._object_listeners) == {"0:1", "3:2"}


def _make_entry_for_alarm():
    from tests.conftest import _make_entry

    return _make_entry()


def test_alarms_attribute_is_capped():
    """The recorder refuses attributes over 16 KB; the count stays exact."""
    from unittest.mock import MagicMock

    from custom_components.bacnet.sensor import BACnetActiveAlarmsSensor

    from tests.conftest import _make_entry

    alarms = [{**_alarm(0, i, "high-limit"), "object_name": f"AI {i}"} for i in range(80)]
    sensor = BACnetActiveAlarmsSensor(MagicMock(active_alarms=alarms), _make_entry())
    attrs = sensor.extra_state_attributes
    assert sensor.native_value == 80
    assert len(attrs["alarms"]) == 50
    assert attrs["alarms_truncated"] is True
