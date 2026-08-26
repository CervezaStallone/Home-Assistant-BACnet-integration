"""
Tests for BACnetClient pure-Python logic.

Network I/O is NOT tested here — those require a real or simulated BACnet
device. This file covers deterministic static/class methods that can be
verified without any network.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from custom_components.bacnet.bacnet_client import BACnetClient
from custom_components.bacnet.const import (
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
    OBJECT_TYPE_BINARY_VALUE,
)


class _FakeReadApp:
    """Fake BACpypes3 app that answers read_property from a canned value map."""

    def __init__(self, values: dict) -> None:
        self._values = values

    async def read_property(self, addr, oid, prop_name, array_index=None):
        return self._values.get(prop_name)


class _FakeCovApp:
    """Fake BACpypes3 app for exercising COV-Property subscribe/cancel.

    Mirrors just enough of ChangeOfValueServices' private surface
    (_cov_next_id, _cov_contexts) for _PropertyCOVSubscriptionContextManager
    to work, plus a scriptable async request() that records every APDU sent.
    """

    def __init__(self, request_result=None):
        self._cov_next_id = 1
        self._cov_contexts = {}
        self.requests = []
        self._request_result = request_result

    async def request(self, apdu):
        self.requests.append(apdu)
        if isinstance(self._request_result, Exception):
            raise self._request_result
        if self._request_result is not None:
            return self._request_result
        from bacpypes3.apdu import SimpleAckPDU

        return SimpleAckPDU()


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
# write_property() priority gating — issue #24
# ---------------------------------------------------------------------------


class _FakeApp:
    """Records write_property calls so tests can assert on the priority kwarg."""

    def __init__(self):
        self.calls: list[dict] = []

    async def write_property(self, addr, oid, property_name, value, **kwargs):
        self.calls.append({"property_name": property_name, **kwargs})


class TestWritePropertyPriorityGating:
    """priority must only be sent for presentValue writes to a commandable object.

    Sending priority to a non-commandable object, or to any property other than
    presentValue (e.g. covIncrement), is rejected by many real devices — the
    object's type alone doesn't tell you whether *this* instance is commandable.
    """

    def _client(self):
        client = BACnetClient(local_ip="127.0.0.1", local_port=47811)
        client._app = _FakeApp()
        return client

    def test_commandable_presentvalue_sends_priority(self):
        client = self._client()
        asyncio.run(
            client.write_property(
                device_address="192.168.1.1",
                object_type=OBJECT_TYPE_BINARY_VALUE,
                instance=1,
                property_name="presentValue",
                value=1,
                priority=8,
                commandable=True,
            )
        )
        assert client._app.calls[0]["priority"] == 8

    def test_non_commandable_presentvalue_omits_priority(self):
        client = self._client()
        asyncio.run(
            client.write_property(
                device_address="192.168.1.1",
                object_type=OBJECT_TYPE_BINARY_VALUE,
                instance=1,
                property_name="presentValue",
                value=1,
                priority=8,
                commandable=False,
            )
        )
        assert "priority" not in client._app.calls[0]

    def test_non_presentvalue_omits_priority_even_if_commandable(self):
        """covIncrement writes must never carry priority (coordinator.py caller)."""
        client = self._client()
        asyncio.run(
            client.write_property(
                device_address="192.168.1.1",
                object_type=OBJECT_TYPE_ANALOG_VALUE,
                instance=1,
                property_name="covIncrement",
                value=0.5,
                commandable=True,
            )
        )
        assert "priority" not in client._app.calls[0]

    def test_default_commandable_is_false(self):
        """Callers that don't pass commandable get the safe (no-priority) default."""
        client = self._client()
        asyncio.run(
            client.write_property(
                device_address="192.168.1.1",
                object_type=OBJECT_TYPE_ANALOG_OUTPUT,
                instance=1,
                property_name="presentValue",
                value=1.0,
            )
        )
        assert "priority" not in client._app.calls[0]

    def test_relinquish_defaults_commandable_true(self):
        client = self._client()
        asyncio.run(
            client.relinquish(
                device_address="192.168.1.1",
                object_type=OBJECT_TYPE_ANALOG_OUTPUT,
                instance=1,
                priority=8,
            )
        )
        assert client._app.calls[0]["priority"] == 8

    def test_relinquish_non_commandable_omits_priority(self):
        client = self._client()
        asyncio.run(
            client.relinquish(
                device_address="192.168.1.1",
                object_type=OBJECT_TYPE_ANALOG_VALUE,
                instance=1,
                priority=8,
                commandable=False,
            )
        )
        assert "priority" not in client._app.calls[0]


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

        async def fake_disconnect(self, *, graceful=True):
            calls.append(("disconnect", graceful))

        async def fake_connect(self, bbmd_address=None, bbmd_ttl=900):
            calls.append(("connect", bbmd_address, bbmd_ttl))

        # Bind as plain functions taking self (unbound-method style).
        monkeypatch.setattr(BACnetClient, "disconnect", fake_disconnect)
        monkeypatch.setattr(BACnetClient, "connect", fake_connect)

        import asyncio

        asyncio.run(client.reconnect())

        # graceful=False: reconnect() is called after a network outage, so
        # it must not wait ~10s per COV subscription trying to notify a
        # device that may be unreachable (issue #29).
        assert calls[0] == ("disconnect", False)
        assert calls[1] == ("connect", "10.0.0.1:47808", 600)

    def test_reconnect_defaults_when_never_connected(self, monkeypatch):
        """A fresh client that never called connect() reconnects without BBMD."""
        client = BACnetClient(local_ip="127.0.0.1", local_port=47810)

        assert client._last_bbmd_address is None
        assert client._last_bbmd_ttl == 900

        calls: list[tuple] = []

        async def fake_disconnect(self, *, graceful=True):
            calls.append(("disconnect", graceful))

        async def fake_connect(self, bbmd_address=None, bbmd_ttl=900):
            calls.append(("connect", bbmd_address, bbmd_ttl))

        monkeypatch.setattr(BACnetClient, "disconnect", fake_disconnect)
        monkeypatch.setattr(BACnetClient, "connect", fake_connect)

        import asyncio

        asyncio.run(client.reconnect())

        assert calls[0] == ("disconnect", False)
        assert calls[1] == ("connect", None, 900)


