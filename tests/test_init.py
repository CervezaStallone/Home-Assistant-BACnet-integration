"""Tests for __init__._domain_for_object and _get_platforms_in_use."""

from __future__ import annotations

import pytest

from custom_components.bacnet.__init__ import _domain_for_object, _get_platforms_in_use
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


# ---------------------------------------------------------------------------
# _domain_for_object
# ---------------------------------------------------------------------------


class TestDomainForObject:
    @pytest.mark.parametrize(
        "obj_type, commandable, expected",
        [
            (OBJECT_TYPE_ANALOG_INPUT, False, "sensor"),
            (OBJECT_TYPE_ANALOG_OUTPUT, True, "number"),
            (OBJECT_TYPE_BINARY_INPUT, False, "binary_sensor"),
            (OBJECT_TYPE_BINARY_OUTPUT, True, "switch"),
            (OBJECT_TYPE_MULTI_STATE_INPUT, False, "sensor"),
            (OBJECT_TYPE_MULTI_STATE_OUTPUT, True, "number"),
            # Value types depend on commandable flag
            (OBJECT_TYPE_ANALOG_VALUE, True, "number"),
            (OBJECT_TYPE_ANALOG_VALUE, False, "sensor"),
            (OBJECT_TYPE_BINARY_VALUE, True, "switch"),
            (OBJECT_TYPE_BINARY_VALUE, False, "binary_sensor"),
            (OBJECT_TYPE_MULTI_STATE_VALUE, True, "number"),
            (OBJECT_TYPE_MULTI_STATE_VALUE, False, "sensor"),
        ],
    )
    def test_defaults(self, obj_type, commandable, expected):
        obj = {"object_type": obj_type, "instance": 1, "commandable": commandable}
        assert _domain_for_object(obj, {}) == expected

    def test_override_takes_precedence(self):
        obj = {
            "object_type": OBJECT_TYPE_BINARY_VALUE,
            "instance": 7,
            "commandable": False,
        }
        overrides = {"5:7": "climate"}
        assert _domain_for_object(obj, overrides) == "climate"

    def test_missing_commandable_flag_defaults_to_false(self):
        obj = {"object_type": OBJECT_TYPE_BINARY_VALUE, "instance": 1}
        assert _domain_for_object(obj, {}) == "binary_sensor"


# ---------------------------------------------------------------------------
# _get_platforms_in_use  (the A1 bug fix)
# ---------------------------------------------------------------------------


class TestGetPlatformsInUse:
    def _platforms(self, objects, overrides=None):
        result = _get_platforms_in_use(objects, overrides or {})
        return {p.value for p in result}

    def test_analog_input_needs_sensor(self):
        obj = {
            "object_type": OBJECT_TYPE_ANALOG_INPUT,
            "instance": 1,
            "commandable": False,
        }
        assert "sensor" in self._platforms([obj])

    def test_binary_input_needs_binary_sensor(self):
        obj = {
            "object_type": OBJECT_TYPE_BINARY_INPUT,
            "instance": 1,
            "commandable": False,
        }
        assert "binary_sensor" in self._platforms([obj])

    def test_commandable_bv_needs_switch(self):
        obj = {
            "object_type": OBJECT_TYPE_BINARY_VALUE,
            "instance": 1,
            "commandable": True,
        }
        assert "switch" in self._platforms([obj])

    def test_non_commandable_bv_needs_binary_sensor_not_switch(self):
        """A1 regression: non-commandable BV must land on binary_sensor, not switch."""
        obj = {
            "object_type": OBJECT_TYPE_BINARY_VALUE,
            "instance": 1,
            "commandable": False,
        }
        platforms = self._platforms([obj])
        assert "binary_sensor" in platforms
        assert "switch" not in platforms

    def test_non_commandable_av_needs_sensor_not_number(self):
        obj = {
            "object_type": OBJECT_TYPE_ANALOG_VALUE,
            "instance": 1,
            "commandable": False,
        }
        platforms = self._platforms([obj])
        assert "sensor" in platforms
        assert "number" not in platforms

    def test_commandable_av_needs_number(self):
        obj = {
            "object_type": OBJECT_TYPE_ANALOG_VALUE,
            "instance": 1,
            "commandable": True,
        }
        assert "number" in self._platforms([obj])

    def test_mixed_objects_returns_correct_platforms(self):
        objects = [
            {
                "object_type": OBJECT_TYPE_ANALOG_INPUT,
                "instance": 1,
                "commandable": False,
            },
            {
                "object_type": OBJECT_TYPE_BINARY_VALUE,
                "instance": 2,
                "commandable": False,
            },
            {
                "object_type": OBJECT_TYPE_ANALOG_OUTPUT,
                "instance": 3,
                "commandable": True,
            },
        ]
        platforms = self._platforms(objects)
        assert "sensor" in platforms
        assert "binary_sensor" in platforms
        assert "number" in platforms
        assert "switch" not in platforms

    def test_domain_override_respected(self):
        obj = {
            "object_type": OBJECT_TYPE_ANALOG_INPUT,
            "instance": 5,
            "commandable": False,
        }
        platforms = self._platforms([obj], {"0:5": "climate"})
        assert "climate" in platforms

    def test_empty_object_list(self):
        assert _get_platforms_in_use([], {}) == []

    def test_no_duplicate_platforms(self):
        objects = [
            {
                "object_type": OBJECT_TYPE_ANALOG_INPUT,
                "instance": 1,
                "commandable": False,
            },
            {
                "object_type": OBJECT_TYPE_ANALOG_INPUT,
                "instance": 2,
                "commandable": False,
            },
            {
                "object_type": OBJECT_TYPE_ANALOG_VALUE,
                "instance": 3,
                "commandable": False,
            },
        ]
        result = _get_platforms_in_use(objects, {})
        platform_values = [p.value for p in result]
        assert len(platform_values) == len(set(platform_values))
