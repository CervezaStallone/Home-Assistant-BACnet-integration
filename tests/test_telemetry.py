"""Tests for the daily usage report (telemetry.py).

The server rejects the whole report with 400 when anything is outside its
contract, so the payload limits are pinned here.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.bacnet import telemetry
from custom_components.bacnet.telemetry import (
    build_report,
    clean_text,
    collect_devices,
    device_report,
    object_counts,
)


def test_clean_text_strips_control_chars_and_cuts():
    assert clean_text("V6.00\n.338\t\x00", 128) == "V6.00.338"
    assert clean_text("  x  ", 128) == "x"
    assert clean_text("a" * 300, 128) == "a" * 128
    assert clean_text(None, 128) == ""


def test_object_counts_keys_limits():
    objs = [{"object_type": 0}] * 3 + [{"object_type": 19}] + [{"object_type": 2000}]
    assert object_counts(objs) == {"0": 3, "19": 1}
    many = [{"object_type": t} for t in range(100)]
    assert list(object_counts(many)) == [str(t) for t in range(64)]
    assert object_counts([{"object_type": 0}] * 100001) == {"0": 100000}


def test_device_report_fields_and_connection():
    data = {
        "vendor_name": "Siemens",
        "model_name": "PXC200-E.D",
        "firmware_version": "V6.00.338",
        "use_bbmd": True,
        "device_address": "10.0.0.5",
        "device_id": 1234,
    }
    rep = device_report(data, [{"object_type": 2}], cov=True, rpm=False)
    assert rep == {
        "vendor": "Siemens",
        "model": "PXC200-E.D",
        "firmware": "V6.00.338",
        "connection": "foreign_device",
        "cov": True,
        "rpm": False,
        "objects": {"2": 1},
    }
    empty = device_report({}, [], cov=False, rpm=True)
    assert empty["connection"] == "direct"
    assert empty["vendor"] == ""


def test_build_report_shape():
    install_id = str(uuid.uuid4())
    rep = build_report(install_id.upper(), "1.0.50-beta.10", "2026.10.1", [{}] * 300)
    assert set(rep) == {
        "schema",
        "install_id",
        "integration_version",
        "ha_version",
        "devices",
    }
    assert rep["schema"] == 1
    assert len(rep["devices"]) == 256
    assert rep["install_id"] == install_id


def _entry(share=True, polled=True, loaded=True):
    if loaded:
        entry = MagicMock()
        coord = entry.runtime_data.coordinator
        coord.last_successful_poll = datetime.now(UTC) if polled else None
        coord.objects = [{"object_type": 0, "instance": 1}]
        coord.cov_active = True
        coord.device_address = "10.0.0.5"
        coord.client.rpm_supported.return_value = False
    else:
        entry = MagicMock(spec=["options", "data"])
    entry.options = {"share_usage": share}
    entry.data = {"vendor_name": "Siemens", "model_name": "PXC200-E.D"}
    return entry


def test_collect_devices_only_opted_in_loaded_polled():
    included = _entry()
    entries = [
        included,
        _entry(share=False),
        _entry(loaded=False),
        _entry(polled=False),
    ]
    devices = collect_devices(entries)
    assert len(devices) == 1
    assert devices[0]["vendor"] == "Siemens"
    assert devices[0]["cov"] is True
    assert devices[0]["rpm"] is False
    assert devices[0]["objects"] == {"0": 1}
    included.runtime_data.coordinator.client.rpm_supported.assert_called_with(
        "10.0.0.5"
    )


def _reporter(entries, store_data=None):
    """A TelemetryReporter with HA's store, session and loader faked."""
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = entries
    store = MagicMock()
    store.async_load = AsyncMock(return_value=store_data)
    store.async_save = AsyncMock()
    session = MagicMock()
    response = MagicMock(status=204)
    post_cm = MagicMock()
    post_cm.__aenter__ = AsyncMock(return_value=response)
    post_cm.__aexit__ = AsyncMock(return_value=False)
    session.post = MagicMock(return_value=post_cm)
    integration = MagicMock(version="1.0.50-beta.10")
    patches = [
        patch.object(telemetry, "Store", return_value=store),
        patch.object(telemetry, "async_get_clientsession", return_value=session),
        patch.object(
            telemetry, "async_get_integration", AsyncMock(return_value=integration)
        ),
    ]
    for p in patches:
        p.start()
    return telemetry.TelemetryReporter(hass), store, session, patches


def test_send_does_nothing_when_nobody_opted_in():
    reporter, store, session, patches = _reporter([_entry(share=False)])
    try:
        asyncio.run(reporter.async_send())
    finally:
        for p in patches:
            p.stop()
    session.post.assert_not_called()
    store.async_load.assert_not_called()


def _send(entries, store_data=None):
    reporter, store, session, patches = _reporter(entries, store_data)
    try:
        asyncio.run(reporter.async_send())
    finally:
        for p in patches:
            p.stop()
    return store, session


