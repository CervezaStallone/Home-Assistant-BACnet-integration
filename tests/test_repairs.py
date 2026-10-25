"""Tests for the stale domain-override repair (options-flow bug cleanup).

Before the options-flow fix, saving options stored the type-only default
from DEFAULT_DOMAIN_MAP as an explicit override for every object. For
non-commandable Value objects that override is a writable domain the
device rejects writes on. The repair lets the user drop those overrides.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from custom_components.bacnet.const import (
    CONF_DOMAIN_MAPPING,
    CONF_KEPT_DOMAIN_OVERRIDES,
    CONF_SELECTED_OBJECTS,
)
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

    def test_kept_overrides_are_not_stale(self):
        # Issue #97: a BV without priorityArray that still accepts writes
        assert (
            stale_domain_overrides([_BV_NON_COMMANDABLE], {"5:1": "switch"}, ["5:1"])
            == []
        )


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
    def test_init_offers_remove_or_keep(self):
        flow, _, _ = _repair_flow({CONF_DOMAIN_MAPPING: {"5:1": "switch"}})
        menu = asyncio.run(flow.async_step_init())
        assert menu["step_id"] == "confirm"
        assert menu["menu_options"] == ["remove", "keep"]

    def test_keep_remembers_the_overrides(self):
        # Issue #97: the issue came back on every start for deliberate choices
        flow, hass, entry = _repair_flow(
            {
                CONF_DOMAIN_MAPPING: {"5:1": "switch"},
                CONF_KEPT_DOMAIN_OVERRIDES: ["9:9"],
            }
        )
        result = asyncio.run(flow.async_step_keep())
        assert result["type"] == "create_entry"
        hass.config_entries.async_update_entry.assert_called_once_with(
            entry,
            options={
                CONF_DOMAIN_MAPPING: {"5:1": "switch"},
                CONF_KEPT_DOMAIN_OVERRIDES: ["9:9", "5:1"],
            },
        )

    def test_remove_drops_only_stale_overrides(self):
        flow, hass, entry = _repair_flow(
            {CONF_DOMAIN_MAPPING: {"5:1": "switch", "2:3": "climate"}, "x": 1}
        )
        result = asyncio.run(flow.async_step_remove())
        assert result["type"] == "create_entry"
        hass.config_entries.async_update_entry.assert_called_once_with(
            entry, options={CONF_DOMAIN_MAPPING: {"2:3": "climate"}, "x": 1}
        )

    def test_confirm_when_entry_is_gone_just_finishes(self):
        flow, hass, _ = _repair_flow({})
        hass.config_entries.async_get_entry.return_value = None
        assert asyncio.run(flow.async_step_remove())["type"] == "create_entry"
        assert asyncio.run(flow.async_step_keep())["type"] == "create_entry"


class TestIssueLifecycle:
    def _run(self, overrides, options=None):
        import custom_components.bacnet as integration

        ir = MagicMock()
        entry = MagicMock()
        entry.entry_id = "abc"
        entry.data = {"device_name": "AHU-1"}
        entry.options = options or {}
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

    def test_no_issue_for_kept_overrides(self):
        ir = self._run({"5:1": "switch"}, {CONF_KEPT_DOMAIN_OVERRIDES: ["5:1"]})
        ir.async_create_issue.assert_not_called()
        ir.async_delete_issue.assert_called_once()

    def test_issue_deleted_when_clean(self):
        ir = self._run({})
        ir.async_create_issue.assert_not_called()
        ir.async_delete_issue.assert_called_once()


class TestShareUsageRepairFlow:
    def _flow(self):
        from unittest.mock import AsyncMock

        from custom_components.bacnet.repairs import async_create_fix_flow

        entries = [MagicMock(options={"x": 1}), MagicMock(options={})]
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = entries
        reporter = MagicMock(async_mark_asked=AsyncMock())
        hass.data = {"bacnet": {"_telemetry": reporter}}
        flow = asyncio.run(async_create_fix_flow(hass, "share_usage", None))
        flow.hass = hass
        flow.async_show_form = lambda **kw: kw
        flow.async_create_entry = lambda **kw: {"type": "create_entry"}
        return flow, hass, entries, reporter

    def test_form_is_ticked_and_links_the_privacy_policy(self, monkeypatch):
        from custom_components.bacnet import repairs
        from custom_components.bacnet.const import PRIVACY_URL

        monkeypatch.setattr(
            repairs.vol, "Optional", lambda key, default=None: (key, default)
        )
        flow, *_ = self._flow()
        form = asyncio.run(flow.async_step_init())
        assert form["description_placeholders"] == {"privacy_url": PRIVACY_URL}
        assert form["data_schema"] == {("share_usage", True): bool}

    def test_yes_ticks_every_device(self):
        flow, hass, _, reporter = self._flow()
        asyncio.run(flow.async_step_confirm({"share_usage": True}))
        assert [
            c.kwargs["options"]
            for c in hass.config_entries.async_update_entry.call_args_list
        ] == [
            {"x": 1, "share_usage": True},
            {"share_usage": True},
        ]
        reporter.async_mark_asked.assert_awaited_once()

    def test_no_changes_nothing_but_is_not_asked_again(self):
        flow, hass, _, reporter = self._flow()
        asyncio.run(flow.async_step_confirm({"share_usage": False}))
        hass.config_entries.async_update_entry.assert_not_called()
        reporter.async_mark_asked.assert_awaited_once()
