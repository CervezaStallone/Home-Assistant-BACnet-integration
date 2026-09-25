"""UX guards: translations in sync, entity categories, write errors, polling floor."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import _make_coordinator, _make_entry

_COMPONENT = Path(__file__).parent.parent / "custom_components" / "bacnet"


def test_en_translation_matches_strings():
    """Custom integrations load translations/en.json — it must not drift."""
    strings = json.loads((_COMPONENT / "strings.json").read_text())
    en = json.loads((_COMPONENT / "translations" / "en.json").read_text())
    assert en == strings


class TestDeviceLevelEntities:
    def test_write_priority_select_is_config_and_translated(self):
        from custom_components.bacnet.select import BACnetWritePrioritySelect

        entity = BACnetWritePrioritySelect(_make_coordinator(), _make_entry())
        assert entity._attr_entity_category == "config"
        assert entity._attr_translation_key == "write_priority"
        assert "_attr_name" not in vars(entity)

    def test_refresh_button_is_config_and_translated(self):
        from custom_components.bacnet.button import BACnetRefreshMetadataButton

        entity = BACnetRefreshMetadataButton(_make_coordinator(), _make_entry())
        assert entity._attr_entity_category == "config"
        assert entity._attr_translation_key == "refresh_metadata"
        assert "_attr_name" not in vars(entity)

    def test_translation_keys_exist(self):
        strings = json.loads((_COMPONENT / "strings.json").read_text())
        assert "name" in strings["entity"]["select"]["write_priority"]
        assert "name" in strings["entity"]["button"]["refresh_metadata"]


def _writable(module, cls, obj):
    import importlib

    klass = getattr(importlib.import_module(f"custom_components.bacnet.{module}"), cls)
    entity = klass(_make_coordinator({}), _make_entry(), obj)
    client = MagicMock()
    client.write_property = AsyncMock(return_value=False)
    client.relinquish = AsyncMock(return_value=False)
    entity.coordinator.client = client
    entity.coordinator.async_refresh_object = AsyncMock()
    return entity


class TestFailedWritesRaise:
    @pytest.mark.parametrize(
        ("module", "cls", "call"),
        [
            ("switch", "BACnetSwitch", lambda e: e.async_turn_on()),
            ("switch", "BACnetSwitch", lambda e: e.async_turn_off()),
            ("number", "BACnetNumber", lambda e: e.async_set_native_value(3.0)),
            (
                "climate",
                "BACnetClimate",
                lambda e: e.async_set_temperature(temperature=21),
            ),
        ],
    )
    def test_rejected_write_raises_home_assistant_error(self, module, cls, call):
        from homeassistant.exceptions import HomeAssistantError

        obj = {"object_type": 2, "instance": 1, "commandable": True, "object_name": "X"}
        entity = _writable(module, cls, obj)
        with pytest.raises(HomeAssistantError) as err:
            asyncio.run(call(entity))
        assert err.value.translation_key == "write_failed"
        entity.coordinator.async_refresh_object.assert_not_awaited()

    def test_rejected_relinquish_raises_and_keeps_heat(self):
        from homeassistant.exceptions import HomeAssistantError

        from custom_components.bacnet.climate import HVACMode

        obj = {"object_type": 2, "instance": 1, "commandable": True, "object_name": "X"}
        entity = _writable("climate", "BACnetClimate", obj)
        entity.coordinator.data = {"2:1": {"presentValue": 20.0}}
        with pytest.raises(HomeAssistantError):
            asyncio.run(entity.async_set_hvac_mode(HVACMode.OFF))
        assert entity.hvac_mode == HVACMode.HEAT

    def test_write_failed_message_exists(self):
        strings = json.loads((_COMPONENT / "strings.json").read_text())
        assert "{object}" in strings["exceptions"]["write_failed"]["message"]


class TestPollingIntervalFloor:
    def test_interval_below_minimum_is_rejected(self):
        from custom_components.bacnet.const import MIN_POLLING_INTERVAL
        from custom_components.bacnet.options_flow import BACnetOptionsFlow

        entry = MagicMock()
        entry.options = {}
        flow = BACnetOptionsFlow(entry)
        flow.async_show_form = lambda **kw: kw
        result = asyncio.run(
            flow.async_step_init({"polling_interval": MIN_POLLING_INTERVAL - 1})
        )
        assert result["errors"] == {"base": "invalid_polling_interval"}