# ---------------------------------------------------------------------------
# disconnect() COV teardown — issue #29
# ---------------------------------------------------------------------------


class TestDisconnectCovTeardown:
    """disconnect() had its own inline `task.cancel()` loop for COV cleanup,
    bypassing the graceful unsubscribe_all_cov()/unsubscribe_all_cov_property()
    path entirely — the same abandoned-subscription bug as issue #29, just on
    the full-client-teardown path (ref_count-triggered unload, HA shutdown)
    instead of the per-entry coordinator path."""

    def test_graceful_disconnect_uses_unsubscribe_all(self, monkeypatch):
        client = BACnetClient(local_ip="127.0.0.1", local_port=47818)
        calls: list[str] = []

        async def fake_unsubscribe_all_cov(self):
            calls.append("cov")

        async def fake_unsubscribe_all_cov_property(self):
            calls.append("cov_property")

        monkeypatch.setattr(
            BACnetClient, "unsubscribe_all_cov", fake_unsubscribe_all_cov
        )
        monkeypatch.setattr(
            BACnetClient,
            "unsubscribe_all_cov_property",
            fake_unsubscribe_all_cov_property,
        )

        asyncio.run(client.disconnect())  # graceful=True is the default

        assert calls == ["cov", "cov_property"]

    def test_non_graceful_disconnect_cancels_tasks_directly(self):
        client = BACnetClient(local_ip="127.0.0.1", local_port=47819)

        async def never_resolves():
            await asyncio.sleep(10)

        async def run():
            task = asyncio.create_task(never_resolves())
            stop_event = asyncio.Event()
            client._cov_tasks["sub-1"] = task
            client._cov_stop_events["sub-1"] = stop_event

            await client.disconnect(graceful=False)
            # task.cancel() only schedules cancellation; let it land.
            with contextlib.suppress(asyncio.CancelledError):
                await task

            assert task.cancelled()
            assert client._cov_tasks == {}
            assert client._cov_stop_events == {}

        asyncio.run(run())


# ---------------------------------------------------------------------------
# refresh_object_metadata() — issue #26 (stale metadata after discovery)
# ---------------------------------------------------------------------------


