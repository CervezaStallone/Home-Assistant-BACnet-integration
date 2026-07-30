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
