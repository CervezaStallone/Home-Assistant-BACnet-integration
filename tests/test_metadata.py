"""Tests for batched metadata reads (discovery + periodic sweep).

Metadata used to be 5 sequential ReadProperty calls per object. It is
now read with ReadPropertyMultiple in chunks, falling back to parallel
individual reads, and includes stateText / min / max / resolution.
"""

from __future__ import annotations

import asyncio

from bacpypes3.apdu import RejectPDU

from custom_components.bacnet.bacnet_client import BACnetClient

_ADDR = "192.168.1.60"

_DEVICE = {
    ("analog-input", 1): {
        "object-name": "Room Temp",
        "description": "Zone 1",
        "present-value": 21.5,
        "units": "degrees-celsius",
        "min-pres-value": -20.0,
        "max-pres-value": 60.0,
        "resolution": 0.1,
    },
    ("multi-state-value", 2): {
        "object-name": "Mode",
        "description": "",
        "present-value": 2,
        "state-text": ["Off", "Heat", "Cool"],
        "number-of-states": 3,
        "priority-array": [None] * 16,
    },
    ("analog-value", 3): {
        "object-name": "Setpoint",
        "description": "",
        "present-value": 20.0,
        "units": "degrees-celsius",
    },
}

_OBJECTS = [
    {"object_type": 0, "instance": 1},
    {"object_type": 19, "instance": 2},
    {"object_type": 2, "instance": 3},
]


class _FakeDevice:
    """Answers RPM and ReadProperty from _DEVICE; missing props are errors."""

    def __init__(self, rpm=True):
        self.rpm = rpm
        self.rpm_calls = 0
        self.reads = 0

    async def read_property_multiple(self, addr, param_list):
        self.rpm_calls += 1
        if not self.rpm:
            raise RejectPDU(reason="unrecognized-service")
        results = []
        for oid_str, props in zip(param_list[::2], param_list[1::2], strict=True):
            type_str, inst = oid_str.split(",")
            values = _DEVICE[(type_str, int(inst))]
            for prop in props:
                value = values.get(prop, RejectPDU(reason="unrecognized-service"))
                results.append(((type_str, int(inst)), prop, None, value))
        return results

    async def read_property(self, addr, oid, prop_name, array_index=None):
        self.reads += 1
        hyphen = BACnetClient._CAMEL_TO_HYPHEN.get(prop_name, prop_name)
        return _DEVICE[(str(oid[0]), int(oid[1]))].get(hyphen)


def _read(app, objects=_OBJECTS):
    client = BACnetClient(local_ip="127.0.0.1", local_port=47830)
    client._app = app
    return client, asyncio.run(client.read_objects_metadata(_ADDR, objects))


class TestBatchedMetadata:
    def test_rpm_reads_everything_without_individual_reads(self):
        app = _FakeDevice(rpm=True)
        _, result = _read(app)
        assert app.reads == 0
        assert app.rpm_calls >= 1
        ai = result["0:1"]
        assert ai["object_name"] == "Room Temp"
        assert ai["units"] == "degrees-celsius"
        assert (ai["min_value"], ai["max_value"], ai["resolution"]) == (
            -20.0,
            60.0,
            0.1,
        )
        assert result["19:2"]["state_text"] == ["Off", "Heat", "Cool"]
        assert result["19:2"]["number_of_states"] == 3

    def test_commandable_from_priority_array(self):
        _, result = _read(_FakeDevice(rpm=True))
        assert result["19:2"]["commandable"] is True
        assert result["2:3"]["commandable"] is False

    def test_individual_fallback_gives_same_result(self):
        _, via_rpm = _read(_FakeDevice(rpm=True))
        app = _FakeDevice(rpm=False)
        _, via_reads = _read(app)
        assert app.reads > 0
        assert via_reads == via_rpm

    def test_missing_property_keeps_known_value(self):
        """Issue #27: an unreadable property must not look like a change."""
        known = {
            "object_type": 2,
            "instance": 3,
            "object_name": "Setpoint",
            "min_value": 5.0,
            "commandable": True,
        }
        _, result = _read(_FakeDevice(rpm=True), [known])
        assert result["2:3"]["min_value"] == 5.0
        assert result["2:3"]["commandable"] is True

    def test_binary_objects_get_no_analog_or_multistate_keys(self):
        _DEVICE[("binary-input", 4)] = {"object-name": "Door", "present-value": 0}
        try:
            _, result = _read(
                _FakeDevice(rpm=True), [{"object_type": 3, "instance": 4}]
            )
        finally:
            del _DEVICE[("binary-input", 4)]
        assert "state_text" not in result["3:4"]
        assert "min_value" not in result["3:4"]
