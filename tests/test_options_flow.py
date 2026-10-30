"""Tests for the options flow's per-object customisation steps.

Step domain_mapping picks which objects to customise (by readable label);
step customize_objects shows type / COV (/ climate temperature sensor)
fields for just those objects.

Regressions guarded here: saving the form used to persist an override for
EVERY object, seeded from the type-only DEFAULT_DOMAIN_MAP, turning
non-commandable Binary Values into switches and freezing every object's COV
setting. Only values that differ from the default may be stored.
"""

from __future__ import annotations

import asyncio
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

from custom_components.bacnet.const import (
    CONF_CLIMATE_TEMPERATURE_SOURCES,
    CONF_COV_OVERRIDES,
    CONF_CUSTOMIZE_OBJECTS,
    CONF_DOMAIN_MAPPING,
    CONF_ENABLE_COV,
    CONF_KEPT_DOMAIN_OVERRIDES,
    CONF_SELECTED_OBJECTS,
    CONF_STATE_CLASSES,
    STATE_CLASS_CHOICES,
)
from custom_components.bacnet.helpers import object_label
from custom_components.bacnet.options_flow import BACnetOptionsFlow

_BV_NON_COMMANDABLE = {
    "object_type": 5,
    "instance": 1,
    "commandable": False,
    "object_name": "Alarm",
}
_BV_COMMANDABLE = {
    "object_type": 5,
    "instance": 2,
    "commandable": True,
    "object_name": "Fan",
}
_AI = {"object_type": 0, "instance": 3, "object_name": "Room"}
_AV_SP = {"object_type": 2, "instance": 4, "commandable": True, "object_name": "SP"}


def _flow(objects, options=None, enable_cov=True):
    entry = MagicMock()
    entry.data = {CONF_SELECTED_OBJECTS: objects}
    entry.options = options or {}
    flow = BACnetOptionsFlow(entry)
    flow._options_so_far = {**entry.options, CONF_ENABLE_COV: enable_cov}
    flow.async_create_entry = lambda title, data: {"type": "create_entry", "data": data}
    flow.async_show_form = lambda step_id, data_schema, **kw: {
        "type": "form",
        "step_id": step_id,
        "fields": {k.key: k.default for k in data_schema},
        "validators": {k.key: v for k, v in data_schema.items()},
    }
    return flow


class _Opt:
    """Hashable stand-in for vol.Optional that remembers its default."""

    def __init__(self, key, default=None, **_):
        self.key, self.default = key, default

    def __hash__(self):
        return hash(self.key)


def _run(coro, monkeypatch):
    """Run a flow step with vol.Optional returning (key, default) pairs."""
    import custom_components.bacnet.options_flow as of

    monkeypatch.setattr(of.vol, "Optional", _Opt)
    return asyncio.run(coro)


def _field(obj, what):
    return f"{object_label(obj)} — {what}"


class TestPickObjects:
    def test_objects_with_overrides_are_preselected(self, monkeypatch):
        flow = _flow(
            [_AI, _BV_COMMANDABLE], options={CONF_DOMAIN_MAPPING: {"0:3": "number"}}
        )
        result = _run(flow.async_step_domain_mapping(), monkeypatch)
        assert result["fields"][CONF_CUSTOMIZE_OBJECTS] == ["0:3"]

    def test_picking_nothing_resets_selected_but_keeps_removed_leftovers(
        self, monkeypatch
    ):
        """Issue #30: removed objects keep their settings for re-selection."""
        flow = _flow(
            [_AI],
            options={
                CONF_DOMAIN_MAPPING: {"0:3": "number", "2:9": "number"},
                CONF_COV_OVERRIDES: {"0:3": False, "2:9": False},
            },
        )
        result = _run(
            flow.async_step_domain_mapping({CONF_CUSTOMIZE_OBJECTS: []}), monkeypatch
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_DOMAIN_MAPPING] == {"2:9": "number"}
        assert result["data"][CONF_COV_OVERRIDES] == {"2:9": False}

    def test_picking_objects_shows_labelled_fields_for_them_only(self, monkeypatch):
        flow = _flow([_AI, _BV_NON_COMMANDABLE])
        result = _run(
            flow.async_step_domain_mapping({CONF_CUSTOMIZE_OBJECTS: ["5:1"]}),
            monkeypatch,
        )
        assert result["step_id"] == "customize_objects"
        assert _field(_BV_NON_COMMANDABLE, "type") in result["fields"]
        assert _field(_AI, "type") not in result["fields"]


