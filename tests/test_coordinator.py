"""Tests for BACnetCoordinator domain mapping and helper methods."""

from __future__ import annotations

import pytest

from custom_components.bacnet.const import (
    CONF_SELECTED_OBJECTS,
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


def _make_coordinator(
    objects=None, domain_overrides=None, cov_overrides=None, enable_cov=True
):
    """Return a BACnetCoordinator with mocked dependencies."""
    from unittest.mock import MagicMock

    client = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test"
    entry.data = {"device_address": "192.168.1.100"}

    hass = MagicMock()
    # Real HA schedules the coroutine; tests that don't care about the
    # COV-triggered metadata check (most of them) just need it not to leak
    # an "never awaited" warning. Tests that DO care override this.
    hass.async_create_task = MagicMock(side_effect=lambda coro: coro.close())

    coord = BACnetCoordinator(
        hass=hass,
        client=client,
        objects=objects or [],
        domain_overrides=domain_overrides or {},
        cov_overrides=cov_overrides or {},
        enable_cov=enable_cov,
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


# ---------------------------------------------------------------------------
# Per-object COV override
# ---------------------------------------------------------------------------


class TestCovOverrides:
    def test_override_false_skips_subscribe_even_if_global_enabled(self):
        import asyncio
        from unittest.mock import AsyncMock

        coord = _make_coordinator(
            objects=[{"object_type": 0, "instance": 1}],
            enable_cov=True,
            cov_overrides={"0:1": False},
        )
        coord.client.subscribe_cov = AsyncMock(return_value="sub_key")

        asyncio.run(coord._setup_subscriptions())

        coord.client.subscribe_cov.assert_not_awaited()
        assert "0:1" not in coord._cov_subscriptions
        assert coord._polled_objects == [{"object_type": 0, "instance": 1}]

    def test_override_true_subscribes_even_if_global_disabled(self):
        import asyncio
        from unittest.mock import AsyncMock

        coord = _make_coordinator(
            objects=[{"object_type": 0, "instance": 1}],
            enable_cov=False,
            cov_overrides={"0:1": True},
        )
        coord.client.subscribe_cov = AsyncMock(return_value="sub_key")

        asyncio.run(coord._setup_subscriptions())

        coord.client.subscribe_cov.assert_awaited_once()
        assert coord._cov_subscriptions["0:1"] == "sub_key"

    def test_no_override_falls_back_to_global_flag(self):
        import asyncio
        from unittest.mock import AsyncMock

        coord = _make_coordinator(
            objects=[{"object_type": 0, "instance": 1}],
            enable_cov=False,
            cov_overrides={},
        )
        coord.client.subscribe_cov = AsyncMock(return_value="sub_key")

        asyncio.run(coord._setup_subscriptions())

        coord.client.subscribe_cov.assert_not_awaited()
        assert coord._polled_objects == [{"object_type": 0, "instance": 1}]

    def test_mixed_overrides_per_object(self):
        """Two objects, opposite overrides, both diverging from the global flag."""
        import asyncio
        from unittest.mock import AsyncMock

        coord = _make_coordinator(
            objects=[
                {"object_type": 0, "instance": 1},
                {"object_type": 0, "instance": 2},
            ],
            enable_cov=True,
            cov_overrides={"0:1": False, "0:2": True},
        )
        coord.client.subscribe_cov = AsyncMock(return_value="sub_key")

        asyncio.run(coord._setup_subscriptions())

        assert "0:1" not in coord._cov_subscriptions
        assert coord._cov_subscriptions["0:2"] == "sub_key"
        assert coord._polled_objects == [{"object_type": 0, "instance": 1}]


# ---------------------------------------------------------------------------
# async_refresh_metadata — issue #26 (stale metadata after discovery)
# ---------------------------------------------------------------------------


class TestRefreshMetadata:
    def test_updates_changed_fields_and_persists(self):
        import asyncio
        from unittest.mock import AsyncMock

        obj = {
            "object_type": 0,
            "instance": 1,
            "object_name": "Old Name",
            "description": "",
            "units": None,
            "commandable": False,
        }
        coord = _make_coordinator(objects=[obj])
        coord.client.refresh_object_metadata = AsyncMock(
            return_value={
                "object_name": "New Name",
                "description": "",
                "units": "degrees-celsius",
                "commandable": False,
            }
        )

        changed = asyncio.run(coord.async_refresh_metadata())

        assert changed is True
        # A genuinely new dict replaces the original — see _diff_metadata's
        # docstring for why the original `obj` reference must NOT be mutated.
        assert coord.objects[0]["object_name"] == "New Name"
        assert coord.objects[0]["units"] == "degrees-celsius"
        coord.hass.config_entries.async_update_entry.assert_called_once()

    def test_no_change_skips_persist(self):
        import asyncio
        from unittest.mock import AsyncMock

        obj = {
            "object_type": 0,
            "instance": 1,
            "object_name": "Same",
            "description": "",
            "units": None,
            "commandable": False,
        }
        coord = _make_coordinator(objects=[obj])
        coord.client.refresh_object_metadata = AsyncMock(return_value=dict(obj))

        changed = asyncio.run(coord.async_refresh_metadata())

        assert changed is False
        coord.hass.config_entries.async_update_entry.assert_not_called()

    def test_none_result_is_skipped(self):
        import asyncio
        from unittest.mock import AsyncMock

        obj = {"object_type": 0, "instance": 1, "object_name": "Same"}
        coord = _make_coordinator(objects=[obj])
        coord.client.refresh_object_metadata = AsyncMock(return_value=None)

        changed = asyncio.run(coord.async_refresh_metadata())

        assert changed is False
        assert obj["object_name"] == "Same"

    def test_read_error_does_not_crash(self):
        import asyncio
        from unittest.mock import AsyncMock

        obj = {"object_type": 0, "instance": 1}
        coord = _make_coordinator(objects=[obj])
        coord.client.refresh_object_metadata = AsyncMock(
            side_effect=RuntimeError("boom")
        )

        changed = asyncio.run(coord.async_refresh_metadata())

        assert changed is False


class TestMetadataRefreshTiming:
    """_async_update_data must only trigger a metadata refresh once the
    DEFAULT_METADATA_REFRESH_INTERVAL has elapsed since the last one."""

    def test_not_triggered_before_interval_elapses(self):
        import asyncio
        from unittest.mock import AsyncMock

        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        coord._setup_subscriptions = AsyncMock()
        coord.client.poll_objects = AsyncMock(
            return_value={"0:1": {"presentValue": 1.0, "statusFlags": [False] * 4}}
        )
        coord.async_refresh_metadata = AsyncMock()

        asyncio.run(coord._async_update_data())

        coord.async_refresh_metadata.assert_not_awaited()

    def test_triggered_once_interval_elapses(self):
        import asyncio
        from datetime import datetime, timedelta, timezone
        from unittest.mock import AsyncMock

        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        coord._setup_subscriptions = AsyncMock()
        coord.client.poll_objects = AsyncMock(
            return_value={"0:1": {"presentValue": 1.0, "statusFlags": [False] * 4}}
        )
        coord.async_refresh_metadata = AsyncMock()
        coord._last_metadata_refresh = datetime.now(timezone.utc) - timedelta(hours=2)

        asyncio.run(coord._async_update_data())

        coord.async_refresh_metadata.assert_awaited_once()

    def test_not_triggered_on_failed_poll(self):
        """A failed poll must not attempt a metadata refresh either."""
        import asyncio
        from datetime import datetime, timedelta, timezone
        from unittest.mock import AsyncMock

        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        coord._setup_subscriptions = AsyncMock()
        coord.client.poll_objects = AsyncMock(
            return_value={"0:1": {"presentValue": None, "statusFlags": None}}
        )
        coord.async_refresh_metadata = AsyncMock()
        coord._last_metadata_refresh = datetime.now(timezone.utc) - timedelta(hours=2)

        asyncio.run(coord._async_update_data())

        coord.async_refresh_metadata.assert_not_awaited()


# ---------------------------------------------------------------------------
# COV-triggered metadata check — issue #26
# ---------------------------------------------------------------------------


class TestCovTriggeredMetadataCheck:
    """A COV notification is the cheapest live signal that a device is
    talking about an object right now — use it to trigger a throttled
    per-object metadata re-check instead of waiting on the hourly sweep."""

    def test_notification_schedules_a_background_task(self):
        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        coord.data = {"0:1": {"presentValue": 20.0}}

        captured = []
        coord.hass.async_create_task = lambda coro: captured.append(coro) or coro

        coord._handle_cov_notification("0:1", {"presentValue": 25.0})

        assert len(captured) == 1
        captured[0].close()  # avoid "never awaited" warning; not run in this test

    def test_second_notification_within_throttle_window_is_skipped(self):
        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        coord.data = {"0:1": {"presentValue": 20.0}}

        captured = []
        coord.hass.async_create_task = lambda coro: captured.append(coro) or coro

        coord._handle_cov_notification("0:1", {"presentValue": 25.0})
        coord._handle_cov_notification("0:1", {"presentValue": 26.0})

        assert len(captured) == 1
        captured[0].close()

    def test_notification_after_throttle_window_schedules_again(self):
        from datetime import datetime, timedelta, timezone

        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        coord.data = {"0:1": {"presentValue": 20.0}}

        captured = []
        coord.hass.async_create_task = lambda coro: captured.append(coro) or coro

        coord._handle_cov_notification("0:1", {"presentValue": 25.0})
        coord._last_object_metadata_check["0:1"] = datetime.now(
            timezone.utc
        ) - timedelta(minutes=10)
        coord._handle_cov_notification("0:1", {"presentValue": 26.0})

        assert len(captured) == 2
        for coro in captured:
            coro.close()

    def test_noop_when_data_is_none_does_not_schedule(self):
        """The existing 'no data yet' guard must still short-circuit first."""
        from unittest.mock import MagicMock

        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        coord.data = None
        coord.hass.async_create_task = MagicMock()

        coord._handle_cov_notification("0:1", {"presentValue": 25.0})

        coord.hass.async_create_task.assert_not_called()

    def test_refresh_object_metadata_now_applies_and_persists_change(self):
        import asyncio
        from unittest.mock import AsyncMock

        obj = {
            "object_type": 0,
            "instance": 1,
            "object_name": "Old",
            "units": None,
        }
        coord = _make_coordinator(objects=[obj])
        coord.client.refresh_object_metadata = AsyncMock(
            return_value={"object_name": "Old", "units": "degrees-celsius"}
        )

        asyncio.run(coord._refresh_object_metadata_now("0:1"))

        assert coord.objects[0]["units"] == "degrees-celsius"
        coord.hass.config_entries.async_update_entry.assert_called_once()

    def test_refresh_object_metadata_now_unknown_key_is_noop(self):
        import asyncio
        from unittest.mock import AsyncMock

        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        coord.client.refresh_object_metadata = AsyncMock()

        asyncio.run(coord._refresh_object_metadata_now("9:9"))

        coord.client.refresh_object_metadata.assert_not_awaited()


# ---------------------------------------------------------------------------
# Live SubscribeCOVProperty wiring — issue #26
# ---------------------------------------------------------------------------


class TestLiveMetadataPropertySubscriptions:
    def test_disabled_by_default_no_property_subscriptions_attempted(self):
        import asyncio
        from unittest.mock import AsyncMock

        coord = _make_coordinator(
            objects=[{"object_type": 0, "instance": 1, "commandable": False}]
        )
        coord.client.subscribe_cov = AsyncMock(return_value=None)
        coord.client.subscribe_cov_property = AsyncMock(return_value="prop-sub")

        asyncio.run(coord._setup_subscriptions())

        coord.client.subscribe_cov_property.assert_not_awaited()

    def test_enabled_property_is_attempted_and_tracked(self):
        import asyncio
        from unittest.mock import AsyncMock

        coord = _make_coordinator(
            objects=[{"object_type": 0, "instance": 1, "commandable": False}]
        )
        coord.live_metadata_properties = ["units"]
        coord.client.subscribe_cov = AsyncMock(return_value=None)
        coord.client.subscribe_cov_property = AsyncMock(return_value="prop-sub-key")

        asyncio.run(coord._setup_subscriptions())

        coord.client.subscribe_cov_property.assert_awaited_once()
        kwargs = coord.client.subscribe_cov_property.call_args.kwargs
        assert kwargs["property_name"] == "units"
        assert kwargs["object_type"] == 0
        assert kwargs["instance"] == 1
        assert coord._cov_property_subscriptions["0:1:units"] == "prop-sub-key"

    def test_rejected_property_subscription_is_not_tracked(self):
        import asyncio
        from unittest.mock import AsyncMock

        coord = _make_coordinator(
            objects=[{"object_type": 0, "instance": 1, "commandable": False}]
        )
        coord.live_metadata_properties = ["units", "description"]
        coord.client.subscribe_cov = AsyncMock(return_value=None)
        coord.client.subscribe_cov_property = AsyncMock(return_value=None)

        asyncio.run(coord._setup_subscriptions())

        assert coord.client.subscribe_cov_property.await_count == 2
        assert coord._cov_property_subscriptions == {}

    def test_disabled_cov_for_object_skips_property_subscriptions_too(self):
        """COV off for an object means no COV mechanism at all for it."""
        import asyncio
        from unittest.mock import AsyncMock

        coord = _make_coordinator(
            objects=[{"object_type": 0, "instance": 1, "commandable": False}],
            enable_cov=False,
        )
        coord.live_metadata_properties = ["units"]
        coord.client.subscribe_cov_property = AsyncMock(return_value="sub")

        asyncio.run(coord._setup_subscriptions())

        coord.client.subscribe_cov_property.assert_not_awaited()


class TestHandleCovPropertyNotification:
    def test_updates_and_persists_on_change(self):
        obj = {"object_type": 0, "instance": 1, "units": None}
        coord = _make_coordinator(objects=[obj])

        coord._handle_cov_property_notification("0:1", "units", "degrees-celsius")

        assert coord.objects[0]["units"] == "degrees-celsius"
        coord.hass.config_entries.async_update_entry.assert_called_once()

    def test_noop_when_value_unchanged(self):
        obj = {"object_type": 0, "instance": 1, "units": "degrees-celsius"}
        coord = _make_coordinator(objects=[obj])

        coord._handle_cov_property_notification("0:1", "units", "degrees-celsius")

        coord.hass.config_entries.async_update_entry.assert_not_called()

    def test_unknown_object_key_is_noop(self):
        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])

        coord._handle_cov_property_notification("9:9", "units", "kilowatts")

        coord.hass.config_entries.async_update_entry.assert_not_called()

    def test_unknown_bacnet_property_name_is_noop(self):
        obj = {"object_type": 0, "instance": 1}
        coord = _make_coordinator(objects=[obj])

        coord._handle_cov_property_notification("0:1", "presentValue", 42.0)

        assert "presentValue" not in obj
        coord.hass.config_entries.async_update_entry.assert_not_called()


