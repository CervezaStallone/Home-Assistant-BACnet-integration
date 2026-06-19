"""
Tests for BACnetClient pure-Python logic.

Network I/O is NOT tested here — those require a real or simulated BACnet
device. This file covers deterministic static/class methods that can be
verified without any network.
"""

from __future__ import annotations

import pytest

from custom_components.bacnet.bacnet_client import BACnetClient

# ---------------------------------------------------------------------------
# Attempt to import BACpypes3 primitives for _coerce_value tests.
# If not installed the coerce-value suite is skipped gracefully.
# ---------------------------------------------------------------------------
try:
    from bacpypes3.primitivedata import (
        CharacterString,
        Enumerated,
        Real,
        Unsigned,
    )

    _BACPYPES3_AVAILABLE = True
except ImportError:
    _BACPYPES3_AVAILABLE = False

bacpypes3_required = pytest.mark.skipif(
    not _BACPYPES3_AVAILABLE, reason="bacpypes3 not installed"
)


# ---------------------------------------------------------------------------
# _coerce_value
# ---------------------------------------------------------------------------


class TestCoerceValue:
    def test_none(self):
        assert BACnetClient._coerce_value(None) is None

    def test_plain_int_roundtrips(self):
        result = BACnetClient._coerce_value(42)
        assert result == 42
        assert type(result) is int

    def test_plain_float_roundtrips(self):
        result = BACnetClient._coerce_value(3.14)
        assert abs(result - 3.14) < 1e-9
        assert type(result) is float

    def test_plain_bool_preserved(self):
        assert BACnetClient._coerce_value(True) is True
        assert BACnetClient._coerce_value(False) is False

    def test_plain_str_roundtrips(self):
        result = BACnetClient._coerce_value("hello")
        assert result == "hello"
        assert type(result) is str

    def test_list_converted_to_bool_list(self):
        # StatusFlags-style: [False, True, False, False]
        result = BACnetClient._coerce_value([False, True, False, False])
        assert result == [False, True, False, False]
        assert all(type(x) is bool for x in result)

    def test_list_of_ints_converted_to_bools(self):
        result = BACnetClient._coerce_value([0, 1, 0, 0])
        assert result == [False, True, False, False]

    @bacpypes3_required
    def test_real_returns_float(self):
        result = BACnetClient._coerce_value(Real(23.5))
        assert result == pytest.approx(23.5)
        assert type(result) is float

    @bacpypes3_required
    def test_unsigned_returns_int(self):
        result = BACnetClient._coerce_value(Unsigned(7))
        assert result == 7
        assert type(result) is int

    @bacpypes3_required
    def test_character_string_returns_str(self):
        result = BACnetClient._coerce_value(CharacterString("Zone 1"))
        assert result == "Zone 1"
        assert type(result) is str

    @bacpypes3_required
    def test_enumerated_returns_plain_int(self):
        """Enumerated is an int subclass; must return a plain Python int."""
        result = BACnetClient._coerce_value(Enumerated(1))
        assert result == 1
        assert type(result) is int  # NOT Enumerated

    @bacpypes3_required
    def test_enumerated_zero_is_false_when_bool(self):
        result = BACnetClient._coerce_value(Enumerated(0))
        assert result == 0
        assert type(result) is int

    def test_fallback_converts_to_str(self):
        class Weird:
            def __str__(self):
                return "weird-value"

        result = BACnetClient._coerce_value(Weird())
        assert result == "weird-value"
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _derive_device_instance
# ---------------------------------------------------------------------------


class TestDeriveDeviceInstance:
    def test_deterministic(self):
        a = BACnetClient._derive_device_instance("192.168.1.1", 47808)
        b = BACnetClient._derive_device_instance("192.168.1.1", 47808)
        assert a == b

    def test_different_ip_gives_different_instance(self):
        a = BACnetClient._derive_device_instance("192.168.1.1", 47808)
        b = BACnetClient._derive_device_instance("192.168.1.2", 47808)
        assert a != b

    def test_different_port_gives_different_instance(self):
        a = BACnetClient._derive_device_instance("192.168.1.1", 47808)
        b = BACnetClient._derive_device_instance("192.168.1.1", 47809)
        assert a != b

    def test_result_in_valid_range(self):
        instance = BACnetClient._derive_device_instance("10.0.0.1", 47808)
        assert 3_900_000 <= instance <= 4_194_302

    def test_empty_ip(self):
        instance = BACnetClient._derive_device_instance("", 47808)
        assert 3_900_000 <= instance <= 4_194_302