def test_send_posts_report_with_timeout():
    _, session = _send([_entry()])
    session.post.assert_called_once()
    args, kwargs = session.post.call_args
    assert args[0] == "https://ha-bacnet.telemetry.brdc.nl/v1/report"
    assert set(kwargs["json"]) == {
        "schema",
        "install_id",
        "integration_version",
        "ha_version",
        "devices",
    }
    assert kwargs["json"]["integration_version"] == "1.0.50-beta.10"
    assert len(kwargs["json"]["devices"]) == 1
    assert kwargs["timeout"].total == 10


@pytest.mark.parametrize("error", [aiohttp.ClientError("down"), TimeoutError()])
def test_send_swallows_errors_at_debug(error, caplog):
    # A telemetry server that is down must never show up in the user's log.
    reporter, _, session, patches = _reporter([_entry()])
    session.post.side_effect = error
    try:
        with caplog.at_level(logging.DEBUG):
            asyncio.run(reporter.async_send())
    finally:
        for p in patches:
            p.stop()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_rejected_report_is_logged_at_debug_only(caplog):
    # A 400 from the server's strict validator must be findable in a debug log.
    reporter, _, session, patches = _reporter([_entry()])
    session.post.return_value.__aenter__.return_value.status = 400
    try:
        with caplog.at_level(logging.DEBUG):
            asyncio.run(reporter.async_send())
    finally:
        for p in patches:
            p.stop()
    session.post.return_value.__aexit__.assert_awaited_once()
    assert any("400" in r.getMessage() for r in caplog.records)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_opted_in_without_polled_devices_sends_empty_list():
    # The server still counts the installation as seen.
    _, session = _send([_entry(polled=False)])
    assert session.post.call_args.kwargs["json"]["devices"] == []


def test_install_id_generated_saved_and_reused():
    stored = str(uuid.uuid4())
    store, session = _send([_entry()], {"install_id": stored})
    assert session.post.call_args.kwargs["json"]["install_id"] == stored
    store.async_save.assert_not_called()

    for broken in (None, {"install_id": "junk"}, ["x"]):
        store, session = _send([_entry()], broken)
        new = session.post.call_args.kwargs["json"]["install_id"]
        assert str(uuid.UUID(new)) == new
        store.async_save.assert_awaited_once_with({"install_id": new})

    reporter, store, session, patches = _reporter([_entry()])
    store.async_load.side_effect = ValueError("corrupt")
    try:
        asyncio.run(reporter.async_send())
    finally:
        for p in patches:
            p.stop()
    new = session.post.call_args.kwargs["json"]["install_id"]
    store.async_save.assert_awaited_once_with({"install_id": new})


def test_start_registers_and_stop_cancels_timers():
    reporter, _, _, patches = _reporter([])
    first, daily = MagicMock(), MagicMock()
    try:
        with (
            patch.object(telemetry, "async_call_later", return_value=first) as later,
            patch.object(
                telemetry, "async_track_time_interval", return_value=daily
            ) as interval,
        ):
            reporter.async_start()
    finally:
        for p in patches:
            p.stop()
    assert later.call_args.args[1] == 600
    assert interval.call_args.args[2] == timedelta(hours=24)
    reporter._hass.bus.async_listen_once.assert_called_once()
    reporter.async_stop()
    first.assert_called_once()
    daily.assert_called_once()


def test_start_and_stop_run_in_the_event_loop():
    # Without @callback HA runs the stop listener in a worker thread, where
    # cancelling the loop's timers isn't thread-safe.
    assert getattr(telemetry.TelemetryReporter.async_stop, "_hass_callback", False)
    assert getattr(telemetry.TelemetryReporter.async_start, "_hass_callback", False)


def test_clean_text_strips_lone_surrogates():
    # The server rejects them; a broken decoder must not sink the whole report.
    assert clean_text("PXC\ud800200", 128) == "PXC200"


def _ask(entries, store_data=None):
    """Run async_ask_once; return the faked issue registry and store."""
    reporter, store, _, patches = _reporter(entries, store_data)
    issues = MagicMock()
    patches.append(patch.object(telemetry, "ir", issues))
    patches[-1].start()
    try:
        asyncio.run(reporter.async_ask_once())
    finally:
        for p in patches:
            p.stop()
    return issues, store


def test_existing_install_that_never_chose_is_asked_once():
    issues, _ = _ask([_entry(share=False)])
    issues.async_create_issue.assert_called_once()
    assert issues.async_create_issue.call_args.kwargs["is_fixable"] is True

    issues, _ = _ask([_entry(share=False)], {"asked": True})
    issues.async_create_issue.assert_not_called()

    issues, _ = _ask([])
    issues.async_create_issue.assert_not_called()


def test_opted_in_install_is_not_asked_and_question_disappears():
    issues, _ = _ask([_entry(share=False), _entry()])
    issues.async_create_issue.assert_not_called()
    issues.async_delete_issue.assert_called_once()


def test_mark_asked_keeps_install_id_and_saves_once():
    stored = str(uuid.uuid4())
    reporter, store, _, patches = _reporter([], {"install_id": stored})
    patches.append(patch.object(telemetry, "ir", MagicMock()))
    patches[-1].start()
    try:
        asyncio.run(reporter.async_mark_asked())
        asyncio.run(reporter.async_mark_asked())
    finally:
        for p in patches:
            p.stop()
    store.async_save.assert_awaited_once_with({"install_id": stored, "asked": True})