class TestCustomizeObjects:
    def _customize(self, monkeypatch, objects, pick, options=None, enable_cov=True):
        flow = _flow(objects, options=options, enable_cov=enable_cov)
        form = _run(
            flow.async_step_domain_mapping({CONF_CUSTOMIZE_OBJECTS: pick}), monkeypatch
        )
        return flow, form

    def test_defaults_are_commandable_aware(self, monkeypatch):
        _, form = self._customize(
            monkeypatch, [_BV_NON_COMMANDABLE, _BV_COMMANDABLE], ["5:1", "5:2"]
        )
        assert form["fields"][_field(_BV_NON_COMMANDABLE, "type")] == "binary_sensor"
        assert form["fields"][_field(_BV_COMMANDABLE, "type")] == "switch"

    def test_unchanged_form_stores_no_overrides(self, monkeypatch):
        flow, _ = self._customize(monkeypatch, [_BV_NON_COMMANDABLE], ["5:1"])
        result = _run(
            flow.async_step_customize_objects(
                {
                    _field(_BV_NON_COMMANDABLE, "type"): "binary_sensor",
                    _field(_BV_NON_COMMANDABLE, "COV"): True,
                }
            ),
            monkeypatch,
        )
        assert result["data"][CONF_DOMAIN_MAPPING] == {}
        assert result["data"][CONF_COV_OVERRIDES] == {}

    def test_changes_are_stored(self, monkeypatch):
        flow, _ = self._customize(monkeypatch, [_AI], ["0:3"])
        result = _run(
            flow.async_step_customize_objects(
                {_field(_AI, "type"): "number", _field(_AI, "COV"): False}
            ),
            monkeypatch,
        )
        assert result["data"][CONF_DOMAIN_MAPPING] == {"0:3": "number"}
        assert result["data"][CONF_COV_OVERRIDES] == {"0:3": False}

    def test_chosen_type_is_remembered_as_deliberate(self, monkeypatch):
        # Issue #97: "set it again in the options" must stop the repair issue
        flow, _ = self._customize(
            monkeypatch,
            [_BV_NON_COMMANDABLE, _AI],
            ["5:1", "0:3"],
            options={CONF_KEPT_DOMAIN_OVERRIDES: ["2:9", "0:3"]},
        )
        result = _run(
            flow.async_step_customize_objects(
                {
                    _field(_BV_NON_COMMANDABLE, "type"): "switch",
                    _field(_AI, "type"): "sensor",
                }
            ),
            monkeypatch,
        )
        assert result["data"][CONF_DOMAIN_MAPPING] == {"5:1": "switch"}
        # removed object 2:9 keeps its mark; 0:3 is back on its default
        assert result["data"][CONF_KEPT_DOMAIN_OVERRIDES] == ["2:9", "5:1"]