class TestRefreshObjectMetadata:
    def _client(self):
        return BACnetClient(local_ip="127.0.0.1", local_port=47813)

    def test_returns_fresh_values(self):
        client = self._client()
        client._app = _FakeReadApp(
            {
                "objectName": "Room Temp",
                "description": "Updated on device",
                "units": "degrees-celsius",
                "presentValue": 21.5,
            }
        )
        result = asyncio.run(
            client.refresh_object_metadata(
                device_address="192.168.1.1",
                object_type=OBJECT_TYPE_ANALOG_OUTPUT,
                instance=3,
            )
        )
        assert result["object_name"] == "Room Temp"
        assert result["description"] == "Updated on device"
        assert result["units"] == "degrees-celsius"
        # AO is inherently commandable regardless of priorityArray probing.
        assert result["commandable"] is True

    def test_raises_when_not_connected(self):
        client = self._client()
        with pytest.raises(RuntimeError):
            asyncio.run(
                client.refresh_object_metadata(
                    device_address="192.168.1.1",
                    object_type=OBJECT_TYPE_ANALOG_OUTPUT,
                    instance=1,
                )
            )

    def test_units_none_when_device_has_no_units(self):
        client = self._client()
        client._app = _FakeReadApp(
            {"objectName": "Binary Value", "description": "", "units": None}
        )
        result = asyncio.run(
            client.refresh_object_metadata(
                device_address="192.168.1.1",
                object_type=OBJECT_TYPE_BINARY_VALUE,
                instance=1,
            )
        )
        assert result["units"] is None


# ---------------------------------------------------------------------------
# SubscribeCOVProperty — issue #26 (live metadata push)
# ---------------------------------------------------------------------------


class TestCoerceCovPropertyValue:
    def test_units_returns_hyphenated_string_not_int(self):
        """EngineeringUnits is an int subclass — must NOT go through
        _coerce_value(), which would return a bare integer."""

        class _FakeUnits(int):
            def __str__(self):
                return "degrees-celsius"

        result = BACnetClient._coerce_cov_property_value("units", _FakeUnits(62))
        assert result == "degrees-celsius"
        assert isinstance(result, str)

    def test_description_uses_generic_coercion(self):
        result = BACnetClient._coerce_cov_property_value("description", "Zone 1 desc")
        assert result == "Zone 1 desc"

    def test_none_passthrough(self):
        assert BACnetClient._coerce_cov_property_value("units", None) is None


class TestSubscribeCovProperty:
    def _client(self):
        return BACnetClient(local_ip="127.0.0.1", local_port=47814)

    def test_successful_subscription_sends_subscribe_request(self):
        client = self._client()
        client._app = _FakeCovApp()
        from bacpypes3.apdu import SubscribeCOVPropertyRequest

        # subscribe + unsubscribe run in one asyncio.run() call (one event
        # loop) so the background reader task stays alive between them —
        # asyncio.run() force-cancels any task still running when its own
        # loop tears down, which would end the reader task before
        # unsubscribe_cov_property() ever got to signal it gracefully.
        async def run():
            sub_key = await client.subscribe_cov_property(
                device_address="192.168.1.50",
                object_type=0,  # analog-input
                instance=1,
                property_name="units",
                callback=lambda *a: None,
                lifetime=300,
            )

            assert sub_key is not None
            assert len(client._app.requests) == 1
            sent = client._app.requests[0]
            assert isinstance(sent, SubscribeCOVPropertyRequest)
            assert str(sent.monitoredPropertyIdentifier.propertyIdentifier) == "units"
            assert sub_key in client._cov_property_tasks

            await client.unsubscribe_cov_property(sub_key)
            assert sub_key not in client._cov_property_tasks
            # Regression check for issue #29: unsubscribe_cov_property()
            # signals the reader task's stop_event so it exits
            # `async with scm:` without an exception, letting __aexit__
            # send the device-side cancel APDU instead of abandoning the
            # subscription (task.cancel() alone would raise
            # CancelledError inside that block and skip the cancel send).
            assert len(client._app.requests) == 2
            sent_cancel = client._app.requests[1]
            assert isinstance(sent_cancel, SubscribeCOVPropertyRequest)

        asyncio.run(run())

    def test_rejected_subscription_returns_none(self):
        from bacpypes3.apdu import ErrorRejectAbortNack

        client = self._client()
        client._app = _FakeCovApp(request_result=ErrorRejectAbortNack())

        sub_key = asyncio.run(
            client.subscribe_cov_property(
                device_address="192.168.1.50",
                object_type=5,  # binary-value
                instance=2,
                property_name="description",
                callback=lambda *a: None,
                lifetime=300,
            )
        )

        assert sub_key is None
        assert client._cov_property_tasks == {}

    def test_raises_when_not_connected(self):
        client = self._client()
        with pytest.raises(RuntimeError):
            asyncio.run(
                client.subscribe_cov_property(
                    device_address="192.168.1.50",
                    object_type=0,
                    instance=1,
                    property_name="units",
                    callback=lambda *a: None,
                )
            )

    def test_next_cov_process_id_avoids_collision(self):
        client = self._client()
        client._app = _FakeCovApp()
        from bacpypes3.pdu import Address

        addr = Address("192.168.1.50")
        client._app._cov_contexts[(addr, 1)] = object()

        pid = client._next_cov_process_id(addr)

        assert pid != 1
        assert (addr, pid) not in client._app._cov_contexts

    def test_unsubscribe_unknown_key_is_noop(self):
        client = self._client()
        asyncio.run(client.unsubscribe_cov_property("does-not-exist"))  # must not raise


