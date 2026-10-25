"""Repair flows for the BACnet IP integration.

stale_domain_overrides: before 1.0.47 the options flow stored a type-only
domain default as an override for every object, forcing e.g. non-commandable
Binary Values onto the switch platform. The mapping is the user's, so they
choose: remove those overrides, or keep them (issue #97: some devices accept
writes without a Priority Array).

share_usage: installations from before the usage statistics were ticked by
default are asked once whether to share them (telemetry.py).
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DOMAIN_MAPPING,
    CONF_KEPT_DOMAIN_OVERRIDES,
    CONF_SELECTED_OBJECTS,
    CONF_SHARE_USAGE,
    DOMAIN,
    ISSUE_SHARE_USAGE,
    PRIVACY_URL,
)
from .helpers import stale_domain_overrides


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the fix flow for the issue."""
    if issue_id == ISSUE_SHARE_USAGE:
        return ShareUsageRepairFlow()
    return StaleDomainOverridesRepairFlow((data or {}).get("entry_id", ""))


class StaleDomainOverridesRepairFlow(RepairsFlow):
    """Remove the stale overrides or keep them (the entry reloads on update)."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None):
        return self.async_show_menu(step_id="confirm", menu_options=["remove", "keep"])

    async def async_step_remove(self, user_input: dict[str, Any] | None = None):
        return self._update(
            lambda options, stale: {
                CONF_DOMAIN_MAPPING: {
                    k: v
                    for k, v in options.get(CONF_DOMAIN_MAPPING, {}).items()
                    if k not in stale
                }
            }
        )

    async def async_step_keep(self, user_input: dict[str, Any] | None = None):
        def keep(options, stale):
            kept = list(options.get(CONF_KEPT_DOMAIN_OVERRIDES, []))
            return {
                CONF_KEPT_DOMAIN_OVERRIDES: kept + [k for k in stale if k not in kept]
            }

        return self._update(keep)

    def _update(self, change):
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is not None:
            stale = stale_domain_overrides(
                entry.data.get(CONF_SELECTED_OBJECTS, []),
                entry.options.get(CONF_DOMAIN_MAPPING, {}),
                entry.options.get(CONF_KEPT_DOMAIN_OVERRIDES, []),
            )
            self.hass.config_entries.async_update_entry(
                entry, options={**entry.options, **change(entry.options, stale)}
            )
        return self.async_create_entry(data={})


class ShareUsageRepairFlow(RepairsFlow):
    """Tick or leave the usage statistics for every device; asked only once."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None):
        if user_input is None:
            return self.async_show_form(
                step_id="confirm",
                data_schema=vol.Schema(
                    {vol.Optional(CONF_SHARE_USAGE, default=True): bool}
                ),
                description_placeholders={"privacy_url": PRIVACY_URL},
            )

        await self.hass.data[DOMAIN]["_telemetry"].async_mark_asked()
        if user_input.get(CONF_SHARE_USAGE, False):
            for entry in self.hass.config_entries.async_entries(DOMAIN):
                self.hass.config_entries.async_update_entry(
                    entry, options={**entry.options, CONF_SHARE_USAGE: True}
                )
        return self.async_create_entry(data={})