class TestClimateTemperatureSource:
    _OPTIONS: ClassVar[dict] = {CONF_DOMAIN_MAPPING: {"2:4": "climate"}}

    def test_field_only_for_climate_objects(self, monkeypatch):
        flow = _flow([_AI, _AV_SP], options=self._OPTIONS)
        form = _run(
            flow.async_step_domain_mapping({CONF_CUSTOMIZE_OBJECTS: ["0:3", "2:4"]}),
            monkeypatch,
        )
        assert _field(_AV_SP, "temperature sensor") in form["fields"]
        assert _field(_AI, "temperature sensor") not in form["fields"]

    def test_source_is_stored_and_cleared(self, monkeypatch):
        flow = _flow([_AI, _AV_SP], options=self._OPTIONS)
        _run(
            flow.async_step_domain_mapping({CONF_CUSTOMIZE_OBJECTS: ["2:4"]}),
            monkeypatch,
        )
        result = _run(
            flow.async_step_customize_objects(
                {
                    _field(_AV_SP, "type"): "climate",
                    _field(_AV_SP, "COV"): True,
                    _field(_AV_SP, "temperature sensor"): "0:3",
                }
            ),
            monkeypatch,
        )
        assert result["data"][CONF_CLIMATE_TEMPERATURE_SOURCES] == {"2:4": "0:3"}

        flow = _flow([_AI, _AV_SP], options={**self._OPTIONS, **result["data"]})
        _run(
            flow.async_step_domain_mapping({CONF_CUSTOMIZE_OBJECTS: ["2:4"]}),
            monkeypatch,
        )
        result = _run(
            flow.async_step_customize_objects(
                {
                    _field(_AV_SP, "type"): "climate",
                    _field(_AV_SP, "COV"): True,
                    _field(_AV_SP, "temperature sensor"): "",
                }
            ),
            monkeypatch,
        )
        assert result["data"][CONF_CLIMATE_TEMPERATURE_SOURCES] == {}


class TestStateClass:
    """Issue #137: tank levels and snapshot totals aren't meters."""

    def test_field_only_for_analog_sensors(self, monkeypatch):
        flow = _flow([_AI, _AV_SP, _BV_NON_COMMANDABLE])
        form = _run(
            flow.async_step_domain_mapping(
                {CONF_CUSTOMIZE_OBJECTS: ["0:3", "2:4", "5:1"]}
            ),
            monkeypatch,
        )
        assert form["fields"][_field(_AI, "state class")] == "auto"
        assert _field(_AV_SP, "state class") not in form["fields"]  # number
        assert _field(_BV_NON_COMMANDABLE, "state class") not in form["fields"]

    def test_choices_are_translated_labels(self, monkeypatch):
        flow = _flow([_AI])
        form = _run(
            flow.async_step_domain_mapping({CONF_CUSTOMIZE_OBJECTS: ["0:3"]}),
            monkeypatch,
        )
        kind, config = form["validators"][_field(_AI, "state class")]
        assert kind == "select"
        assert config["translation_key"] == "state_class"
        assert config["options"] == STATE_CLASS_CHOICES

    def test_choice_is_stored_and_auto_clears_it(self, monkeypatch):
        flow = _flow([_AI])
        _run(
            flow.async_step_domain_mapping({CONF_CUSTOMIZE_OBJECTS: ["0:3"]}),
            monkeypatch,
        )
        result = _run(
            flow.async_step_customize_objects(
                {_field(_AI, "state class"): "measurement"}
            ),
            monkeypatch,
        )
        assert result["data"][CONF_STATE_CLASSES] == {"0:3": "measurement"}

        flow = _flow([_AI], options=result["data"])
        form = _run(flow.async_step_domain_mapping(), monkeypatch)
        assert form["fields"][CONF_CUSTOMIZE_OBJECTS] == ["0:3"]
        _run(
            flow.async_step_domain_mapping({CONF_CUSTOMIZE_OBJECTS: ["0:3"]}),
            monkeypatch,
        )
        result = _run(
            flow.async_step_customize_objects({_field(_AI, "state class"): "auto"}),
            monkeypatch,
        )
        assert result["data"][CONF_STATE_CLASSES] == {}


_NEW_AI = {"object_type": 0, "instance": 9, "object_name": "New"}
_NEW_BO = {"object_type": 4, "instance": 7, "object_name": "Pump"}


