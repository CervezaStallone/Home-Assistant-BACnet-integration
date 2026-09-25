"""Tests for the reconfigure flow (change network settings in place)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from custom_components.bacnet.config_flow import BACnetConfigFlow

_DATA = {
    "local_ip": "192.168.1.10/24",
    "local_port": 47808,
    "use_bbmd": False,
    "bbmd_address": "",
    "bbmd_ttl": 900,
    "device_address": "192.168.1.50",
    "device_id": 1001,
    "selected_objects": [{"object_type": 0, "instance": 1}],
}


def _flow():
    flow = BACnetConfigFlow()
    entry = MagicMock()
    entry.data = dict(_DATA)
    flow.hass = MagicMock()
    flow.hass.config_entries.async_get_entry.return_value = entry
    flow.context = {"entry_id": "e1", "source": "reconfigure"}
    flow.async_show_form = lambda **kw: {"type": "form", **kw}
    flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort"})
    return flow, entry


def _submit(**changes):
    flow, entry = _flow()
    user_input = {
        k: v for k, v in _DATA.items() if k not in ("device_id", "selected_objects")
    }
    user_input.update(changes)
    result = asyncio.run(flow.async_step_reconfigure(user_input))
    return flow, entry, result


class TestReconfigure:
    def test_form_is_prefilled_from_entry(self):
        flow, _ = _flow()
        result = asyncio.run(flow.async_step_reconfigure())
        assert result["step_id"] == "reconfigure"

    def test_valid_change_updates_and_reloads(self):
        flow, entry, result = _submit(device_address="192.168.1.77", local_port=47809)
        assert result == {"type": "abort"}
        args, kwargs = flow.async_update_reload_and_abort.call_args
        assert args[0] is entry
        assert kwargs["data"]["device_address"] == "192.168.1.77"
        assert kwargs["data"]["local_port"] == 47809
        # Everything not on the form is kept.
        assert kwargs["data"]["selected_objects"] == _DATA["selected_objects"]
        assert kwargs["reason"] == "reconfigure_successful"

    def test_invalid_local_ip_shows_error(self):
        flow, _, result = _submit(local_ip="not-an-ip")
        assert result["errors"] == {"base": "invalid_ip"}
        flow.async_update_reload_and_abort.assert_not_called()

    def test_bbmd_enabled_requires_valid_address(self):
        _, _, result = _submit(use_bbmd=True, bbmd_address="")
        assert result["errors"] == {"bbmd_address": "invalid_ip"}

    def test_invalid_device_address_shows_error(self):
        _, _, result = _submit(device_address="::::")
        assert result["errors"] == {"device_address": "invalid_ip"}
