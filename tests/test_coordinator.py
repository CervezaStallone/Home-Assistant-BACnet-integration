"""Tests for BACnetCoordinator domain mapping and helper methods."""

from __future__ import annotations

import pytest

from custom_components.bacnet.const import (
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
    OBJECT_TYPE_BINARY_INPUT,
    OBJECT_TYPE_BINARY_OUTPUT,
    OBJECT_TYPE_BINARY_VALUE,
    OBJECT_TYPE_MULTI_STATE_INPUT,
    OBJECT_TYPE_MULTI_STATE_OUTPUT,
    OBJECT_TYPE_MULTI_STATE_VALUE,
)
from custom_components.bacnet.coordinator import BACnetCoordinator


def _make_coordinator(objects=None, domain_overrides=None):
    """Return a BACnetCoordinator with mocked dependencies."""
    from unittest.mock import MagicMock

    client = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test"
    entry.data = {"device_address": "192.168.1.100"}

    coord = BACnetCoordinator(
        hass=MagicMock(),
        client=client,
        objects=objects or [],
        domain_overrides=domain_overrides or {},
        entry=entry,
    )
    return coord


# ---------------------------------------------------------------------------
# _default_domain_for — commandable-aware domain selection
# ---------------------------------------------------------------------------


class TestDefaultDomainFor:
    @pytest.mark.parametrize(
        "obj_type, commandable, expected",
        [
            # Input types — always read-only
            (OBJECT_TYPE_ANALOG_INPUT, False, "sensor"),
            (OBJECT_TYPE_BINARY_INPUT, False, "binary_sensor"),
            (OBJECT_TYPE_MULTI_STATE_INPUT, False, "sensor"),
            # Output types — always commandable
            (OBJECT_TYPE_ANALOG_OUTPUT, True, "number"),
            (OBJECT_TYPE_BINARY_OUTPUT, True, "switch"),
            (OBJECT_TYPE_MULTI_STATE_OUTPUT, True, "number"),
            # Value types: commandable → writable domain
            (OBJECT_TYPE_ANALOG_VALUE, True, "number"),
            (OBJECT_TYPE_ANALOG_VALUE, False, "sensor"),
            (OBJECT_TYPE_BINARY_VALUE, True, "switch"),
            (OBJECT_TYPE_BINARY_VALUE, False, "binary_sensor"),
            (OBJECT_TYPE_MULTI_STATE_VALUE, True, "number"),
            (OBJECT_TYPE_MULTI_STATE_VALUE, False, "sensor"),
        ],
    )
    def test_domain(self, obj_type, commandable, expected):
        coord = _make_coordinator()
        obj = {"object_type": obj_type, "instance": 1, "commandable": commandable}
        assert coord._default_domain_for(obj) == expected

    def test_non_commandable_bv_is_not_switch(self):
        """Critical: non-commandable BV must NOT become a switch."""
        coord = _make_coordinator()
        obj = {
            "object_type": OBJECT_TYPE_BINARY_VALUE,
            "instance": 5,
            "commandable": False,
        }
        assert coord._default_domain_for(obj) == "binary_sensor"
        assert coord._default_domain_for(obj) != "switch"


# ---------------------------------------------------------------------------
# get_domain_for_object — overrides take precedence
# ---------------------------------------------------------------------------


class TestGetDomainForObject:
    def test_override_wins(self):
        coord = _make_coordinator(domain_overrides={"2:1": "climate"})
        obj = {
            "object_type": OBJECT_TYPE_ANALOG_VALUE,
            "instance": 1,
            "commandable": False,
        }
        assert coord.get_domain_for_object(obj) == "climate"

    def test_no_override_uses_default(self):
        coord = _make_coordinator()
        obj = {
            "object_type": OBJECT_TYPE_ANALOG_INPUT,
            "instance": 3,
            "commandable": False,
        }
        assert coord.get_domain_for_object(obj) == "sensor"

    def test_override_key_is_type_colon_instance(self):
        coord = _make_coordinator(domain_overrides={"5:10": "sensor"})
        obj = {
            "object_type": OBJECT_TYPE_BINARY_VALUE,
            "instance": 10,
            "commandable": True,
        }
        # Override should win over commandable-based default (switch)
        assert coord.get_domain_for_object(obj) == "sensor"


# ---------------------------------------------------------------------------
# get_entity_name
# ---------------------------------------------------------------------------


