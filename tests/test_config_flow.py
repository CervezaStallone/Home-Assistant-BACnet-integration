"""Tests for config_flow._validate_local_ip (issue #23 — CIDR-prefixed local IPs)."""

from custom_components.bacnet.config_flow import _validate_local_ip


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