def _rescan(monkeypatch, user_input=None, device=None):
    """Run the rescan step on an entry with _AI and _BV_COMMANDABLE imported."""
    from custom_components.bacnet import options_flow as of

    imported = [_AI, _BV_COMMANDABLE]
    flow = _flow(imported)
    flow.hass = MagicMock()
    flow._config_entry.data = {
        CONF_SELECTED_OBJECTS: imported,
        "device_address": "1.2.3.4",
        "device_id": 1,
    }
    found = device or [_AI, _BV_COMMANDABLE, _NEW_AI, _NEW_BO]

    async def _read_object_list(**_):
        return found

    flow._config_entry.runtime_data.coordinator.client.read_object_list = (
        _read_object_list
    )
    monkeypatch.setattr(of.cv, "multi_select", lambda options: ("multi", options))
    shown = {}
    flow.async_show_form = lambda **kw: shown.update(kw) or {"type": "form"}
    flow.async_step_domain_mapping = AsyncMock(return_value={"type": "next"})
    _run(flow.async_step_rescan_objects(), monkeypatch)
    if user_input is not None:
        shown.clear()
        _run(flow.async_step_rescan_objects(user_input), monkeypatch)
    return flow, shown


def _saved(flow):
    call = flow.hass.config_entries.async_update_entry.call_args
    return [
        (o["object_type"], o["instance"])
        for o in call.kwargs["data"][CONF_SELECTED_OBJECTS]
    ]


def test_rescan_offers_add_types_and_remove_with_nothing_preselected(monkeypatch):
    """Issue #90: a rescan on a big device must not mean hundreds of checkboxes."""
    _, shown = _rescan(monkeypatch)
    fields = {k.key: (k.default, v) for k, v in shown["data_schema"].items()}

    default, (kind, config) = fields["add_objects"]
    assert default == []
    assert kind == "select" and config["multiple"] is True
    assert {o["value"]: o["label"] for o in config["options"]} == {
        "0:9": object_label(_NEW_AI, new=True),
        "4:7": object_label(_NEW_BO, new=True),
    }

    default, (kind, options) = fields["select_types"]
    assert default == []
    assert options == {"0": "Analog Input (1)", "4": "Binary Output (1)"}

    default, (kind, config) = fields["remove_objects"]
    assert default == []
    assert [o["value"] for o in config["options"]] == ["0:3", "5:2"]


def test_rescan_submit_without_changes_keeps_the_selection(monkeypatch):
    flow, _ = _rescan(monkeypatch, {})
    assert _saved(flow) == [(0, 3), (5, 2)]


def test_rescan_adds_picked_objects_and_types_and_removes(monkeypatch):
    flow, _ = _rescan(
        monkeypatch,
        {"add_objects": ["0:9"], "select_types": ["4"], "remove_objects": ["5:2"]},
    )
    assert _saved(flow) == [(0, 3), (0, 9), (4, 7)]


def test_rescan_drops_objects_the_device_no_longer_has(monkeypatch):
    flow, _ = _rescan(monkeypatch, {}, device=[_AI, _NEW_AI])
    assert _saved(flow) == [(0, 3)]


def test_rescan_removing_everything_says_none_selected(monkeypatch):
    flow, shown = _rescan(monkeypatch, {"remove_objects": ["0:3", "5:2"]})
    flow.hass.config_entries.async_update_entry.assert_not_called()
    assert shown["errors"] == {"base": "no_objects_selected"}


def test_customize_picker_is_searchable(monkeypatch):
    flow = _flow([_AI], options={CONF_DOMAIN_MAPPING: {"0:3": "number"}})
    shown = {}
    flow.async_show_form = lambda **kw: shown.update(kw)
    _run(flow.async_step_domain_mapping(), monkeypatch)
    ((key, (kind, config)),) = shown["data_schema"].items()
    assert key.default == ["0:3"]
    assert kind == "select" and config["mode"] == "dropdown"


