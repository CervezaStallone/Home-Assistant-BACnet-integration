"""bacnet.read_trend_log: Trend Log records via ReadRange (Reddit feedback).

Only records with a time and a value are returned; log-status, time-change,
failure and other bookkeeping records are left out at this layer.
"""

from __future__ import annotations

import asyncio

from bacpypes3.basetypes import (
    DateTime,
    LogRecord,
    LogRecordLogDatum,
    LogStatus,
)
from bacpypes3.primitivedata import Date, Time

from custom_components.bacnet.bacnet_client import BACnetClient

_ADDR = "192.168.1.80"


def _record(when, **datum):
    day, clock = when.split(" ")
    return LogRecord(
        timestamp=DateTime(date=Date(day), time=Time(clock)),
        logDatum=LogRecordLogDatum(**datum),
    )


class _App:
    def __init__(self, records, record_count=None):
        self.records = records
        self.record_count = record_count
        self.ranges = []

    async def read_property(self, addr, oid, prop, array_index=None):
        assert prop == "recordCount"
        return self.record_count

    async def read_range(self, addr, oid, prop, arr_index=None, range_params=None):
        self.ranges.append(range_params)
        return self.records


def _client(app):
    client = BACnetClient(local_ip="127.0.0.1", local_port=47850)
    client._app = app
    return client


def test_last_records_by_position_keep_only_values():
    app = _App(
        [
            _record("2026-10-19 10:00:00", realValue=21.5),
            _record("2026-10-19 10:05:00", logStatus=LogStatus([False, True, False])),
            _record("2026-10-19 10:10:00", booleanValue=True),
            _record("2026-10-19 10:15:00", timeChange=3.0),
            _record("2026-10-19 10:20:00", unsignedValue=3),
        ],
        record_count=500,
    )
    records = asyncio.run(_client(app).read_trend_log(_ADDR, 4, start=None, count=100))
    assert records == [
        {"time": "2026-10-19T10:00:00", "value": 21.5},
        {"time": "2026-10-19T10:10:00", "value": True},
        {"time": "2026-10-19T10:20:00", "value": 3},
    ]
    # the last 100 records: position 500 backwards
    assert app.ranges == [("p", 500, None, None, -100)]


def test_from_start_time_reads_by_time():
    import datetime

    app = _App([_record("2026-10-19 08:00:00", realValue=19.0)])
    records = asyncio.run(
        _client(app).read_trend_log(
            _ADDR, 4, start=datetime.datetime(2026, 10, 19, 7, 30), count=50
        )
    )
    assert records == [{"time": "2026-10-19T08:00:00", "value": 19.0}]
    assert app.ranges == [("t", None, "2026-10-19", "07:30:00", 50)]