# ---------------------------------------------------------------------------
# _object_type_str_to_int / _INT_TO_TYPE_STR
# ---------------------------------------------------------------------------


class TestObjectTypeMapping:
    @pytest.mark.parametrize(
        "name, expected",
        [
            ("analog-input", 0),
            ("analog-output", 1),
            ("analog-value", 2),
            ("binary-input", 3),
            ("binary-output", 4),
            ("binary-value", 5),
            ("multi-state-input", 13),
            ("multi-state-output", 14),
            ("multi-state-value", 19),
            # camelCase aliases
            ("analogInput", 0),
            ("binaryValue", 5),
            ("multiStateOutput", 14),
        ],
    )
    def test_str_to_int(self, name, expected):
        assert BACnetClient._object_type_str_to_int(name) == expected

    def test_int_passthrough(self):
        assert BACnetClient._object_type_str_to_int(2) == 2

    def test_unknown_returns_none(self):
        assert BACnetClient._object_type_str_to_int("unknown-type") is None

    @pytest.mark.parametrize(
        "type_int, expected_str",
        [
            (0, "analog-input"),
            (1, "analog-output"),
            (4, "binary-output"),
            (5, "binary-value"),
            (19, "multi-state-value"),
        ],
    )
    def test_int_to_str(self, type_int, expected_str):
        assert BACnetClient._INT_TO_TYPE_STR[type_int] == expected_str


# ---------------------------------------------------------------------------
# _CAMEL_TO_HYPHEN / _HYPHEN_TO_CAMEL
# ---------------------------------------------------------------------------


class TestPropertyNameMaps:
    def test_camel_to_hyphen(self):
        assert BACnetClient._CAMEL_TO_HYPHEN["presentValue"] == "present-value"
        assert BACnetClient._CAMEL_TO_HYPHEN["statusFlags"] == "status-flags"

    def test_hyphen_to_camel_is_inverse(self):
        for camel, hyphen in BACnetClient._CAMEL_TO_HYPHEN.items():
            assert BACnetClient._HYPHEN_TO_CAMEL[hyphen] == camel


# ---------------------------------------------------------------------------
# reconnect() — issue #18 outage recovery
# ---------------------------------------------------------------------------


class TestReconnect:
    """reconnect() must re-issue connect() with the previously used BBMD params."""

    def test_reconnect_uses_stored_bbmd_params(self, monkeypatch):
        client = BACnetClient(local_ip="127.0.0.1", local_port=47809)

        # Simulate a prior successful connect() with BBMD parameters.
        client._last_bbmd_address = "10.0.0.1:47808"
        client._last_bbmd_ttl = 600

        calls: list[tuple] = []

        async def fake_disconnect(self):
            calls.append(("disconnect",))

        async def fake_connect(self, bbmd_address=None, bbmd_ttl=900):
            calls.append(("connect", bbmd_address, bbmd_ttl))

        # Bind as plain functions taking self (unbound-method style).
        monkeypatch.setattr(BACnetClient, "disconnect", fake_disconnect)
        monkeypatch.setattr(BACnetClient, "connect", fake_connect)

        import asyncio

        asyncio.run(client.reconnect())

        assert calls[0] == ("disconnect",)
        assert calls[1] == ("connect", "10.0.0.1:47808", 600)

    def test_reconnect_defaults_when_never_connected(self, monkeypatch):
        """A fresh client that never called connect() reconnects without BBMD."""
        client = BACnetClient(local_ip="127.0.0.1", local_port=47810)

        assert client._last_bbmd_address is None
        assert client._last_bbmd_ttl == 900

        calls: list[tuple] = []

        async def fake_disconnect(self):
            calls.append(("disconnect",))

        async def fake_connect(self, bbmd_address=None, bbmd_ttl=900):
            calls.append(("connect", bbmd_address, bbmd_ttl))

        monkeypatch.setattr(BACnetClient, "disconnect", fake_disconnect)
        monkeypatch.setattr(BACnetClient, "connect", fake_connect)

        import asyncio

        asyncio.run(client.reconnect())

        assert calls[0] == ("disconnect",)
        assert calls[1] == ("connect", None, 900)
