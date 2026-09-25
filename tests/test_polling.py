"""Tests for BACnetClient.poll_objects batching and fallback concurrency.

Regressions covered:
  - one ReadPropertyMultiple for ALL objects overflowed the device's APDU
    limit, and ANY rejection disabled RPM for the device permanently
  - the per-object fallback read every property sequentially, so an
    offline device with many objects stalled a poll for many minutes
"""

from __future__ import annotations

import asyncio

from bacpypes3.apdu import AbortPDU, RejectPDU

from custom_components.bacnet.bacnet_client import BACnetClient
from custom_components.bacnet.const import MAX_CONCURRENT_REQUESTS, RPM_MAX_OBJECTS

_ADDR = "192.168.1.50"


def _objects(n):
    return [{"object_type": 0, "instance": i} for i in range(n)]


class _FakeRpmApp:
    """RPM answers with 1.0 per object; rejects requests over *max_objects*."""

    def __init__(self, max_objects=None, reject_all=None):
        self.max_objects = max_objects
        self.reject_all = reject_all
        self.rpm_sizes: list[int] = []
        self.reads = 0

    async def read_property_multiple(self, addr, param_list):
        size = len(param_list) // 2
        self.rpm_sizes.append(size)
        if self.reject_all is not None:
            raise self.reject_all
        if self.max_objects is not None and size > self.max_objects:
            raise AbortPDU(reason="segmentation-not-supported")
        results = []
        for oid_str in param_list[::2]:
            type_str, inst = oid_str.split(",")
            results.append(((type_str, int(inst)), "present-value", None, 1.0))
        return results

    async def read_property(self, addr, oid, prop_name, array_index=None):
        self.reads += 1
        return 1.0


def _client(app):
    client = BACnetClient(local_ip="127.0.0.1", local_port=47820)
    client._app = app
    return client


def _poll(client, n):
    return asyncio.run(client.poll_objects(_ADDR, _objects(n), ["presentValue"]))


class TestRpmChunking:
    def test_requests_are_split_into_chunks(self):
        app = _FakeRpmApp()
        result = _poll(_client(app), RPM_MAX_OBJECTS * 2 + 5)
        assert len(result) == RPM_MAX_OBJECTS * 2 + 5
        assert max(app.rpm_sizes) <= RPM_MAX_OBJECTS
        assert len(app.rpm_sizes) == 3

    def test_size_abort_shrinks_chunks_instead_of_disabling_rpm(self):
        app = _FakeRpmApp(max_objects=5)
        client = _client(app)
        # First poll: every chunk too big → data still complete via fallback.
        assert len(_poll(client, 40)) == 40
        assert client._rpm_supported.get(_ADDR, True) is True
        # Chunk size converges below the device limit; RPM stays in use.
        for _ in range(5):
            _poll(client, 40)
        app.rpm_sizes.clear()
        app.reads = 0
        assert len(_poll(client, 40)) == 40
        assert app.rpm_sizes and max(app.rpm_sizes) <= 5
        assert app.reads == 0

    def test_unrecognized_service_disables_rpm_immediately(self):
        app = _FakeRpmApp(reject_all=RejectPDU(reason="unrecognized-service"))
        client = _client(app)
        assert len(_poll(client, 10)) == 10
        assert client._rpm_supported[_ADDR] is False
        app.rpm_sizes.clear()
        _poll(client, 10)
        assert app.rpm_sizes == []

    def test_single_object_chunk_rejected_disables_rpm(self):
        app = _FakeRpmApp(max_objects=0)
        client = _client(app)
        for _ in range(10):
            _poll(client, 3)
        assert client._rpm_supported[_ADDR] is False


class _FakeReadOnlyApp:
    """Individual reads only; tracks peak concurrency."""

    def __init__(self, online=True):
        self.online = online
        self.in_flight = 0
        self.peak = 0
        self.reads = 0

    async def read_property(self, addr, oid, prop_name, array_index=None):
        self.reads += 1
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.001)
            if not self.online:
                raise asyncio.TimeoutError
            return 1.0
        finally:
            self.in_flight -= 1


def _fallback(app, n):
    client = _client(app)
    client._rpm_supported[_ADDR] = False
    return asyncio.run(
        client.poll_objects(_ADDR, _objects(n), ["presentValue", "statusFlags"])
    )


class TestFallbackPoll:
    def test_reads_run_concurrently_within_limit(self):
        app = _FakeReadOnlyApp()
        result = _fallback(app, 20)
        assert len(result) == 20
        assert all(v["presentValue"] == 1.0 for v in result.values())
        assert 1 < app.peak <= MAX_CONCURRENT_REQUESTS

    def test_offline_device_stops_after_first_batch(self):
        app = _FakeReadOnlyApp(online=False)
        result = _fallback(app, 200)
        # Every object still present (as None) so the coordinator sees an outage.
        assert len(result) == 200
        assert all(v["presentValue"] is None for v in result.values())
        assert app.reads <= MAX_CONCURRENT_REQUESTS * 2
