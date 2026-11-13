"""
Tests for BACnetClient pure-Python logic.

Network I/O is NOT tested here — those require a real or simulated BACnet
device. This file covers deterministic static/class methods that can be
verified without any network.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import pytest
from bacpypes3.primitivedata import Null

from custom_components.bacnet.bacnet_client import BACnetClient
from custom_components.bacnet.const import (
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
    OBJECT_TYPE_BINARY_VALUE,
    OBJECT_TYPE_MULTI_STATE_OUTPUT,
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

    def test_non_finite_real_is_no_value(self):
        """A device reporting NaN (e.g. an unconnected input) made Home
        Assistant reject every state write of a numeric sensor."""
        from bacpypes3.primitivedata import Real

        for raw in (Real(float("nan")), Real(float("inf")), float("-inf")):
            assert BACnetClient._coerce_value(raw) is None

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
        self.calls.append({"property_name": property_name, "value": value, **kwargs})


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

    def test_relinquish_sends_null_instance(self):
        """Null() without args returns the Null *class*; BACpypes3 then fails
        its isinstance(value, Null) check and the write dies with TypeError."""
        client = self._client()
        asyncio.run(
            client.relinquish(
                device_address="192.168.1.1",
                object_type=OBJECT_TYPE_MULTI_STATE_OUTPUT,
                instance=1,
                priority=11,
            )
        )
        assert isinstance(client._app.calls[0]["value"], Null)
        assert isinstance(client._python_to_bacnet_value(None, 0), Null)


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

    def test_disconnect_bumps_connection_generation(self):
        """Other coordinators sharing this client must see that their COV
        tasks are gone after a reconnect triggered by one of them (#12)."""
        client = BACnetClient(local_ip="127.0.0.1", local_port=47820)
        before = client.connection_generation

        asyncio.run(client.disconnect(graceful=False))

        assert client.connection_generation == before + 1


class TestDisconnectStopsTransactions:
    """Issue #83: requests in flight when the client reconnected kept
    retrying on the closed socket every few seconds, forever. BACpypes3
    resets the retry count before the send that then fails, so the retry
    limit is never reached; every attempt logged a traceback."""

    def test_disconnect_stops_retry_timers_of_open_transactions(self):
        stopped = []

        class _SSM:
            def __init__(self, name):
                self.name = name

            def stop_timer(self):
                stopped.append(self.name)

        class _App:
            asap = type(
                "ASAP",
                (),
                {
                    "clientTransactions": [_SSM("c1")],
                    "serverTransactions": [_SSM("s1")],
                },
            )()

            def close(self):
                stopped.append("closed")

        client = BACnetClient(local_ip="127.0.0.1", local_port=47823)
        client._app = _App()

        asyncio.run(client.disconnect(graceful=False))

        assert stopped == ["c1", "s1", "closed"]

    def test_disconnect_fails_requests_in_flight_and_queued(self):
        """Their futures were never finished, so whatever awaited them
        (renewals, writes, queued requests) hung forever."""
        import custom_components.bacnet.bacnet_client as bc

        async def run():
            app = _RecordingApp()
            app.close = lambda: None
            bc._throttle_requests(app, limit=1)
            in_flight = app.request(_read_request())
            queued = app.request(_read_request())
            # BACpypes3 keeps the futures of open requests here.
            app._requests = {"10.0.0.1": [app.sent[0]]}
            client = BACnetClient(local_ip="127.0.0.1", local_port=47826)
            client._app = app

            await client.disconnect(graceful=False)

            with pytest.raises(ConnectionError):
                await asyncio.wait_for(in_flight, 1)
            with pytest.raises(ConnectionError):
                await asyncio.wait_for(queued, 1)
            await asyncio.sleep(0)
            assert len(app.sent) == 1  # nothing sent on the closed app

        asyncio.run(run())

    def test_disconnect_detaches_the_app_before_failing_requests(self):
        """Code woken by the failure must not send on the app being closed."""
        import custom_components.bacnet.bacnet_client as bc

        async def run():
            app = _RecordingApp()
            app.close = lambda: None
            app.unregister = lambda: None  # BBMD: disconnect() yields once
            bc._throttle_requests(app, limit=1)
            future = app.request(_read_request())
            app._requests = {"10.0.0.1": [app.sent[0]]}
            client = BACnetClient(local_ip="127.0.0.1", local_port=47827)
            client._app = app
            seen = []
            future.add_done_callback(lambda _f: seen.append(client._app))

            await client.disconnect(graceful=False)
            await asyncio.sleep(0)
            assert seen == [None]

        asyncio.run(run())


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

    refresh_subscription_task = None

    def create_refresh_task(self):
        pass

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

    refresh_subscription_task = None

    def create_refresh_task(self):
        pass

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


class _FakeRenewingSCM(_FakeGracefulExitSCM):
    """Like BACpypes3: renewals run as a task from create_refresh_task(),
    and nobody awaits that task, so a failed renewal went unnoticed."""

    refresh_subscription_task = None

    async def refresh_subscription(self):
        raise RuntimeError("no-response")

    def create_refresh_task(self):
        self.refresh_subscription_task = asyncio.create_task(
            self.refresh_subscription()
        )


class TestCovRenewalFailure:
    """Issue #12: when a renewal failed (busy device), the subscription
    expired on the device while the reader task kept waiting forever, so
    COV died silently and every failure logged an unretrieved exception."""

    def test_failed_renewal_ends_the_subscription(self):
        client = BACnetClient(local_ip="127.0.0.1", local_port=47821)
        scm = _FakeRenewingSCM()
        client._app = type(
            "_FakeApp", (), {"change_of_value": lambda self, *a, **k: scm}
        )()

        async def run():
            ready_event = asyncio.Event()
            sub_key = "sub-1"
            task = asyncio.create_task(
                client._cov_reader_task(
                    addr="192.168.1.50",
                    oid="analog-input,1",
                    lifetime=300,
                    sub_key=sub_key,
                    obj_key="0:1",
                    callback=lambda *a: None,
                    ready_event=ready_event,
                    stop_event=asyncio.Event(),
                )
            )
            await ready_event.wait()
            client._cov_tasks[sub_key] = task
            assert client.cov_subscription_active(sub_key)

            scm.create_refresh_task()  # BACpypes3's call_later fires
            await asyncio.wait_for(task, timeout=1)

            assert not client.cov_subscription_active(sub_key)
            assert scm.refresh_subscription_task.done()

        asyncio.run(run())

    def test_failed_renewal_ends_a_live_metadata_subscription(self):
        client = BACnetClient(local_ip="127.0.0.1", local_port=47822)
        scm = _FakeRenewingSCM()

        async def run():
            ready_event = asyncio.Event()
            sub_key = "sub-1:units"
            task = asyncio.create_task(
                client._cov_property_reader_task(
                    scm=scm,
                    sub_key=sub_key,
                    obj_key="0:1",
                    property_name="units",
                    callback=lambda *a: None,
                    ready_event=ready_event,
                    stop_event=asyncio.Event(),
                )
            )
            await ready_event.wait()
            client._cov_property_tasks[sub_key] = task
            assert client.cov_subscription_active(sub_key)

            scm.create_refresh_task()
            await asyncio.wait_for(task, timeout=1)

            assert not client.cov_subscription_active(sub_key)

        asyncio.run(run())

    def test_no_renewal_after_the_subscription_ended(self):
        """A renewal still in flight when the reader exits reschedules itself
        after the unsubscribe, and would renew the subscription forever."""
        client = BACnetClient(local_ip="127.0.0.1", local_port=47824)
        scm = _FakeRenewingSCM()
        client._app = type(
            "_FakeApp", (), {"change_of_value": lambda self, *a, **k: scm}
        )()

        async def run():
            ready_event = asyncio.Event()
            stop_event = asyncio.Event()
            task = asyncio.create_task(
                client._cov_reader_task(
                    addr="192.168.1.50",
                    oid="analog-input,1",
                    lifetime=300,
                    sub_key="sub-1",
                    obj_key="0:1",
                    callback=lambda *a: None,
                    ready_event=ready_event,
                    stop_event=stop_event,
                )
            )
            await ready_event.wait()
            stop_event.set()
            await asyncio.wait_for(task, timeout=1)

            scm.create_refresh_task()  # the late renewal's call_later fires

        asyncio.run(run())
        assert scm.refresh_subscription_task is None

    def test_renewal_in_flight_is_cancelled_when_the_subscription_ends(self):
        """Otherwise it subscribes again on the device after the cancel."""
        client = BACnetClient(local_ip="127.0.0.1", local_port=47828)

        class _SlowRenewalSCM(_FakeRenewingSCM):
            async def refresh_subscription(self):
                await asyncio.Event().wait()

        scm = _SlowRenewalSCM()
        client._app = type(
            "_FakeApp", (), {"change_of_value": lambda self, *a, **k: scm}
        )()

        async def run():
            ready_event = asyncio.Event()
            stop_event = asyncio.Event()
            task = asyncio.create_task(
                client._cov_reader_task(
                    addr="192.168.1.50",
                    oid="analog-input,1",
                    lifetime=300,
                    sub_key="sub-1",
                    obj_key="0:1",
                    callback=lambda *a: None,
                    ready_event=ready_event,
                    stop_event=stop_event,
                )
            )
            await ready_event.wait()
            scm.create_refresh_task()  # renewal goes out, no answer yet
            await asyncio.sleep(0)
            stop_event.set()
            await asyncio.wait_for(task, timeout=1)
            await asyncio.sleep(0)
            assert scm.refresh_subscription_task.cancelled()

        asyncio.run(run())

    def test_cancelled_subscribe_leaves_no_reader_behind(self):
        """A cancelled resubscribe (unload, reconnect) left a reader task
        that completed the subscription without anyone tracking it."""

        class _HangingSCM:
            refresh_subscription_task = None

            def create_refresh_task(self):
                pass

            async def __aenter__(self):
                await asyncio.Event().wait()

            async def __aexit__(self, *exc):
                return False

        client = BACnetClient(local_ip="127.0.0.1", local_port=47829)
        client._app = type(
            "_FakeApp",
            (),
            {
                "change_of_value": lambda self, *a, **k: _HangingSCM(),
                "_cov_contexts": {},
            },
        )()

        async def run():
            subscribe = asyncio.create_task(
                client.subscribe_cov(
                    device_address="192.168.1.50",
                    object_type=0,
                    instance=1,
                    callback=lambda *a: None,
                )
            )
            await asyncio.sleep(0.05)
            reader = next(iter(client._cov_tasks.values()))
            subscribe.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await subscribe
            await asyncio.sleep(0)
            assert client._cov_tasks == {}
            assert reader.done()

        asyncio.run(run())

    def test_failed_live_renewal_is_logged_as_renewal(self, caplog):
        """Not as 'COV-Property not supported', at debug level."""
        client = BACnetClient(local_ip="127.0.0.1", local_port=47825)
        scm = _FakeRenewingSCM()

        async def run():
            ready_event = asyncio.Event()
            task = asyncio.create_task(
                client._cov_property_reader_task(
                    scm=scm,
                    sub_key="sub-1:units",
                    obj_key="0:1",
                    property_name="units",
                    callback=lambda *a: None,
                    ready_event=ready_event,
                    stop_event=asyncio.Event(),
                )
            )
            await ready_event.wait()
            scm.create_refresh_task()
            await asyncio.wait_for(task, timeout=1)

        with caplog.at_level(logging.INFO, logger="custom_components.bacnet"):
            asyncio.run(run())
        assert "renewal failed" in caplog.text
        assert "not supported" not in caplog.text


# ---------------------------------------------------------------------------
# BACpypes3 0.0.110 upgrade — issue #52
# ---------------------------------------------------------------------------


class TestNullCoercion:
    """Newer BACpypes3 returns Null for a Null-valued atomic property."""

    def test_null_becomes_none(self):
        from bacpypes3.primitivedata import Null

        assert BACnetClient._coerce_value(Null(())) is None

    def test_any_atomic_is_unwrapped(self):
        from bacpypes3.constructeddata import AnyAtomic
        from bacpypes3.primitivedata import Null, Real

        assert BACnetClient._coerce_value(AnyAtomic(Real(21.5))) == 21.5
        assert BACnetClient._coerce_value(AnyAtomic(Null(()))) is None


class TestFreePortProbe:
    """SO_REUSEPORT (BACpypes3 >= 0.0.105) hides port conflicts; the probe
    must still detect a port that another socket already holds."""

    def test_port_in_use_raises_then_passes_once_freed(self):
        import socket

        holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if hasattr(socket, "SO_REUSEPORT"):
            holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        holder.bind(("127.0.0.1", 0))
        addr = holder.getsockname()
        client = BACnetClient(local_ip=addr[0], local_port=addr[1])
        try:
            with pytest.raises(RuntimeError, match="could not be bound"):
                asyncio.run(client._wait_for_free_port(addr, timeout=0))
        finally:
            holder.close()
        asyncio.run(client._wait_for_free_port(addr, timeout=0))


# ---------------------------------------------------------------------------
# BBMD / foreign-device discovery — issue #54
# ---------------------------------------------------------------------------


class TestDiscoveryUnansweredRead:
    """A device can answer Who-Is (forwarded by a BBMD) and still not answer
    unicast reads. BACpypes3 raises AbortPDU for that — a BaseException, so
    `except Exception` in the config flow missed it and HA showed
    "Unknown error occurred"."""

    def test_abort_on_name_read_does_not_escape(self):
        from bacpypes3.apdu import AbortPDU, IAmRequest
        from bacpypes3.pdu import Address

        i_am = IAmRequest(iAmDeviceIdentifier=("device", 1234))
        i_am.pduSource = Address("10.0.5.20")

        class _App:
            async def who_is(self, **kwargs):
                return [i_am]

            async def read_property(self, addr, oid, prop_name, array_index=None):
                raise AbortPDU()

        client = BACnetClient(local_ip="127.0.0.1", local_port=47830)
        client._app = _App()

        devices = asyncio.run(client.discover_devices(timeout=1))

        assert [(d["device_id"], d["device_name"]) for d in devices] == [
            (1234, "Device 1234")
        ]


class TestDiscoverySkipsOwnNode:
    """The Who-Is broadcast reaches Home Assistant's own BACnet node too, so
    discovery listed "HomeAssistant-BACnet" first and pre-selected it."""

    def test_own_i_am_is_not_listed(self):
        from bacpypes3.apdu import IAmRequest
        from bacpypes3.pdu import Address

        client = BACnetClient(local_ip="127.0.0.1", local_port=47833)
        own = IAmRequest(iAmDeviceIdentifier=("device", client._device_instance))
        own.pduSource = Address("127.0.0.1")
        other = IAmRequest(iAmDeviceIdentifier=("device", 37171))
        other.pduSource = Address("10.0.5.20")

        class _App:
            async def who_is(self, **kwargs):
                return [own, other]

            async def read_property(self, addr, oid, prop_name, array_index=None):
                return None

        client._app = _App()

        devices = asyncio.run(client.discover_devices(timeout=1))

        assert [d["device_id"] for d in devices] == [37171]


class TestDisconnectStopsForeignRegistration:
    """ForeignApplication.close() leaves the BBMD re-registration timer
    running, which then sends on the closed socket every 5 s forever."""

    def test_unregister_called_before_close(self):
        calls: list[str] = []

        class _ForeignApp:
            def unregister(self):
                calls.append("unregister")

            def close(self):
                calls.append("close")

        client = BACnetClient(local_ip="127.0.0.1", local_port=47831)
        client._app = _ForeignApp()

        asyncio.run(client.disconnect())

        assert calls == ["unregister", "close"]
        assert client._app is None


# ---------------------------------------------------------------------------
# read_device_info() on a slow device — issue #52
# ---------------------------------------------------------------------------


class TestReadDeviceInfoSlowMetadata:
    """The overall timeout used to cover the optional metadata reads too, so
    a slow controller that had already identified itself was reported as
    unreachable."""

    def test_slow_metadata_does_not_fail_identification(self):
        from bacpypes3.apdu import IAmRequest

        i_am = IAmRequest(iAmDeviceIdentifier=("device", 10002))

        class _App:
            async def who_is(self, **kwargs):
                return [i_am]

            async def read_property(self, addr, oid, prop_name, array_index=None):
                await asyncio.sleep(0.05)  # slower than the timeout below
                return {"objectName": "DDC4000"}.get(prop_name)

        client = BACnetClient(local_ip="127.0.0.1", local_port=47832)
        client._app = _App()

        info = asyncio.run(client.read_device_info("10.0.0.5", timeout=0.02))

        assert info["device_id"] == 10002
        assert info["device_name"] == "DDC4000"
        assert info["address"] == "10.0.0.5"


# ---------------------------------------------------------------------------
# Any property read/write (Reddit feedback: e.g. minimumOnTime, property 67)
# ---------------------------------------------------------------------------


class TestNormalizeProperty:
    @pytest.mark.parametrize("prop", ["minimumOnTime", "minimum-on-time", "67", 67])
    def test_name_or_number(self, prop):
        assert BACnetClient.normalize_property(prop) == "minimumOnTime"

    def test_proprietary_number_kept(self):
        assert BACnetClient.normalize_property("9999") == "9999"

    def test_unknown_name_raises(self):
        with pytest.raises(ValueError):
            BACnetClient.normalize_property("bogus")


class TestReadAnyProperty:
    def _client(self, values):
        client = BACnetClient(local_ip="127.0.0.1", local_port=47812)
        client._app = _FakeReadApp(values)
        return client

    def _read(self, client, prop):
        return asyncio.run(client.read_any_property("192.168.1.1", 4, 1, prop))

    def test_real_becomes_float(self):
        from bacpypes3.primitivedata import Real

        assert self._read(self._client({"highLimit": Real(30.5)}), "highLimit") == 30.5

    def test_enumerated_becomes_its_name(self):
        from bacpypes3.basetypes import EventState

        client = self._client({"eventState": EventState("high-limit")})
        assert self._read(client, "eventState") == "high-limit"

    def test_sequence_becomes_dict(self):
        from bacpypes3.basetypes import DeviceObjectPropertyReference
        from bacpypes3.primitivedata import ObjectIdentifier

        ref = DeviceObjectPropertyReference(
            objectIdentifier=ObjectIdentifier("analog-input,1"),
            propertyIdentifier="present-value",
        )
        client = self._client({"logDeviceObjectProperty": ref})
        assert self._read(client, "logDeviceObjectProperty") == {
            "objectIdentifier": "analog-input,1",
            "propertyIdentifier": "present-value",
        }

    def test_device_error_raises(self):
        from bacpypes3.apdu import RejectPDU

        from custom_components.bacnet.bacnet_client import BACnetPropertyError

        class _ErrApp:
            async def read_property(self, *args, **kwargs):
                return RejectPDU(reason="unrecognized-service")

        client = BACnetClient(local_ip="127.0.0.1", local_port=47813)
        client._app = _ErrApp()
        with pytest.raises(BACnetPropertyError, match="unrecognized-service"):
            self._read(client, "minimumOnTime")


def test_write_other_property_uses_its_datatype_and_no_priority():
    from bacpypes3.primitivedata import Unsigned

    client = BACnetClient(local_ip="127.0.0.1", local_port=47814)
    client._app = _FakeApp()
    asyncio.run(
        client.write_property(
            device_address="192.168.1.1",
            object_type=4,
            instance=1,
            property_name="minimumOnTime",
            value=30,
            priority=8,
            commandable=True,
        )
    )
    call = client._app.calls[0]
    assert type(call["value"]) is Unsigned and call["value"] == 30
    assert "priority" not in call


def test_write_without_answer_times_out(monkeypatch):
    """A write in flight when the client reconnects is never answered (#83).

    disconnect() stops its retries, so without a timeout the action waits forever.
    """
    import custom_components.bacnet.bacnet_client as bc

    class _SilentApp:
        async def write_property(self, *args, **kwargs):
            await asyncio.Event().wait()

    monkeypatch.setattr(bc, "_WRITE_TIMEOUT", 0.01)
    client = BACnetClient(local_ip="127.0.0.1", local_port=47815)
    client._app = _SilentApp()

    async def run():
        return await asyncio.wait_for(
            client.write_property(
                device_address="192.168.1.1",
                object_type=OBJECT_TYPE_BINARY_VALUE,
                instance=1,
                property_name="presentValue",
                value=1,
                priority=8,
                commandable=True,
            ),
            timeout=2,
        )

    assert asyncio.run(run()) is False


def test_int_to_object_type_str_knows_every_standard_type():
    """read_property on a non-imported object (e.g. a Trend Log) needs its name."""
    assert BACnetClient._int_to_object_type_str(20) == "trend-log"
    assert BACnetClient._int_to_object_type_str(600) == "600"


@pytest.mark.parametrize(
    ("text", "expected"), [("false", False), ("On", True), ("0", False)]
)
def test_boolean_property_from_text(text, expected):
    """The action's value field is text; "false" must not become True."""
    value = BACnetClient._value_for_property("binary-output", "outOfService", text)
    assert type(value).__name__ == "Boolean"
    assert bool(value) is expected


def test_real_property_written_as_real():
    """highLimit on an analog object is a REAL, not Unsigned."""
    value = BACnetClient._value_for_property("analog-value", "highLimit", 30)
    assert type(value).__name__ == "Real"


@pytest.mark.parametrize(
    ("prop", "value"), [("minimumOnTime", 1.5), ("minimumOnTime", "abc")]
)
def test_invalid_value_for_property_is_refused(prop, value):
    """1.5 seconds must not silently become 1; text is no Unsigned at all."""
    from custom_components.bacnet.bacnet_client import BACnetPropertyError

    client = BACnetClient(local_ip="127.0.0.1", local_port=47815)
    client._app = _FakeApp()
    with pytest.raises(BACnetPropertyError, match=prop):
        asyncio.run(
            client.write_property(
                device_address="192.168.1.1",
                object_type=4,
                instance=1,
                property_name=prop,
                value=value,
            )
        )
    assert client._app.calls == []


def test_text_for_text_property_is_kept():
    value = BACnetClient._value_for_property("binary-output", "inactiveText", "123")
    assert str(value) == "123"


def test_normalize_property_always_returns_plain_str():
    for prop in ("9999", "minimumOnTime", 67):
        assert type(BACnetClient.normalize_property(prop)) is str


class TestStableCovProcessId:
    """#52: a new subscriber process id after every HA restart left the old
    subscriptions occupying the controller's COV slots until they expired
    (Black Pear 2: 248 COV objects dropped to 8 after a quick restart).
    The same object must get the same id, so the device replaces the old
    subscription instead of adding one."""

    def _pid(self, *args, **kwargs):
        client = BACnetClient(local_ip="127.0.0.1", local_port=47870)
        client._app = _FakeCovApp()
        return client._stable_cov_process_id(*args, **kwargs)

    def test_same_object_same_id_across_restarts(self):
        from bacpypes3.pdu import Address

        addr = Address("192.168.1.5")
        assert self._pid(addr, 0, 1) == self._pid(addr, 0, 1)
        assert self._pid(addr, 0, 1, "units") == self._pid(addr, 0, 1, "units")

    def test_different_objects_and_properties_differ(self):
        from bacpypes3.pdu import Address

        addr = Address("192.168.1.5")
        ids = {
            self._pid(addr, 0, 1),
            self._pid(addr, 0, 2),
            self._pid(addr, 1, 1),
            self._pid(addr, 0, 1, "units"),
        }
        assert len(ids) == 4
        assert all(0 < pid < (1 << 22) for pid in ids)

    def test_id_in_use_is_skipped(self):
        from bacpypes3.pdu import Address

        addr = Address("192.168.1.5")
        client = BACnetClient(local_ip="127.0.0.1", local_port=47871)
        client._app = _FakeCovApp()
        first = client._stable_cov_process_id(addr, 0, 1)
        client._app._cov_contexts[(addr, first)] = object()
        assert client._stable_cov_process_id(addr, 0, 1) != first


def test_subscribe_cov_uses_the_stable_process_id():
    """The value-COV path must hand the stable id to bacpypes3 instead of
    letting it take the next number of its counter."""
    from bacpypes3.pdu import Address

    seen = {}
    scm = _FakeCovValueSCM([])

    class _App:
        def __init__(self):
            self._cov_contexts = {}

        def change_of_value(self, addr, oid, **kwargs):
            seen.update(kwargs)
            return scm

    client = BACnetClient(local_ip="127.0.0.1", local_port=47872)
    client._app = _App()

    async def run():
        sub_key = await client.subscribe_cov("192.168.1.5", 0, 7, lambda *a: None)
        await client.unsubscribe_cov(sub_key)

    asyncio.run(run())
    expected = client._stable_cov_process_id(Address("192.168.1.5"), 0, 7)
    assert seen["subscriber_process_identifier"] == expected


def test_rpm_supported_defaults_true_and_follows_fallback():
    client = BACnetClient(local_ip="127.0.0.1", local_port=47861)
    assert client.rpm_supported("10.0.0.5") is True
    client._rpm_supported["10.0.0.5"] = False
    assert client.rpm_supported("10.0.0.5") is False


# ---------------------------------------------------------------------------
# Auto-detect: Who-Is broadcast without a subnet prefix
# ---------------------------------------------------------------------------


class _FakeServer:
    broadcast_address = None


class _FakeNormalApp:
    """Same shape as BACpypes3's: the server sits on the link layer."""

    def __init__(self, device_object, local_addr):
        self.local_addr = local_addr
        self.normal = type("Link", (), {"server": _FakeServer()})()
        self.server = self.normal.server
        if local_addr.addrBroadcastTuple != local_addr.addrTuple:
            self.server.broadcast_address = local_addr.addrBroadcastTuple

    def request(self, apdu):
        raise AssertionError("no network in this test")


class TestBroadcastAutoDetect:
    """An empty local IP bound 0.0.0.0 without a broadcast address, so every
    Who-Is failed with "no broadcast" and setup found no devices."""

    def _connect(self, monkeypatch, local_ip, interface):
        import custom_components.bacnet.bacnet_client as bc

        monkeypatch.setattr(bc, "NormalApplication", _FakeNormalApp)
        client = BACnetClient(local_ip=local_ip, local_port=47808, interface=interface)

        async def no_wait(*_a, **_k):
            return None

        monkeypatch.setattr(client, "_wait_for_free_port", no_wait)
        monkeypatch.setattr(client, "_wait_for_transport", no_wait)
        asyncio.run(client.connect())
        return client._app

    def test_empty_ip_keeps_any_address_and_gets_the_subnet_broadcast(
        self, monkeypatch
    ):
        app = self._connect(monkeypatch, "", "10.13.37.11/24")
        assert app.local_addr.addrTuple == ("0.0.0.0", 47808)
        assert app.server.broadcast_address == ("10.13.37.255", 47808)

    def test_ip_without_prefix_gets_the_interface_prefix(self, monkeypatch):
        app = self._connect(monkeypatch, "10.13.37.11", "10.13.37.11/24")
        assert app.local_addr.addrTuple == ("10.13.37.11", 47808)
        assert app.server.broadcast_address == ("10.13.37.255", 47808)

    def test_explicit_prefix_wins(self, monkeypatch):
        app = self._connect(monkeypatch, "10.13.37.11/16", "10.13.37.11/24")
        assert app.server.broadcast_address == ("10.13.255.255", 47808)

    def test_unknown_interface_changes_nothing(self, monkeypatch):
        app = self._connect(monkeypatch, "", None)
        assert app.server.broadcast_address is None

    def test_device_instance_does_not_depend_on_the_interface(self):
        with_iface = BACnetClient(local_ip="", interface="10.13.37.11/24")
        without = BACnetClient(local_ip="")
        assert with_iface._device_instance == without._device_instance


class TestBroadcastOnRealApplication:
    """The fake app above had a ``server`` attribute; BACpypes3's real
    NormalApplication keeps it on its link layer (``app.normal.server``), so
    1.0.52-beta.1/2 set nothing and discovery still failed."""

    def test_real_app_gets_the_subnet_broadcast(self):
        async def run():
            client = BACnetClient(local_ip="", local_port=0, interface="10.13.37.11/24")
            await client.connect()
            try:
                return client._app.normal.server.broadcast_address
            finally:
                await client.disconnect()

        assert asyncio.run(run()) == ("10.13.37.255", 0)


# ---------------------------------------------------------------------------
# Outstanding-request cap per device — issue #12
# ---------------------------------------------------------------------------


class _RecordingApp:
    """Stands in for a BACpypes3 app: request() returns a future per APDU."""

    def __init__(self, *_args):
        self.sent: list = []

    def request(self, apdu):
        fut = asyncio.get_running_loop().create_future()
        self.sent.append((apdu, fut))
        return fut


def _read_request(address="10.0.0.1"):
    from bacpypes3.apdu import ReadPropertyRequest
    from bacpypes3.pdu import Address
    from bacpypes3.primitivedata import ObjectIdentifier

    return ReadPropertyRequest(
        objectIdentifier=ObjectIdentifier("analog-value,1"),
        propertyIdentifier="present-value",
        destination=Address(address),
    )


def _subscribe_request(address="10.0.0.1", lifetime=300):
    from bacpypes3.apdu import SubscribeCOVRequest
    from bacpypes3.pdu import Address
    from bacpypes3.primitivedata import ObjectIdentifier

    return SubscribeCOVRequest(
        subscriberProcessIdentifier=1,
        monitoredObjectIdentifier=ObjectIdentifier("analog-value,1"),
        lifetime=lifetime,
        destination=Address(address),
    )


class TestRequestThrottle:
    """BACpypes3 gives each device 256 invoke IDs and loops forever looking
    for a free one when all are in use. 640 COV renewals due at once on a
    slow device froze the whole event loop at 100% CPU (issue #12)."""

    def test_caps_outstanding_requests_per_device(self):
        import custom_components.bacnet.bacnet_client as bc

        async def run():
            app = _RecordingApp()
            bc._throttle_requests(app, limit=2)
            futures = [app.request(_read_request()) for _ in range(3)]
            assert len(app.sent) == 2

            app.sent[0][1].set_result("ack")
            await asyncio.sleep(0)
            assert len(app.sent) == 3  # the waiting request went out
            assert await futures[0] == "ack"

            app.sent[2][1].set_result("third")
            assert await futures[2] == "third"

        asyncio.run(run())

    def test_subscriptions_leave_room_for_other_requests(self):
        """A switched-off device: hundreds of renewals each wait out their
        timeout and filled every slot, so a poll waited minutes behind them
        and the outage was noticed that much later."""
        import custom_components.bacnet.bacnet_client as bc

        async def run():
            app = _RecordingApp()
            bc._throttle_requests(app, limit=4, reserved=1)
            for _ in range(10):
                app.request(_subscribe_request())
            assert len(app.sent) == 3
            app.request(_read_request())
            assert len(app.sent) == 4  # the read did not wait

        asyncio.run(run())

    def test_queued_subscriptions_go_first_within_their_share(self):
        """Renewals are sent 2 s before the subscription expires, so they must
        not starve behind reads; reads keep their reserved slots."""
        import custom_components.bacnet.bacnet_client as bc

        async def run():
            app = _RecordingApp()
            bc._throttle_requests(app, limit=2, reserved=1)
            app.request(_subscribe_request())
            app.request(_subscribe_request())  # waits: subscription share
            app.request(_read_request())
            app.request(_read_request())  # waits: device cap
            assert len(app.sent) == 2

            app.sent[0][1].set_result("ack")
            await asyncio.sleep(0)
            assert len(app.sent) == 3
            assert type(app.sent[2][0]).__name__ == "SubscribeCOVRequest"

        asyncio.run(run())

    def test_cancelling_a_subscription_is_not_held_up_by_renewals(self):
        """A cancellation that waits too long is abandoned (#29)."""
        import custom_components.bacnet.bacnet_client as bc

        async def run():
            app = _RecordingApp()
            bc._throttle_requests(app, limit=2, reserved=1)
            app.request(_subscribe_request())
            app.request(_subscribe_request(lifetime=None))
            assert len(app.sent) == 2

        asyncio.run(run())

    def test_other_devices_are_not_held_up(self):
        import custom_components.bacnet.bacnet_client as bc

        async def run():
            app = _RecordingApp()
            bc._throttle_requests(app, limit=1)
            app.request(_read_request("10.0.0.1"))
            app.request(_read_request("10.0.0.2"))
            assert len(app.sent) == 2

        asyncio.run(run())

    def test_unconfirmed_requests_pass_straight_through(self):
        from bacpypes3.apdu import WhoIsRequest
        from bacpypes3.pdu import Address

        import custom_components.bacnet.bacnet_client as bc

        async def run():
            app = _RecordingApp()
            bc._throttle_requests(app, limit=1)
            app.request(_read_request())
            app.request(WhoIsRequest(destination=Address("10.0.0.1")))
            assert len(app.sent) == 2

        asyncio.run(run())

    def test_cancelled_waiting_request_is_never_sent(self):
        import custom_components.bacnet.bacnet_client as bc

        async def run():
            app = _RecordingApp()
            bc._throttle_requests(app, limit=1)
            app.request(_read_request())
            waiting = app.request(_read_request())
            waiting.cancel()
            app.sent[0][1].set_result("ack")
            await asyncio.sleep(0)
            assert len(app.sent) == 1

        asyncio.run(run())

    def test_cancelling_a_forwarded_request_cancels_it_in_bacpypes3(self):
        """BACpypes3 frees the invoke ID when its future is cancelled."""
        import custom_components.bacnet.bacnet_client as bc

        async def run():
            app = _RecordingApp()
            bc._throttle_requests(app, limit=1)
            app.request(_read_request())
            waiting = app.request(_read_request())
            app.sent[0][1].set_result("ack")
            await asyncio.sleep(0)
            waiting.cancel()
            await asyncio.sleep(0)
            assert app.sent[1][1].cancelled()

        asyncio.run(run())

    def test_connect_throttles_the_application(self, monkeypatch):
        import custom_components.bacnet.bacnet_client as bc

        class App(_RecordingApp, _FakeNormalApp):
            def __init__(self, device_object, local_addr):
                _RecordingApp.__init__(self)
                _FakeNormalApp.__init__(self, device_object, local_addr)

        monkeypatch.setattr(bc, "NormalApplication", App)
        client = BACnetClient(local_ip="127.0.0.1", local_port=47808)

        async def no_wait(*_a, **_k):
            return None

        monkeypatch.setattr(client, "_wait_for_free_port", no_wait)
        monkeypatch.setattr(client, "_wait_for_transport", no_wait)

        async def run():
            await client.connect()
            for _ in range(bc.MAX_OUTSTANDING_REQUESTS + 1):
                client._app.request(_read_request())
            assert len(client._app.sent) == bc.MAX_OUTSTANDING_REQUESTS

        asyncio.run(run())
