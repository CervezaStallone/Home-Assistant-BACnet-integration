"""Repair flows for the BACnet IP integration.

stale_domain_overrides: before 1.0.47 the options flow stored a type-only
domain default as an override for every object, forcing e.g. non-commandable
Binary Values onto the switch platform. The user confirms before those
overrides are removed, since the mapping is theirs to own.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant

from .const import CONF_DOMAIN_MAPPING, CONF_SELECTED_OBJECTS
from .helpers import stale_domain_overrides


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the fix flow for a stale-domain-overrides issue."""
    return StaleDomainOverridesRepairFlow((data or {}).get("entry_id", ""))


class StaleDomainOverridesRepairFlow(RepairsFlow):
    """Confirm, then drop the stale overrides (the entry reloads on update)."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None):
        if user_input is None:
            return self.async_show_form(step_id="confirm")

        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is not None:
            mapping = entry.options.get(CONF_DOMAIN_MAPPING, {})
            stale = set(
                stale_domain_overrides(
                    entry.data.get(CONF_SELECTED_OBJECTS, []), mapping
                )
            )
            self.hass.config_entries.async_update_entry(
                entry,
                options={
                    **entry.options,
                    CONF_DOMAIN_MAPPING: {
                        k: v for k, v in mapping.items() if k not in stale
                    },
                },
            )
        return self.async_create_entry(data={})
