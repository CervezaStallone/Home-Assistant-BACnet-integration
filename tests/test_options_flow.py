"""Tests for the options flow domain-mapping step.

Regression: saving the form used to persist an override for EVERY object,
seeded from the type-only DEFAULT_DOMAIN_MAP. That turned non-commandable
Binary Values into switches and froze every object's COV setting, so the
device-wide enable_cov toggle stopped having any effect.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.bacnet.const import (
    CONF_COV_OVERRIDES,
    CONF_DOMAIN_MAPPING,
    CONF_ENABLE_COV,
    CONF_SELECTED_OBJECTS,
)
from custom_components.bacnet.options_flow import BACnetOptionsFlow

_BV_NON_COMMANDABLE = {"object_type": 5, "instance": 1, "commandable": False}
_BV_COMMANDABLE = {"object_type": 5, "instance": 2, "commandable": True}
_AI = {"object_type": 0, "instance": 3}


def _flow(objects, options=None, enable_cov=True):
    entry = MagicMock()
    entry.data = {CONF_SELECTED_OBJECTS: objects}
    entry.options = options or {}
    flow = BACnetOptionsFlow(entry)
    flow._options_so_far = {**entry.options, CONF_ENABLE_COV: enable_cov}
    flow.async_create_entry = lambda title, data: data
    return flow


def _form_defaults(flow):
    """Return {field_key: default} for the rendered domain_mapping form."""
    captured = {}

    def _show_form(step_id, data_schema, errors):
        captured.update(data_schema)

    flow.async_show_form = _show_form
    import custom_components.bacnet.options_flow as of

    real_optional = of.vol.Optional
    of.vol.Optional = lambda key, default=None, **_: (key, default)
    try:
        import asyncio

        asyncio.run(flow.async_step_domain_mapping())
    finally:
        of.vol.Optional = real_optional
    return dict(captured.keys())


def _submit(flow, user_input):
    import asyncio

    return asyncio.run(flow.async_step_domain_mapping(user_input))


class TestDomainMappingDefaults:
    def test_non_commandable_bv_defaults_to_binary_sensor(self):
        flow = _flow([_BV_NON_COMMANDABLE])
        assert _form_defaults(flow)["domain_5:1"] == "binary_sensor"

    def test_commandable_bv_defaults_to_switch(self):
        flow = _flow([_BV_COMMANDABLE])
        assert _form_defaults(flow)["domain_5:2"] == "switch"


class TestDomainMappingSubmit:
    @pytest.mark.parametrize(
        ("obj", "default_domain"),
        [(_BV_NON_COMMANDABLE, "binary_sensor"), (_BV_COMMANDABLE, "switch")],
    )
    def test_unchanged_form_stores_no_overrides(self, obj, default_domain):
        key = f"{obj['object_type']}:{obj['instance']}"
        flow = _flow([obj])
        result = _submit(flow, {f"domain_{key}": default_domain, f"cov_{key}": True})
        assert result[CONF_DOMAIN_MAPPING] == {}
        assert result[CONF_COV_OVERRIDES] == {}

    def test_changed_domain_is_stored(self):
        flow = _flow([_AI])
        result = _submit(flow, {"domain_0:3": "number", "cov_0:3": True})
        assert result[CONF_DOMAIN_MAPPING] == {"0:3": "number"}

    def test_cov_differing_from_global_is_stored(self):
        flow = _flow([_AI], enable_cov=True)
        result = _submit(flow, {"domain_0:3": "sensor", "cov_0:3": False})
        assert result[CONF_COV_OVERRIDES] == {"0:3": False}

    def test_resetting_to_default_drops_existing_override(self):
        flow = _flow(
            [_AI],
            options={
                CONF_DOMAIN_MAPPING: {"0:3": "number"},
                CONF_COV_OVERRIDES: {"0:3": False},
            },
        )
        result = _submit(flow, {"domain_0:3": "sensor", "cov_0:3": True})
        assert result[CONF_DOMAIN_MAPPING] == {}
        assert result[CONF_COV_OVERRIDES] == {}

    def test_overrides_for_unselected_objects_are_kept(self):
        """Issue #30: removed objects keep their settings for re-selection."""
        flow = _flow(
            [_AI],
            options={
                CONF_DOMAIN_MAPPING: {"2:9": "number"},
                CONF_COV_OVERRIDES: {"2:9": False},
            },
        )
        result = _submit(flow, {"domain_0:3": "sensor", "cov_0:3": True})
        assert result[CONF_DOMAIN_MAPPING] == {"2:9": "number"}
        assert result[CONF_COV_OVERRIDES] == {"2:9": False}
