"""Diagnostics download, diagnostic sensors and the outage repair issue."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from custom_components.bacnet.coordinator import UpdateFailed
from tests.test_coordinator import _make_coordinator as _real_coordinator


def _polling_coordinator(present_value):
    coord = _real_coordinator(objects=[{"object_type": 0, "instance": 1}])
    coord._setup_subscriptions = AsyncMock()
    coord.client.poll_objects = AsyncMock(
        return_value={"0:1": {"presentValue": present_value, "statusFlags": None}}
    )
    coord.client.reconnect = AsyncMock()
    return coord


class TestCoordinatorHealth:
    def test_successful_poll_records_timestamp(self):
        coord = _polling_coordinator(1.0)
        assert coord.last_successful_poll is None
        asyncio.run(coord._async_update_data())
        assert isinstance(coord.last_successful_poll, datetime)

    def test_summary_reports_subscriptions_and_failures(self):
        coord = _polling_coordinator(1.0)
        coord._cov_subscriptions = {"0:1": "sub"}
        coord._consecutive_failures = 2
        summary = coord.diagnostics_summary()
        assert summary["cov_subscriptions"] == 1
        assert summary["consecutive_failed_polls"] == 2
        assert summary["objects"] == 1


class TestOutageIssue:
    def _run_polls(self, monkeypatch, n, present_value=None):
        import custom_components.bacnet.coordinator as coord_mod

        ir = MagicMock()
        monkeypatch.setattr(coord_mod, "ir", ir)
        coord = _polling_coordinator(present_value)
        for _ in range(n):
            with contextlib.suppress(UpdateFailed):  # after MAX_SILENT_FAILURES
                asyncio.run(coord._async_update_data())
        return coord, ir

    def test_issue_raised_once_outage_lasts(self, monkeypatch):
        from custom_components.bacnet.const import RECONNECT_THRESHOLD

        _, ir = self._run_polls(monkeypatch, RECONNECT_THRESHOLD - 1)
        ir.async_create_issue.assert_not_called()
        _, ir = self._run_polls(monkeypatch, RECONNECT_THRESHOLD)
        ir.async_create_issue.assert_called_once()
        assert ir.async_create_issue.call_args.kwargs["translation_key"] == (
            "device_unreachable"
        )

    def test_issue_cleared_by_successful_poll(self, monkeypatch):
        _, ir = self._run_polls(monkeypatch, 1, present_value=1.0)
        ir.async_delete_issue.assert_called()


class TestDiagnosticsDownload:
    def test_addresses_are_redacted(self):
        from custom_components.bacnet.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        coord = _polling_coordinator(1.0)
        entry = MagicMock()
        entry.data = {
            "local_ip": "192.168.1.10/24",
            "device_address": "192.168.1.50",
            "bbmd_address": "10.0.0.1:47808",
            "device_id": 1001,
        }
        entry.options = {"polling_interval": 30}
        entry.runtime_data.coordinator = coord
        result = asyncio.run(async_get_config_entry_diagnostics(MagicMock(), entry))
        text = str(result)
        assert "192.168.1.50" not in text
        assert "10.0.0.1" not in text
        assert result["entry"]["data"]["device_id"] == 1001
        assert result["coordinator"]["objects"] == 1


class TestDiagnosticSensors:
    def _sensors(self, coord):
        from custom_components.bacnet.sensor import (
            DIAGNOSTIC_SENSORS,
            BACnetDiagnosticSensor,
        )
        from tests.conftest import _make_entry

        return {
            d.key: BACnetDiagnosticSensor(coord, _make_entry(), d)
            for d in DIAGNOSTIC_SENSORS
        }

    def test_values_and_categories(self):
        coord = _polling_coordinator(1.0)
        coord.last_successful_poll = datetime(2026, 9, 25, tzinfo=timezone.utc)
        coord._cov_subscriptions = {"a": 1, "b": 2}
        coord._consecutive_failures = 3
        sensors = self._sensors(coord)
        assert (
            sensors["last_successful_poll"].native_value == coord.last_successful_poll
        )
        assert sensors["cov_subscriptions"].native_value == 2
        assert sensors["consecutive_failed_polls"].native_value == 3
        assert all(s._attr_entity_category == "diagnostic" for s in sensors.values())

    def test_stay_available_during_outage(self):
        coord = _polling_coordinator(None)
        coord.last_update_success = False
        assert all(s.available for s in self._sensors(coord).values())
