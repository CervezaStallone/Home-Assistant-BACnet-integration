"""Shared helper utilities for the BACnet integration."""

from __future__ import annotations

from typing import Any


def object_key(obj: dict[str, Any]) -> str:
    """Build a unique key string for a BACnet object dict."""
    return f"{obj['object_type']}:{obj['instance']}"


def select_objects_by_key(
    objects: list[dict[str, Any]], keys: set[str] | list[str]
) -> list[dict[str, Any]]:
    """Filter *objects* down to those whose object_key() is in *keys*.

    Shared by the config flow's initial selection and the options flow's
    rescan/re-selection step (issue #30) so both build the stored
    selected_objects list the same way.
    """
    keys = set(keys)
    return [obj for obj in objects if object_key(obj) in keys]


def object_label(obj: dict[str, Any]) -> str:
    """Build a human-readable label for an object selection checkbox."""
    from .const import OBJECT_TYPE_NAMES  # local import avoids a cycle at module load

    type_name = OBJECT_TYPE_NAMES.get(obj["object_type"], f"Type {obj['object_type']}")
    name = obj.get("object_name", "unnamed")
    instance = obj["instance"]
    return f"{type_name} ({instance}) — {name}"


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
