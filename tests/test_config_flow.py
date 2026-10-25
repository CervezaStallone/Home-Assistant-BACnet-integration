"""Tests for config_flow address validators (issues #22, #23)."""

from custom_components.bacnet.config_flow import (
    _validate_local_ip,
    _validate_target_address,
)


class TestValidateLocalIp:
    def test_empty_is_valid(self):
        assert _validate_local_ip("") is True

    def test_plain_ipv4_is_valid(self):
        assert _validate_local_ip("192.168.10.11") is True

    def test_cidr_prefix_is_valid(self):
        assert _validate_local_ip("192.168.10.11/24") is True

    def test_full_mask_prefix_is_valid(self):
        assert _validate_local_ip("192.168.10.11/255.255.255.0") is True

    def test_invalid_prefix_rejected(self):
        assert _validate_local_ip("192.168.10.11/33") is False

    def test_garbage_rejected(self):
        assert _validate_local_ip("not-an-ip") is False

    def test_hostname_rejected(self):
        assert _validate_local_ip("bacnet-host") is False


class TestValidateTargetAddress:
    def test_empty_is_valid(self):
        assert _validate_target_address("") is True

    def test_plain_ipv4_is_valid(self):
        assert _validate_target_address("192.168.1.50") is True

    def test_ipv4_with_port_is_valid(self):
        assert _validate_target_address("192.168.1.50:47808") is True

    def test_remote_station_is_valid(self):
        assert _validate_target_address("20000:1") is True

    def test_remote_station_hex_mac_is_valid(self):
        assert _validate_target_address("20000:0x05") is True

    def test_out_of_range_network_rejected(self):
        assert _validate_target_address("999999999:1") is False

    def test_garbage_rejected(self):
        assert _validate_target_address("garbage") is False


def test_select_objects_stores_share_usage_in_options():
    # Opt-in lives in the options (switchable later), never in entry data.
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.bacnet.config_flow import BACnetConfigFlow

    def finish(user_input):
        flow = BACnetConfigFlow()
        flow.hass = MagicMock(data={})
        flow._network_config = {}
        flow._selected_device = {"device_id": 1, "address": "10.0.0.5"}
        flow._discovered_objects = [{"object_type": 0, "instance": 1}]
        flow._cleanup_client = AsyncMock()
        flow.async_create_entry = lambda **kw: kw
        return asyncio.run(
            flow.async_step_select_objects({"selected_objects": ["0:1"], **user_input})
        )

    on = finish({"share_usage": True})
    off = finish({})
    assert on["options"] == {"share_usage": True}
    assert off["options"] == {"share_usage": False}
    assert "share_usage" not in on["data"]


def test_select_objects_links_the_privacy_policy():
    import asyncio

    from custom_components.bacnet.config_flow import BACnetConfigFlow
    from custom_components.bacnet.const import PRIVACY_URL

    flow = BACnetConfigFlow()
    flow._selected_device = {"device_id": 1, "device_name": "Dev"}
    flow._discovered_objects = [{"object_type": 0, "instance": 1}]
    flow.async_show_form = lambda **kw: kw
    shown = asyncio.run(flow.async_step_select_objects())
    assert shown["description_placeholders"] == {"privacy_url": PRIVACY_URL}


def test_setup_ticks_share_usage_by_default(monkeypatch):
    import asyncio

    from custom_components.bacnet import config_flow as cf
    from custom_components.bacnet.config_flow import BACnetConfigFlow

    defaults = {}

    def optional(key, default=None):
        defaults[key] = default
        return key

    monkeypatch.setattr(cf.vol, "Optional", optional)
    flow = BACnetConfigFlow()
    flow._selected_device = {"device_id": 1, "device_name": "Dev"}
    flow._discovered_objects = [{"object_type": 0, "instance": 1}]
    flow.async_show_form = lambda **kw: kw
    asyncio.run(flow.async_step_select_objects())
    assert defaults["share_usage"] is True


def _object_form(monkeypatch, user_input=None):
    """Run select_objects with two discovered objects; return form and defaults."""
    import asyncio
    from unittest.mock import MagicMock

    from custom_components.bacnet import config_flow as cf
    from custom_components.bacnet.config_flow import BACnetConfigFlow

    defaults = {}

    def optional(key, default=None):
        defaults[key] = default
        return key

    monkeypatch.setattr(cf.vol, "Optional", optional)
    flow = BACnetConfigFlow()
    flow.hass = MagicMock(data={})
    flow._network_config = {}
    flow._selected_device = {"device_id": 1, "device_name": "Dev"}
    flow._discovered_objects = [
        {"object_type": 0, "instance": 1, "object_name": "Room temp"},
        {"object_type": 4, "instance": 2, "object_name": "Fan"},
    ]
    flow.async_show_form = lambda **kw: kw
    return asyncio.run(flow.async_step_select_objects(user_input)), defaults


def test_objects_are_a_searchable_dropdown_with_nothing_preselected(monkeypatch):
    # Issue #90: with hundreds of objects, unticking all was the only way to
    # import a few. Nothing is preselected; Select all covers the other case.
    form, defaults = _object_form(monkeypatch)
    assert defaults["selected_objects"] == []
    kind, config = form["data_schema"]["selected_objects"]
    assert kind == "select"
    assert config["multiple"] is True
    assert config["mode"] == "dropdown"
    assert [o["value"] for o in config["options"]] == ["0:1", "4:2"]
    assert "Room temp" in config["options"][0]["label"]


def test_submitting_no_objects_says_none_were_selected(monkeypatch):
    form, _ = _object_form(monkeypatch, {"selected_objects": []})
    assert form["errors"] == {"base": "no_objects_selected"}
    assert "selected_objects" in form["data_schema"]
