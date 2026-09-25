"""Tests for the stale domain-override repair (options-flow bug cleanup).

Before the options-flow fix, saving options stored the type-only default
from DEFAULT_DOMAIN_MAP as an explicit override for every object. For
non-commandable Value objects that override is a writable domain the
device rejects writes on. The repair lets the user drop those overrides.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from custom_components.bacnet.const import CONF_DOMAIN_MAPPING, CONF_SELECTED_OBJECTS
from custom_components.bacnet.helpers import stale_domain_overrides

_BV_NON_COMMANDABLE = {"object_type": 5, "instance": 1, "commandable": False}
_BV_COMMANDABLE = {"object_type": 5, "instance": 2, "commandable": True}
_AV_NON_COMMANDABLE = {"object_type": 2, "instance": 3, "commandable": False}


class TestStaleDomainOverrides:
    def test_bug_signature_is_detected(self):
        # BV → switch is the type-only default, but non-commandable wants binary_sensor
        assert stale_domain_overrides([_BV_NON_COMMANDABLE], {"5:1": "switch"}) == [
            "5:1"
        ]

    def test_commandable_av_stuck_on_sensor_is_stale(self):
        av = {"object_type": 2, "instance": 4, "commandable": True}
        assert stale_domain_overrides([av], {"2:4": "sensor"}) == ["2:4"]

    def test_override_matching_real_default_is_not_stale(self):
        assert stale_domain_overrides([_BV_COMMANDABLE], {"5:2": "switch"}) == []

    def test_deliberate_non_default_choice_is_not_stale(self):
        # AV type-default is "sensor"; "climate" can only be a user choice
        assert stale_domain_overrides([_AV_NON_COMMANDABLE], {"2:3": "climate"}) == []

    def test_unselected_objects_are_ignored(self):
        assert stale_domain_overrides([], {"5:1": "switch"}) == []


def _repair_flow(options):
    from custom_components.bacnet.repairs import async_create_fix_flow

    entry = MagicMock()
    entry.data = {CONF_SELECTED_OBJECTS: [_BV_NON_COMMANDABLE, _AV_NON_COMMANDABLE]}
    entry.options = options
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = entry
    flow = asyncio.run(async_create_fix_flow(hass, "issue", {"entry_id": "abc"}))
    flow.hass = hass
    return flow, hass, entry


class TestRepairFlow:
    def test_init_shows_confirm_form(self):
        flow, _, _ = _repair_flow({CONF_DOMAIN_MAPPING: {"5:1": "switch"}})
        assert asyncio.run(flow.async_step_init())["step_id"] == "confirm"

    def test_confirm_removes_only_stale_overrides(self):
        flow, hass, entry = _repair_flow(
            {CONF_DOMAIN_MAPPING: {"5:1": "switch", "2:3": "climate"}, "x": 1}
        )
        result = asyncio.run(flow.async_step_confirm({}))
        assert result["type"] == "create_entry"
        hass.config_entries.async_update_entry.assert_called_once_with(
            entry, options={CONF_DOMAIN_MAPPING: {"2:3": "climate"}, "x": 1}
        )

    def test_confirm_when_entry_is_gone_just_finishes(self):
        flow, hass, _ = _repair_flow({})
        hass.config_entries.async_get_entry.return_value = None
        assert asyncio.run(flow.async_step_confirm({}))["type"] == "create_entry"


class TestIssueLifecycle:
    def _run(self, overrides):
        import custom_components.bacnet as integration

        ir = MagicMock()
        entry = MagicMock()
        entry.entry_id = "abc"
        entry.data = {"device_name": "AHU-1"}
        original = integration.ir
        integration.ir = ir
        try:
            integration._async_check_stale_overrides(
                MagicMock(), entry, [_BV_NON_COMMANDABLE], overrides
            )
        finally:
            integration.ir = original
        return ir

    def test_issue_created_when_stale_overrides_exist(self):
        ir = self._run({"5:1": "switch"})
        ir.async_create_issue.assert_called_once()
        kwargs = ir.async_create_issue.call_args.kwargs
        assert kwargs["is_fixable"] is True
        assert kwargs["data"] == {"entry_id": "abc"}
        assert kwargs["translation_placeholders"]["device"] == "AHU-1"
        ir.async_delete_issue.assert_not_called()

    def test_issue_deleted_when_clean(self):
        ir = self._run({})
        ir.async_create_issue.assert_not_called()
        ir.async_delete_issue.assert_called_once()