class _FakeCovValueSCM:
    """Fake change_of_value() context manager yielding scripted (id, value) pairs.

    Once the scripted items are exhausted, get_value() blocks so the
    asyncio.wait_for(..., timeout=0.05) drain loop in _cov_reader_task
    times out naturally instead of looping forever.
    """

    def __init__(self, items):
        self._items = list(items)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get_value(self):
        if self._items:
            return self._items.pop(0)
        await asyncio.sleep(10)


class TestCovReaderTaskKeyNormalization:
    """Regression test for issue #28: COV keys must be camelCase to match
    the keys entities read via get_object_value(prop="presentValue"),
    matching the normalization _try_rpm_poll already applies via
    _HYPHEN_TO_CAMEL."""

    def test_hyphenated_property_ids_normalized_to_camel_case(self):
        client = BACnetClient(local_ip="127.0.0.1", local_port=47815)
        scm = _FakeCovValueSCM([("present-value", 1), ("status-flags", [0, 1, 0, 0])])
        client._app = type(
            "_FakeApp", (), {"change_of_value": lambda self, *a, **k: scm}
        )()

        received: list[dict] = []

        async def run():
            ready_event = asyncio.Event()
            stop_event = asyncio.Event()
            task = asyncio.create_task(
                client._cov_reader_task(
                    addr="192.168.1.50",
                    oid="analog-input,1",
                    lifetime=300,
                    sub_key="sub-1",
                    obj_key="obj-1",
                    callback=lambda obj_key, changes: received.append(changes),
                    ready_event=ready_event,
                    stop_event=stop_event,
                )
            )
            await ready_event.wait()
            # Wait long enough for one full notification batch (drain
            # timeout is 0.05s) to be delivered to the callback.
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())

        assert len(received) == 1
        assert received[0] == {
            "presentValue": 1,
            "statusFlags": [False, True, False, False],
        }


class _FakeGracefulExitSCM:
    """Mimics real bacpypes3 SubscriptionContextManager.__aexit__ semantics:
    it only "sends" the device-side cancel when the context exits without
    an exception — matching the real library's behavior that motivated
    issue #29 (a CancelledError raised inside `async with scm:` makes
    __aexit__ abandon the subscription instead of cancelling it).
    """

    def __init__(self):
        self.cancel_sent = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.cancel_sent = True
        return False

    async def get_value(self):
        await asyncio.sleep(10)  # never resolves; only stop_event should end the wait


class TestCovGracefulUnsubscribe:
    """Regression test for issue #29: COV subscriptions were left active on
    the BACnet device after integration reload/unload because
    unsubscribe_cov() relied on task.cancel(), which raises CancelledError
    inside the reader task's `async with scm:` block — causing BACpypes3's
    SubscriptionContextManager.__aexit__() to abandon the context without
    sending the device-side cancel request. unsubscribe_cov() must instead
    signal a stop_event so the reader task's loop exits normally."""

    def test_unsubscribe_cov_lets_reader_exit_without_exception(self):
        client = BACnetClient(local_ip="127.0.0.1", local_port=47816)
        scm = _FakeGracefulExitSCM()
        client._app = type(
            "_FakeApp", (), {"change_of_value": lambda self, *a, **k: scm}
        )()

        async def run():
            ready_event = asyncio.Event()
            stop_event = asyncio.Event()
            sub_key = "sub-1"
            task = asyncio.create_task(
                client._cov_reader_task(
                    addr="192.168.1.50",
                    oid="analog-input,1",
                    lifetime=300,
                    sub_key=sub_key,
                    obj_key="obj-1",
                    callback=lambda *a: None,
                    ready_event=ready_event,
                    stop_event=stop_event,
                )
            )
            await ready_event.wait()
            client._cov_tasks[sub_key] = task
            client._cov_stop_events[sub_key] = stop_event

            await client.unsubscribe_cov(sub_key)

        asyncio.run(run())

        assert scm.cancel_sent is True

    def test_unsubscribe_unknown_key_is_noop(self):
        client = BACnetClient(local_ip="127.0.0.1", local_port=47817)
        asyncio.run(client.unsubscribe_cov("does-not-exist"))  # must not raise