class TestGetEntityName:
    def test_returns_object_name_by_default(self):
        coord = _make_coordinator()
        obj = {
            "object_type": 0,
            "instance": 1,
            "object_name": "Room Temp",
            "description": "Room temperature sensor",
        }
        assert coord.get_entity_name(obj) == "Room Temp"

    def test_returns_description_when_use_description_enabled(self):
        coord = _make_coordinator()
        coord.use_description = True
        obj = {
            "object_type": 0,
            "instance": 1,
            "object_name": "Room Temp",
            "description": "Room temperature sensor",
        }
        assert coord.get_entity_name(obj) == "Room temperature sensor"

    def test_falls_back_to_object_name_when_no_description(self):
        coord = _make_coordinator()
        coord.use_description = True
        obj = {
            "object_type": 0,
            "instance": 1,
            "object_name": "Zone 1",
            "description": "",
        }
        assert coord.get_entity_name(obj) == "Zone 1"

    def test_fallback_when_no_object_name(self):
        coord = _make_coordinator()
        obj = {"object_type": 0, "instance": 5}
        assert "0:5" in coord.get_entity_name(obj) or "BACnet" in coord.get_entity_name(
            obj
        )


# ---------------------------------------------------------------------------
# get_update_method / is_cov_subscribed
# ---------------------------------------------------------------------------


class TestUpdateMethod:
    def test_polling_by_default(self):
        coord = _make_coordinator()
        assert coord.get_update_method("0:1") == "polling"

    def test_cov_when_subscribed(self):
        coord = _make_coordinator()
        coord._cov_subscriptions["0:1"] = "sub_key"
        assert coord.is_cov_subscribed("0:1") is True
        assert coord.get_update_method("0:1") == "COV"


# ---------------------------------------------------------------------------
# COV notification merge
# ---------------------------------------------------------------------------


class TestCOVNotification:
    def test_merges_into_existing_data(self):
        coord = _make_coordinator()
        coord.data = {"0:1": {"presentValue": 20.0, "statusFlags": [False] * 4}}
        coord._handle_cov_notification("0:1", {"presentValue": 25.0})
        assert coord.data["0:1"]["presentValue"] == 25.0
        assert coord.data["0:1"]["statusFlags"] == [False] * 4  # untouched

    def test_creates_new_key_if_missing(self):
        coord = _make_coordinator()
        coord.data = {}
        coord._handle_cov_notification("2:3", {"presentValue": 1.0})
        assert coord.data["2:3"]["presentValue"] == 1.0

    def test_noop_when_data_is_none(self):
        coord = _make_coordinator()
        coord.data = None
        coord._handle_cov_notification("0:1", {"presentValue": 5.0})
        assert coord.data is None


# ---------------------------------------------------------------------------
# Outage recovery — issue #18
# ---------------------------------------------------------------------------


# UpdateFailed is imported via the HA update_coordinator stub. The stub maps
# UpdateFailed to a real Exception subclass in conftest.py via the
# DataUpdateCoordinator stub module; fetch it lazily so this file imports
# cleanly whether or not the symbol is present.
def _update_failed_type():
    from homeassistant.helpers.update_coordinator import UpdateFailed

    return UpdateFailed


class TestPollYieldCheck:
    """The _poll_yielded_data static method defines what counts as a successful poll."""

    def test_none_polled_is_failure(self):
        assert BACnetCoordinator._poll_yielded_data(None) is False

    def test_empty_polled_is_failure(self):
        assert BACnetCoordinator._poll_yielded_data({}) is False

    def test_all_none_present_values_is_failure(self):
        polled = {"0:1": {"presentValue": None}, "4:2": {"presentValue": None}}
        assert BACnetCoordinator._poll_yielded_data(polled) is False

    def test_one_real_value_is_success(self):
        polled = {
            "0:1": {"presentValue": None},
            "4:2": {"presentValue": 1},
        }
        assert BACnetCoordinator._poll_yielded_data(polled) is True


class TestFailureTracking:
    """Consecutive failed polls must raise UpdateFailed after MAX_SILENT_FAILURES."""

    def test_first_failure_keeps_stale_data(self):
        """Below threshold, _handle_poll_failure returns normally."""
        import asyncio

        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        # Should not raise.
        asyncio.run(coord._handle_poll_failure())
        assert coord._consecutive_failures == 1

    def test_third_failure_raises_update_failed(self):
        import asyncio

        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        coord._consecutive_failures = 2  # already at 2

        UpdateFailed = _update_failed_type()
        with pytest.raises(UpdateFailed):
            asyncio.run(coord._handle_poll_failure())
        assert coord._consecutive_failures == 3

    def test_success_resets_counter(self):
        """A successful poll path must reset _consecutive_failures to 0."""
        import asyncio

        from unittest.mock import AsyncMock

        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        coord._consecutive_failures = 5
        # Stub setup so we test the poll path in isolation.
        coord._setup_subscriptions = AsyncMock()
        # Make poll_objects return real data.
        coord.client.poll_objects = AsyncMock(
            return_value={"0:1": {"presentValue": 23.5, "statusFlags": [False] * 4}}
        )

        asyncio.run(coord._async_update_data())

        assert coord._consecutive_failures == 0