def test_customize_stores_extra_properties(monkeypatch):
    """Reddit feedback: extra properties are picked here, typed ones normalized."""
    from custom_components.bacnet.const import CONF_EXTRA_PROPERTIES

    flow = _flow([_BV_COMMANDABLE])
    _run(flow.async_step_domain_mapping({CONF_CUSTOMIZE_OBJECTS: ["5:2"]}), monkeypatch)
    result = _run(
        flow.async_step_customize_objects(
            {
                _field(_BV_COMMANDABLE, "type"): "switch",
                _field(_BV_COMMANDABLE, "COV"): True,
                _field(_BV_COMMANDABLE, "extra properties"): ["minimumOnTime"],
                _field(_BV_COMMANDABLE, "other properties"): "inactive-text, 9999",
            }
        ),
        monkeypatch,
    )
    assert result["data"][CONF_EXTRA_PROPERTIES] == {
        "5:2": ["minimumOnTime", "inactiveText", "9999"]
    }


def test_customize_unknown_property_shows_error(monkeypatch):
    flow = _flow([_BV_COMMANDABLE])
    errors = {}
    _run(flow.async_step_domain_mapping({CONF_CUSTOMIZE_OBJECTS: ["5:2"]}), monkeypatch)
    flow.async_show_form = lambda step_id, data_schema, **kw: (
        errors.update(kw["errors"]) or {"type": "form"}
    )
    result = _run(
        flow.async_step_customize_objects(
            {_field(_BV_COMMANDABLE, "other properties"): "bogusProperty"}
        ),
        monkeypatch,
    )
    assert result["type"] == "form"
    assert errors == {"base": "unknown_property"}


def test_options_settings_show_share_usage_with_current_value(monkeypatch):
    # Usage statistics stay off unless the user ticked them.
    on = _run(
        _flow([], options={"share_usage": True}).async_step_settings(), monkeypatch
    )
    off = _run(_flow([]).async_step_settings(), monkeypatch)
    assert on["fields"]["share_usage"] is True
    assert off["fields"]["share_usage"] is False


def test_options_settings_link_the_privacy_policy(monkeypatch):
    # The share_usage description links {privacy_url}; without the
    # placeholder the frontend shows the raw text.
    from pathlib import Path

    from custom_components.bacnet.const import PRIVACY_URL

    flow = _flow([])
    shown = {}
    flow.async_show_form = lambda **kw: shown.update(kw)
    _run(flow.async_step_settings(), monkeypatch)
    assert shown["description_placeholders"] == {"privacy_url": PRIVACY_URL}
    assert (Path(__file__).parents[1] / PRIVACY_URL.rsplit("/", 1)[1]).is_file()


def test_options_open_with_a_menu(monkeypatch):
    """Issue #137 feedback: each part of the options is one click away."""
    flow = _flow([_AI])
    flow.async_show_menu = lambda step_id, menu_options: {
        "type": "menu",
        "step_id": step_id,
        "menu_options": menu_options,
    }
    result = _run(flow.async_step_init(), monkeypatch)
    assert result["type"] == "menu"
    assert result["menu_options"] == ["settings", "rescan_objects", "domain_mapping"]


def test_settings_save_without_the_per_object_steps(monkeypatch):
    flow = _flow([_AI], options={CONF_DOMAIN_MAPPING: {"0:3": "number"}})
    form = _run(flow.async_step_settings(), monkeypatch)
    assert "rescan_objects" not in form["fields"]
    result = _run(flow.async_step_settings({"polling_interval": 60}), monkeypatch)
    assert result["type"] == "create_entry"
    assert result["data"]["polling_interval"] == 60
    assert result["data"][CONF_DOMAIN_MAPPING] == {"0:3": "number"}


def test_customize_from_the_menu_keeps_the_settings(monkeypatch):
    entry = MagicMock()
    entry.data = {CONF_SELECTED_OBJECTS: [_AI]}
    entry.options = {"polling_interval": 60, CONF_ENABLE_COV: False}
    flow = BACnetOptionsFlow(entry)
    flow.async_create_entry = lambda title, data: {"type": "create_entry", "data": data}
    result = _run(
        flow.async_step_domain_mapping({CONF_CUSTOMIZE_OBJECTS: []}), monkeypatch
    )
    assert result["data"]["polling_interval"] == 60
    assert result["data"][CONF_ENABLE_COV] is False
