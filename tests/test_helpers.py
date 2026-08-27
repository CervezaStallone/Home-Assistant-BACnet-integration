"""Tests for helpers.mask_address."""

from typing import ClassVar

from custom_components.bacnet.helpers import (
    mask_address,
    object_key,
    select_objects_by_key,
)


class TestMaskAddress:
    def test_ipv4_no_port(self):
        assert mask_address("192.168.1.100") == "192.x.x.100"

    def test_ipv4_with_port(self):
        assert mask_address("10.0.0.1:47808") == "10.x.x.1:47808"

    def test_first_and_last_octet_preserved(self):
        result = mask_address("172.16.254.1")
        assert result.startswith("172.")
        assert result.endswith(".1")
        assert "x.x" in result

    def test_middle_octets_masked(self):
        result = mask_address("1.2.3.4")
        assert "2" not in result or result == "1.x.x.4"  # 2 only hidden
        assert result == "1.x.x.4"

    def test_non_ipv4_passthrough(self):
        assert mask_address("bacnet-device") == "bacnet-device"

    def test_empty_string(self):
        assert mask_address("") == "<none>"

    def test_object_with_str(self):
        class Addr:
            def __str__(self):
                return "10.1.2.3:47809"

        assert mask_address(Addr()) == "10.x.x.3:47809"

    def test_port_variants(self):
        assert mask_address("192.168.0.50:1234") == "192.x.x.50:1234"


class TestSelectObjectsByKey:
    """Object selection filtering shared by config_flow and options_flow (#30)."""

    OBJECTS: ClassVar = [
        {"object_type": "analog-value", "instance": 1, "object_name": "AV1"},
        {"object_type": "analog-value", "instance": 2, "object_name": "AV2"},
        {"object_type": "binary-input", "instance": 5, "object_name": "BI5"},
    ]

    def test_filters_to_selected_keys_only(self):
        result = select_objects_by_key(self.OBJECTS, {"analog-value:1"})
        assert result == [self.OBJECTS[0]]

    def test_preserves_source_order_not_key_order(self):
        keys = {"binary-input:5", "analog-value:1"}
        result = select_objects_by_key(self.OBJECTS, keys)
        assert [object_key(o) for o in result] == ["analog-value:1", "binary-input:5"]

    def test_empty_keys_returns_empty(self):
        assert select_objects_by_key(self.OBJECTS, set()) == []

    def test_accepts_list_of_keys(self):
        result = select_objects_by_key(self.OBJECTS, ["analog-value:2"])
        assert result == [self.OBJECTS[1]]

    def test_unknown_key_ignored(self):
        result = select_objects_by_key(self.OBJECTS, {"analog-value:999"})
        assert result == []
