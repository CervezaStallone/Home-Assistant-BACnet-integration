"""Shared helper utilities for the BACnet integration."""

from __future__ import annotations


def mask_address(addr: str | object) -> str:
    """Partially mask a network address for safe logging.

    Replaces the middle octets of an IPv4 address with 'x' to avoid
    logging full network addresses while retaining enough detail for
    debugging (first and last octet plus port).
    """
    addr_str = str(addr)
    if not addr_str:
        return "<none>"
    parts = addr_str.rsplit(":", 1)
    ip_part = parts[0]
    port_suffix = f":{parts[1]}" if len(parts) == 2 else ""
    octets = ip_part.split(".")
    if len(octets) == 4:
        return f"{octets[0]}.x.x.{octets[3]}{port_suffix}"
    return addr_str