class TestReconnectTrigger:
    """At RECONNECT_THRESHOLD failures, client.reconnect() is called exactly once."""

    def test_reconnect_called_at_threshold(self):
        import asyncio

        from unittest.mock import AsyncMock

        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        coord._consecutive_failures = 9  # one away from RECONNECT_THRESHOLD (10)
        coord.client.reconnect = AsyncMock()

        # failures become 10 → triggers reconnect AND raises UpdateFailed.
        UpdateFailed = _update_failed_type()
        with pytest.raises(UpdateFailed):
            asyncio.run(coord._handle_poll_failure())

        coord.client.reconnect.assert_awaited_once()
        assert coord._needs_resubscribe is True

    def test_reconnect_not_called_below_threshold(self):
        import asyncio

        from unittest.mock import AsyncMock

        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        coord._consecutive_failures = 0
        coord.client.reconnect = AsyncMock()

        asyncio.run(coord._handle_poll_failure())

        coord.client.reconnect.assert_not_awaited()
        assert coord._needs_resubscribe is False

    def test_reconnect_called_only_once_per_outage(self):
        """Past RECONNECT_THRESHOLD, subsequent failures must NOT re-trigger."""
        import asyncio

        from unittest.mock import AsyncMock

        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        coord._consecutive_failures = 20  # well past threshold
        coord.client.reconnect = AsyncMock()

        UpdateFailed = _update_failed_type()
        with pytest.raises(UpdateFailed):
            asyncio.run(coord._handle_poll_failure())

        coord.client.reconnect.assert_not_awaited()

    def test_reconnect_failure_does_not_crash_coordinator(self):
        """If client.reconnect() raises, _handle_poll_failure must absorb it."""
        import asyncio

        from unittest.mock import AsyncMock

        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        coord._consecutive_failures = 9
        coord.client.reconnect = AsyncMock(side_effect=RuntimeError("boom"))

        # Should not raise the inner RuntimeError; it surfaces as UpdateFailed
        # because failures (10) >= MAX_SILENT_FAILURES (3).
        UpdateFailed = _update_failed_type()
        with pytest.raises(UpdateFailed):
            asyncio.run(coord._handle_poll_failure())
        # needs_resubscribe stays False because reconnect threw.
        assert coord._needs_resubscribe is False


class TestRestoreSubscriptions:
    """After reconnect + first successful poll, COV subs are rebuilt."""

    def test_successful_poll_after_reconnect_calls_setup(self):
        import asyncio

        from unittest.mock import AsyncMock

        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        # Pretend an earlier setup already ran so first_run does not double-call.
        coord._cov_subscriptions = {"0:1": "stale_key"}
        coord._polled_objects = [{"object_type": 0, "instance": 1}]
        coord._needs_resubscribe = True
        coord.client.poll_objects = AsyncMock(
            return_value={"0:1": {"presentValue": 23.5, "statusFlags": [False] * 4}}
        )
        # _setup_subscriptions would try to subscribe via the client; stub it
        # so we can assert it was invoked without doing real BACnet I/O.
        coord._setup_subscriptions = AsyncMock()

        asyncio.run(coord._async_update_data())

        # Restore path calls setup exactly once (first_run was False).
        coord._setup_subscriptions.assert_awaited_once()
        assert coord._needs_resubscribe is False
        # _restore_subscriptions cleared the stale mapping before re-setup.
        # (Setup itself is mocked, so it stays empty after the clear.)
        assert coord._cov_subscriptions == {}

    def test_no_resubscribe_without_reconnect(self):
        """Normal successful polls must NOT rebuild subscriptions every cycle."""
        import asyncio

        from unittest.mock import AsyncMock

        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        coord._needs_resubscribe = False
        coord.client.poll_objects = AsyncMock(
            return_value={"0:1": {"presentValue": 23.5, "statusFlags": [False] * 4}}
        )
        # Stubbed so first_run setup also doesn't run real COV I/O.
        called = {"count": 0}

        async def fake_setup():
            called["count"] += 1
            coord._polled_objects = list(coord.objects)

        coord._setup_subscriptions = fake_setup

        asyncio.run(coord._async_update_data())

        # first_run triggers setup exactly once; restore path did not.
        assert called["count"] == 1