class TestShutdownAndRestoreCleanupPropertySubs:
    def test_shutdown_unsubscribes_property_subs(self):
        import asyncio
        from unittest.mock import AsyncMock

        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        coord._cov_property_subscriptions = {"0:1:units": "sub-key"}
        coord.client.unsubscribe_cov = AsyncMock()
        coord.client.unsubscribe_cov_property = AsyncMock()

        asyncio.run(coord.async_shutdown())

        coord.client.unsubscribe_cov_property.assert_awaited_once_with("sub-key")
        assert coord._cov_property_subscriptions == {}

    def test_restore_subscriptions_clears_property_subs_before_resetup(self):
        import asyncio
        from unittest.mock import AsyncMock

        coord = _make_coordinator(objects=[{"object_type": 0, "instance": 1}])
        coord._cov_property_subscriptions = {"0:1:units": "stale-sub"}
        coord._setup_subscriptions = AsyncMock()

        asyncio.run(coord._restore_subscriptions())

        assert coord._cov_property_subscriptions == {}
        coord._setup_subscriptions.assert_awaited_once()


# ---------------------------------------------------------------------------
# Regression: metadata change must be a REAL entry.data change — issue #26
# ---------------------------------------------------------------------------


class TestMetadataChangeTriggersRealReload:
    """Home Assistant's async_update_entry only fires update listeners (and
    therefore only reloads the integration) when `entry.data != data`
    (homeassistant/config_entries.py). In production, coordinator.objects
    IS entry.data[CONF_SELECTED_OBJECTS] — the same list, same dicts,
    assigned once in __init__.py's async_setup_entry(). Mutating those
    dicts in place before calling async_update_entry made old and new data
    compare equal, so the real reload silently never fired: units/
    description looked updated via extra_state_attributes (a live read of
    the mutated dict) but unit_of_measurement never refreshed because the
    sensor entity was never recreated. Confirmed live on the v1.0.42-beta
    pre-release via issue #26."""

    def test_async_refresh_metadata_persists_genuinely_new_data(self):
        import asyncio
        from unittest.mock import AsyncMock

        obj = {"object_type": 0, "instance": 1, "object_name": "Old", "units": None}
        coord = _make_coordinator(objects=[obj])
        # Mirror __init__.py exactly: entry.data[CONF_SELECTED_OBJECTS] is
        # the SAME list object as coordinator.objects.
        coord.entry.data[CONF_SELECTED_OBJECTS] = coord.objects
        pre_change_entry_data = dict(coord.entry.data)

        coord.client.refresh_object_metadata = AsyncMock(
            return_value={"object_name": "Old", "units": "degrees-celsius"}
        )

        asyncio.run(coord.async_refresh_metadata())

        persisted_data = coord.hass.config_entries.async_update_entry.call_args.kwargs[
            "data"
        ]
        assert persisted_data != pre_change_entry_data, (
            "async_update_entry was called with data equal to entry.data "
            "before the change — real HA's `entry.data != data` check "
            "would treat this as a no-op and never fire the reload."
        )

    def test_refresh_object_metadata_now_persists_genuinely_new_data(self):
        import asyncio
        from unittest.mock import AsyncMock

        obj = {"object_type": 0, "instance": 1, "object_name": "Old", "units": None}
        coord = _make_coordinator(objects=[obj])
        coord.entry.data[CONF_SELECTED_OBJECTS] = coord.objects
        pre_change_entry_data = dict(coord.entry.data)

        coord.client.refresh_object_metadata = AsyncMock(
            return_value={"object_name": "Old", "units": "degrees-celsius"}
        )

        asyncio.run(coord._refresh_object_metadata_now("0:1"))

        persisted_data = coord.hass.config_entries.async_update_entry.call_args.kwargs[
            "data"
        ]
        assert persisted_data != pre_change_entry_data

    def test_handle_cov_property_notification_persists_genuinely_new_data(self):
        obj = {"object_type": 0, "instance": 1, "units": None}
        coord = _make_coordinator(objects=[obj])
        coord.entry.data[CONF_SELECTED_OBJECTS] = coord.objects
        pre_change_entry_data = dict(coord.entry.data)

        coord._handle_cov_property_notification("0:1", "units", "degrees-celsius")

        persisted_data = coord.hass.config_entries.async_update_entry.call_args.kwargs[
            "data"
        ]
        assert persisted_data != pre_change_entry_data
