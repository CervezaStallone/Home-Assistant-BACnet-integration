"""Shared helper utilities for the BACnet integration."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)


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


def object_label(obj: dict[str, Any], *, new: bool = False) -> str:
    """Build a human-readable label for an object selection checkbox.

    The description is added when it says more than the name (issue #61:
    Siemens PXC objects have technical names, the description says what
    they are). *new* marks objects a rescan found for the first time.
    """
    # local import avoids a cycle at module load
    from .const import OBJECT_TYPE_ABBREVIATIONS, OBJECT_TYPE_NAMES

    object_type = obj["object_type"]
    type_name = OBJECT_TYPE_NAMES.get(object_type, f"Type {object_type}")
    name = obj.get("object_name", "unnamed")
    instance = obj["instance"]
    abbreviation = OBJECT_TYPE_ABBREVIATIONS.get(object_type)
    number = f"{abbreviation} {instance}" if abbreviation else instance
    label = f"{type_name} ({number}) — {name}"
    description = obj.get("description")
    if description and description != name:
        label += f" ({description})"
    return f"NEW · {label}" if new else label


def object_picker(options: dict[str, str]) -> SelectSelector:
    """Searchable multi-select of object keys → labels.

    A device can have hundreds of objects (issue #90); typing filters the
    list, picked objects show as chips.
    """
    return SelectSelector(
        SelectSelectorConfig(
            options=[SelectOptionDict(value=k, label=v) for k, v in options.items()],
            multiple=True,
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def object_type_options(objects: Iterable[dict[str, Any]]) -> dict[str, str]:
    """One choice per object type present, with its count: "Analog Input (40)"."""
    from .const import OBJECT_TYPE_NAMES  # local import avoids a cycle at module load

    counts = Counter(obj["object_type"] for obj in objects)
    return {
        str(t): f"{OBJECT_TYPE_NAMES.get(t, f'Type {t}')} ({n})"
        for t, n in sorted(counts.items())
    }


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


def default_domain_for(obj: dict[str, Any]) -> str:
    """Return the default HA domain for a BACnet object (type + commandability).

    Value-type objects (AV, BV, MSV) only map to a writable domain when they
    are commandable (have a Priority Array) — the device rejects writes
    otherwise. Single source of truth for setup, the coordinator and the
    options flow, so they can never disagree.
    """
    from .const import (  # local import avoids a cycle at module load
        DEFAULT_DOMAIN_MAP,
        OBJECT_TYPE_ANALOG_VALUE,
        OBJECT_TYPE_BINARY_VALUE,
        OBJECT_TYPE_MULTI_STATE_VALUE,
    )

    obj_type = obj["object_type"]
    commandable = obj.get("commandable", False)
    if obj_type == OBJECT_TYPE_BINARY_VALUE:
        return "switch" if commandable else "binary_sensor"
    if obj_type in {OBJECT_TYPE_ANALOG_VALUE, OBJECT_TYPE_MULTI_STATE_VALUE}:
        return "number" if commandable else "sensor"
    return DEFAULT_DOMAIN_MAP.get(obj_type, "sensor")


def stale_domain_overrides(
    objects: list[dict[str, Any]],
    domain_overrides: dict[str, str],
    kept: Iterable[str] = (),
) -> list[str]:
    """Return keys of overrides left behind by the pre-1.0.47 options-flow bug.

    That bug stored the type-only DEFAULT_DOMAIN_MAP value as an explicit
    override for every object. Only the overrides where that value differs
    from the commandable-aware default are harmful: a non-commandable BV
    forced to "switch", or a commandable AV/MSV stuck on read-only "sensor".
    Any other value was a deliberate user choice, as are the *kept* keys:
    a device can accept writes without a Priority Array (issue #97).
    """
    from .const import DEFAULT_DOMAIN_MAP  # local import avoids a cycle at module load

    kept = set(kept)
    stale = []
    for obj in objects:
        key = object_key(obj)
        override = domain_overrides.get(key)
        if (
            key not in kept
            and override is not None
            and override == DEFAULT_DOMAIN_MAP.get(obj["object_type"])
            and override != default_domain_for(obj)
        ):
            stale.append(key)
    return stale


def extra_property_platform(prop: str, object_type: int) -> str:
    """HA platform for extra property *prop* of an *object_type* object.

    Its spec's platform when the spec covers that object type, otherwise a
    read-only sensor (typed-in or proprietary properties).
    """
    from .const import EXTRA_PROPERTY_SPECS  # local import avoids a cycle

    spec = EXTRA_PROPERTY_SPECS.get(prop)
    return spec[0] if spec and object_type in spec[1] else "sensor"


def property_label(prop: str) -> str:
    """'minimumOnTime' -> 'Minimum On Time'; proprietary numbers stay as-is."""
    return " ".join(
        w.capitalize() for w in re.findall(r"[A-Z]?[a-z]+|\d+|[A-Z]+", prop)
    )
