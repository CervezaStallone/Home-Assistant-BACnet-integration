"""Contract tests against the real, pinned BACpypes3 (no fakes).

Several fixes rely on BACpypes3 internals that have no public API: the
request throttle (#12), renewal failure detection (#12), stopping open
requests on disconnect (#83) and the graceful COV cancel (#29). The client
reads most of them with getattr() and a harmless default, so after a
BACpypes3 bump that renames one, the fix silently stops working while the
fake-based tests keep passing. These tests run the real library against a
simulated device on loopback, so such a bump fails CI instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket

import pytest
from bacpypes3.app import Application
from bacpypes3.appservice import ClientSSM, ServerSSM
from bacpypes3.argparse import SimpleArgumentParser
from bacpypes3.local.analog import AnalogValueObjectCmd
from bacpypes3.pdu import Address
from bacpypes3.primitivedata import ObjectIdentifier

from custom_components.bacnet.bacnet_client import BACnetClient


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.asynccontextmanager
async def _device_and_client():
    """A simulated device with analog-value,1 and a connected client."""
    port = _free_port()
    args = SimpleArgumentParser().parse_args(
        ["--address", f"127.0.0.1:{port}", "--instance", "4000", "--name", "sim"]
    )
    device = Application.from_args(args)
    point = AnalogValueObjectCmd(
        objectIdentifier=("analog-value", 1),
        objectName="AV1",
        presentValue=20.0,
        units="degrees-celsius",
        covIncrement=0.1,
        statusFlags=[False] * 4,
        eventState="normal",
        outOfService=False,
    )
    device.add_object(point)
    client = BACnetClient(local_ip="127.0.0.1", local_port=_free_port())
    await client.connect()
    try:
        yield device, point, client, f"127.0.0.1:{port}"
    finally:
        await client.disconnect(graceful=False)
        device.close()


async def _until(condition, timeout: float = 5.0) -> None:
    async def wait():
        while not condition():
            await asyncio.sleep(0.05)

    await asyncio.wait_for(wait(), timeout)


def test_internals_the_client_relies_on_still_exist():
    """One clear list of what to adapt after a BACpypes3 bump."""

    async def run():
        async with _device_and_client() as (_device, _point, client, address):
            app = client._app
            asap = getattr(app, "asap", None)
            server = client._get_datagram_server()
            scm = app.change_of_value(
                Address(address), ObjectIdentifier("analog-value,1"), lifetime=60
            )
            checks = {
                "app._requests (disconnect, #83)": isinstance(
                    getattr(app, "_requests", None), dict
                ),
                "app.asap.clientTransactions (disconnect, #83)": isinstance(
                    getattr(asap, "clientTransactions", None), list
                ),
                "app.asap.serverTransactions (disconnect, #83)": isinstance(
                    getattr(asap, "serverTransactions", None), list
                ),
                "ClientSSM/ServerSSM.stop_timer (disconnect, #83)": callable(
                    getattr(ClientSSM, "stop_timer", None)
                )
                and callable(getattr(ServerSSM, "stop_timer", None)),
                "app._cov_contexts (COV process IDs, live metadata)": isinstance(
                    getattr(app, "_cov_contexts", None), dict
                ),
                "server._transport_tasks (connect waits for the socket)": isinstance(
                    getattr(server, "_transport_tasks", None), list
                ),
                "server.local_transport (connect checks the bind)": (
                    getattr(server, "local_transport", None) is not None
                ),
                "scm.create_refresh_task (renewal failures, #12)": callable(
                    getattr(scm, "create_refresh_task", None)
                ),
                "scm.refresh_subscription (live metadata subclass)": callable(
                    getattr(scm, "refresh_subscription", None)
                ),
            }
            for name in (
                "refresh_subscription_task",
                "refresh_subscription_handle",
                "subscriber_process_identifier",
                "monitored_object_identifier",
                "issue_confirmed_notifications",
                "lifetime",
                "address",
                "app",
            ):
                checks[f"scm.{name} (renewals, live metadata subclass)"] = hasattr(
                    scm, name
                )
            return [name for name, ok in checks.items() if not ok]

    assert asyncio.run(run()) == []


def test_disconnect_ends_requests_in_flight():
    """#83: a request to a silent device kept retrying on the closed socket,
    and whatever awaited it waited forever."""

    async def run():
        client = BACnetClient(local_ip="127.0.0.1", local_port=_free_port())
        await client.connect()
        app = client._app
        read = asyncio.create_task(
            app.read_property(
                Address(f"127.0.0.1:{_free_port()}"),
                ObjectIdentifier("analog-value,1"),
                "present-value",
            )
        )
        await _until(lambda: app.asap.clientTransactions)
        await client.disconnect(graceful=False)
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(read, 1)
        # No retry timer left to send on the closed socket.
        assert all(ssm._timer_handle is None for ssm in app.asap.clientTransactions)

    asyncio.run(run())


def test_requests_bacpypes3_sends_itself_pass_the_throttle():
    """The throttle replaces app.request; BACpypes3's own reads and COV
    subscriptions must still go through it, or #12 comes back."""

    async def run():
        async with _device_and_client() as (_device, _point, client, address):
            assert "request" in vars(client._app)  # the throttle is installed
            sent = []
            throttled = client._app.request

            def request(apdu):
                sent.append(type(apdu).__name__)
                return throttled(apdu)

            client._app.request = request
            value = await client._app.read_property(
                Address(address), ObjectIdentifier("analog-value,1"), "present-value"
            )
            sub_key = await client.subscribe_cov(
                device_address=address,
                object_type=2,
                instance=1,
                callback=lambda *a: None,
            )
            await client.unsubscribe_cov(sub_key)
            return value, sent

    value, sent = asyncio.run(run())
    assert value == 20.0
    assert "ReadPropertyRequest" in sent
    assert sent.count("SubscribeCOVRequest") == 2  # subscribe and cancel


def test_unsubscribe_cancels_the_subscription_on_the_device():
    """#29: BACpypes3 only sends the cancel when the context exits cleanly."""

    async def run():
        async with _device_and_client() as (device, _point, client, address):
            sub_key = await client.subscribe_cov(
                device_address=address,
                object_type=2,
                instance=1,
                callback=lambda *a: None,
            )
            assert device._cov_detections  # subscribed on the device
            await client.unsubscribe_cov(sub_key)
            await _until(lambda: not device._cov_detections)

    asyncio.run(run())


def test_failed_renewal_ends_the_subscription():
    """#12: a renewal the device rejects must end the reader task, or COV
    dies silently while the object still counts as subscribed."""

    async def run():
        async with _device_and_client() as (device, point, client, address):
            sub_key = await client.subscribe_cov(
                device_address=address,
                object_type=2,
                instance=1,
                callback=lambda *a: None,
                lifetime=3,  # BACpypes3 renews after 1 s
            )
            assert client.cov_subscription_active(sub_key)
            device.delete_object(point)  # the renewal gets unknown-object
            await _until(lambda: not client.cov_subscription_active(sub_key))

    asyncio.run(run())


def test_foreign_app_can_be_unregistered():
    """disconnect() stops the BBMD re-registration timer with unregister();
    without it the timer keeps sending on the closed socket (#54)."""

    async def run():
        client = BACnetClient(local_ip="127.0.0.1", local_port=_free_port())
        await client.connect(bbmd_address=f"127.0.0.1:{_free_port()}")
        try:
            app = client._app
            return callable(getattr(app, "unregister", None)), (
                client._get_datagram_server() is not None
            )
        finally:
            await client.disconnect(graceful=False)

    assert asyncio.run(run()) == (True, True)
