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
from unittest.mock import MagicMock

from custom_components.bacnet.const import (
    CONF_CLIMATE_TEMPERATURE_SOURCES,
    CONF_COV_OVERRIDES,
    CONF_CUSTOMIZE_OBJECTS,
    CONF_DOMAIN_MAPPING,
    CONF_ENABLE_COV,
    CONF_SELECTED_OBJECTS,
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
