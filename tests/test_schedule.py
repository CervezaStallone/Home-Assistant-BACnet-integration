"""Tests for BACnet Schedule support (issue #52).

Codec and client logic run against real BACpypes3 types with a fake app in
place of the network; the service handler runs against a mocked client.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from bacpypes3.apdu import ErrorRejectAbortNack
from bacpypes3.basetypes import (
    CalendarEntry,
    DailySchedule,
    DateRange,
    SpecialEvent,
    SpecialEventPeriod,
    TimeValue,
)
from bacpypes3.constructeddata import AnyAtomic
from bacpypes3.primitivedata import (
    Date,
    Enumerated,
    Null,
    ObjectIdentifier,
    Real,
    Time,
    Unsigned,
)

from custom_components.bacnet import bacnet_client as bc
from custom_components.bacnet.bacnet_client import BACnetClient, ScheduleError

ADDR = "192.168.1.9"


def _day(*entries):
    return DailySchedule(
        daySchedule=[
            TimeValue(time=Time((h, m, 0, 0)), value=AnyAtomic(Real(v)))
            for h, m, v in entries
        ]
    )


class _Denied(ErrorRejectAbortNack):
    """A bare ErrorRejectAbortNack has no PDU behind it to print."""

    def __str__(self) -> str:
        return "write-access-denied"


class _FakeScheduleApp:
    """Stores written properties like a device; optionally misbehaves."""

    def __init__(self, props: dict, *, reject=(), ignore=()) -> None:
        self.props = props
        self.reject = set(reject)
        self.ignore = set(ignore)
        self.writes: list[tuple] = []

    async def read_property(self, addr, oid, prop, array_index=None):
        if str(oid[0]) == "calendar":
            return [CalendarEntry(date=Date((126, 12, 25, 5)))]
        value = self.props.get(prop)
        if value is None:
            raise ErrorRejectAbortNack("unknown-property")
        return value[array_index - 1] if array_index else value

    async def write_property(self, addr, oid, prop, value, array_index=None):
        self.writes.append((prop, array_index))
        if prop in self.reject:
            return _Denied()
        if prop in self.ignore:
            return None
        if array_index:
            self.props[prop][array_index - 1] = value
        else:
            self.props[prop] = (
                AnyAtomic(value) if prop == "scheduleDefault" else list(value)
            )
        return None


def _client(**kwargs) -> tuple[BACnetClient, _FakeScheduleApp]:
    props = {
        "presentValue": AnyAtomic(Real(21.0)),
        "scheduleDefault": AnyAtomic(Real(16.0)),
        "effectivePeriod": DateRange(
            startDate=Date((124, 1, 1, 1)), endDate=Date((255, 255, 255, 255))
        ),
        "weeklySchedule": [_day((8, 0, 21.0), (17, 0, 16.0)) for _ in range(7)],
        "exceptionSchedule": [
            SpecialEvent(
                period=SpecialEventPeriod(
                    calendarReference=ObjectIdentifier(("calendar", 1))
                ),
                listOfTimeValues=[
                    TimeValue(time=Time((0, 0, 0, 0)), value=AnyAtomic(Null(())))
                ],
                eventPriority=1,
            )
        ],
        "priorityForWriting": Unsigned(12),
    }
    client = BACnetClient(local_ip="127.0.0.1", local_port=47999)
    client._app = _FakeScheduleApp(props, **kwargs)
    return client, client._app


class TestCodec:
    @pytest.mark.parametrize(
        "raw",
        [(124, 1, 1, 1), (255, 12, 25, 255), (255, 13, 32, 255), (126, 255, 34, 3)],
    )
    def test_date_round_trip_keeps_wildcards_and_specials(self, raw):
        assert tuple(bc._date_from_json(bc._date_to_json(Date(raw)))) == raw

    def test_date_accepts_iso_string(self):
        assert tuple(bc._date_from_json("2026-12-25")) == (126, 12, 25, 5)

    @pytest.mark.parametrize(
        ("raw", "text"),
        [
            ((8, 0, 0, 0), "08:00:00"),
            ((6, 30, 15, 5), "06:30:15.05"),
            ((255, 255, 255, 255), "*:*:*.*"),
        ],
    )
    def test_time_round_trip(self, raw, text):
        assert bc._time_to_json(Time(raw)) == text
        assert tuple(bc._time_from_json(text)) == raw

    def test_time_accepts_short_form_and_yaml_sexagesimal(self):
        assert tuple(bc._time_from_json("6:30")) == (6, 30, 0, 0)
        assert tuple(bc._time_from_json(79200)) == (22, 0, 0, 0)

    def test_values_are_written_in_the_schedules_datatype(self):
        assert isinstance(bc._atomic_from_json(21, Real), Real)
        assert isinstance(bc._atomic_from_json(1.0, Enumerated), Enumerated)
        assert isinstance(bc._atomic_from_json(None, Real), Null)
        with pytest.raises(ValueError):
            bc._atomic_from_json(1.5, Unsigned)


class TestReadSchedule:
    def test_read(self):
        client, _ = _client()
        data = asyncio.run(client.read_schedule(ADDR, 1))
        assert data["present_value"] == 21.0
        assert data["value_type"] == "real"
        assert data["weekly_schedule"]["sunday"] == [
            {"time": "08:00:00", "value": 21.0},
            {"time": "17:00:00", "value": 16.0},
        ]
        assert data["effective_period"]["end"]["year"] is None
        assert data["exception_schedule"][0]["period"] == {"calendar_reference": 1}
        assert data["exception_schedule"][0]["time_values"][0]["value"] is None
        assert data["calendars"][0]["date_list"][0]["date"]["day"] == 25
        assert data["out_of_service"] is None  # property not on the device

    def test_weekly_schedule_falls_back_to_per_day_reads(self):
        client, app = _client()
        real_read = app.read_property

        async def no_bulk(addr, oid, prop, array_index=None):
            if prop == "weeklySchedule" and array_index is None:
                raise ErrorRejectAbortNack("segmentation-not-supported")
            return await real_read(addr, oid, prop, array_index)

        app.read_property = no_bulk
        data = asyncio.run(client.read_schedule(ADDR, 1))
        assert len(data["weekly_schedule"]) == 7

    def test_calendar_reads_are_capped(self):
        """The device decides how many calendars it refers to."""
        client, app = _client()
        event = app.props["exceptionSchedule"][0]
        app.props["exceptionSchedule"] = [
            SpecialEvent(
                period=SpecialEventPeriod(
                    calendarReference=ObjectIdentifier(("calendar", number))
                ),
                listOfTimeValues=event.listOfTimeValues,
                eventPriority=1,
            )
            for number in range(1, 200)
        ]
        data = asyncio.run(client.read_schedule(ADDR, 1))
        assert len(data["exception_schedule"]) == 199
        assert len(data["calendars"]) == bc._SCHEDULE_MAX_CALENDARS

    def test_silent_device_raises(self):
        client, app = _client()
        app.props.clear()
        with pytest.raises(ScheduleError):
            asyncio.run(client.read_schedule(ADDR, 1))


class TestWriteSchedule:
    def test_single_day_is_an_indexed_write_and_reads_back(self):
        client, app = _client()
        new = [{"time": "06:30:00", "value": 22.5}]
        data = asyncio.run(
            client.write_schedule(ADDR, 1, {"weekly_schedule": {"wednesday": new}})
        )
        assert app.writes == [("weeklySchedule", 3)]
        assert data["weekly_schedule"]["wednesday"] == new
        assert len(data["weekly_schedule"]["monday"]) == 2  # untouched

    def test_full_week_is_one_array_write(self):
        client, app = _client()
        week = {day: [{"time": "07:00:00", "value": 20.0}] for day in bc.WEEKDAYS}
        asyncio.run(client.write_schedule(ADDR, 1, {"weekly_schedule": week}))
        assert app.writes == [("weeklySchedule", None)]

    def test_rejected_write_raises(self):
        client, _ = _client(reject={"scheduleDefault"})
        with pytest.raises(ScheduleError, match="rejected"):
            asyncio.run(client.write_schedule(ADDR, 1, {"schedule_default": 15.0}))

    def test_readback_mismatch_raises(self):
        """Device acks the write but keeps the old value."""
        client, _ = _client(ignore={"scheduleDefault"})
        with pytest.raises(ScheduleError, match="reads back differently"):
            asyncio.run(client.write_schedule(ADDR, 1, {"schedule_default": 15.0}))

    def test_dropping_existing_exceptions_is_refused(self):
        """One new holiday must not silently erase the ones on the device."""
        client, app = _client()
        new = [{"period": {"date": "2026-12-25"}, "time_values": []}]
        with pytest.raises(ScheduleError, match="would remove 1 exception"):
            asyncio.run(client.write_schedule(ADDR, 1, {"exception_schedule": new}))
        assert app.writes == []

    def test_keeping_existing_exceptions_is_allowed(self):
        client, _ = _client()
        current = asyncio.run(client.read_schedule(ADDR, 1))["exception_schedule"]
        new = [*current, {"period": {"date": "2026-12-25"}, "time_values": []}]
        data = asyncio.run(client.write_schedule(ADDR, 1, {"exception_schedule": new}))
        assert len(data["exception_schedule"]) == 2

    def test_allow_removal_replaces_the_list(self):
        client, _ = _client()
        new = [{"period": {"date": "2026-12-25"}, "time_values": []}]
        data = asyncio.run(
            client.write_schedule(
                ADDR, 1, {"exception_schedule": new}, allow_removal=True
            )
        )
        assert [e["period"] for e in data["exception_schedule"]] == [
            {"date": {"year": 2026, "month": 12, "day": 25, "weekday": 5}}
        ]

    def test_unreadable_exceptions_are_not_overwritten_blindly(self):
        client, app = _client()
        del app.props["exceptionSchedule"]
        with pytest.raises(ScheduleError, match="could not read"):
            asyncio.run(client.write_schedule(ADDR, 1, {"exception_schedule": []}))
        assert app.writes == []

    @pytest.mark.parametrize(
        "changes",
        [
            {},
            {"weekly_schedule": {"funday": []}},
            {"weekly_schedule": {"monday": [{"time": "morning", "value": 1}]}},
            {"exception_schedule": [{"priority": 1}]},
        ],
    )
    def test_invalid_data_raises_before_any_write(self, changes):
        client, app = _client()
        with pytest.raises(ScheduleError):
            asyncio.run(client.write_schedule(ADDR, 1, changes))
        assert app.writes == []


def _call(data=None, user_id=None):
    return MagicMock(data=data or {}, context=MagicMock(user_id=user_id))


class TestScheduleService:
    def _setup(self, monkeypatch, object_type=17):
        from custom_components.bacnet import services

        obj = {"object_type": object_type, "instance": 1}
        coordinator = MagicMock()
        coordinator.device_address = ADDR
        coordinator.client.read_schedule = AsyncMock(return_value={"x": 1})
        coordinator.client.write_schedule = AsyncMock(return_value={"x": 2})
        coordinator.async_refresh_object = AsyncMock()
        monkeypatch.setattr(
            services,
            "_targets",
            lambda hass, call: [(coordinator, obj, "sensor.sched")],
        )
        return services, coordinator, obj

    def test_read_returns_response_per_entity(self, monkeypatch):
        services, coordinator, _ = self._setup(monkeypatch)
        result = asyncio.run(services.async_handle_schedule(None, _call(), write=False))
        assert result == {"sensor.sched": {"x": 1}}
        coordinator.client.read_schedule.assert_awaited_once_with(ADDR, 1)

    def test_write_passes_only_schedule_fields_and_refreshes(self, monkeypatch):
        services, coordinator, obj = self._setup(monkeypatch)
        call = _call({"entity_id": "sensor.sched", "schedule_default": None})
        result = asyncio.run(services.async_handle_schedule(None, call, write=True))
        assert result == {"sensor.sched": {"x": 2}}
        coordinator.client.write_schedule.assert_awaited_once_with(
            ADDR, 1, {"schedule_default": None}, allow_removal=False
        )
        coordinator.async_refresh_object.assert_awaited_once_with(obj)

    def test_non_schedule_entity_is_refused(self, monkeypatch):
        from homeassistant.exceptions import HomeAssistantError

        services, coordinator, _ = self._setup(monkeypatch, object_type=2)
        with pytest.raises(HomeAssistantError) as err:
            asyncio.run(services.async_handle_schedule(None, _call(), write=True))
        assert err.value.translation_key == "not_a_schedule"
        coordinator.client.write_schedule.assert_not_awaited()

    def _as_user(self, monkeypatch, services, allowed):
        user = MagicMock()
        user.permissions.check_entity.side_effect = lambda _id, policy: (
            policy in allowed
        )
        hass = MagicMock()
        hass.auth.async_get_user = AsyncMock(return_value=user)
        monkeypatch.setattr(
            services,
            "_referenced",
            lambda hass, call: MagicMock(
                referenced={"sensor.sched"}, indirectly_referenced=set()
            ),
        )
        return hass

    @pytest.mark.parametrize(
        ("write", "allowed"),
        [(False, set()), (True, set()), (True, {"read"}), (True, {"control"})],
    )
    def test_user_without_permission_is_refused(self, monkeypatch, write, allowed):
        """A write returns the schedule too, so it needs read and control."""
        from homeassistant.exceptions import Unauthorized

        services, coordinator, _ = self._setup(monkeypatch, object_type=2)
        monkeypatch.setattr(services, "POLICY_READ", "read")
        monkeypatch.setattr(services, "POLICY_CONTROL", "control")
        hass = self._as_user(monkeypatch, services, allowed)
        # Unauthorized, not "not_a_schedule": nothing about the entity leaks.
        with pytest.raises(Unauthorized):
            asyncio.run(
                services.async_handle_schedule(hass, _call(user_id="u1"), write=write)
            )
        coordinator.client.read_schedule.assert_not_awaited()
        coordinator.client.write_schedule.assert_not_awaited()

    def test_permitted_user_can_write(self, monkeypatch):
        services, coordinator, _ = self._setup(monkeypatch)
        monkeypatch.setattr(services, "POLICY_READ", "read")
        monkeypatch.setattr(services, "POLICY_CONTROL", "control")
        hass = self._as_user(monkeypatch, services, {"read", "control"})
        asyncio.run(
            services.async_handle_schedule(hass, _call(user_id="u1"), write=True)
        )
        coordinator.client.write_schedule.assert_awaited_once()

    def test_schedule_error_becomes_ha_error(self, monkeypatch):
        from homeassistant.exceptions import HomeAssistantError

        services, coordinator, _ = self._setup(monkeypatch)
        coordinator.client.read_schedule.side_effect = ScheduleError("boom")
        with pytest.raises(HomeAssistantError) as err:
            asyncio.run(services.async_handle_schedule(None, _call(), write=False))
        assert err.value.translation_placeholders["error"] == "boom"


def test_schedule_is_a_supported_sensor_object():
    from custom_components.bacnet.helpers import default_domain_for

    assert 17 in bc.SUPPORTED_OBJECT_TYPES
    assert BACnetClient._object_type_str_to_int("schedule") == 17
    assert BACnetClient._int_to_object_type_str(17) == "schedule"
    assert default_domain_for({"object_type": 17, "instance": 1}) == "sensor"
