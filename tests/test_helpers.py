"""Tests for helpers.mask_address."""

from custom_components.bacnet.helpers import mask_address


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
