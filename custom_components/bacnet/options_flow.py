"""
Options flow for BACnet IP integration.

Steps behind the integration's "Configure" button:
  init              – COV toggle, polling interval, naming, live metadata
  rescan_objects    – (optional) re-read the device's object list
  domain_mapping    – pick which objects to customise (unpicked = defaults)
  customize_objects – per picked object: HA type, COV on/off and, for
                      climate objects, the object providing the temperature
"""

from __future__ import annotations

import logging
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_CLIMATE_TEMPERATURE_SOURCES,
    CONF_COV_INCREMENT,
    CONF_COV_OVERRIDES,
    CONF_CUSTOMIZE_OBJECTS,
    CONF_DEVICE_ADDRESS,
    CONF_DEVICE_ID,
    CONF_DOMAIN_MAPPING,
    CONF_ENABLE_COV,
    CONF_LIVE_METADATA_PROPERTIES,
    CONF_POLLING_INTERVAL,
    CONF_RESCAN_OBJECTS,
    CONF_SELECTED_OBJECTS,
    CONF_USE_DESCRIPTION,
    DEFAULT_COV_INCREMENT,
    DEFAULT_ENABLE_COV,
    DEFAULT_LIVE_METADATA_PROPERTIES,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_USE_DESCRIPTION,
    LIVE_METADATA_PROPERTY_CHOICES,
    MIN_POLLING_INTERVAL,
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
    SUPPORTED_DOMAINS,
)
from .helpers import default_domain_for
from .helpers import object_key as _object_key
from .helpers import object_label as _object_label
from .helpers import select_objects_by_key as _select_objects_by_key

_LOGGER = logging.getLogger(__name__)

# Object types that can provide a temperature for a climate entity.
_ANALOG_TYPES = {
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
}


class BACnetOptionsFlow(config_entries.OptionsFlow):
    """Handle BACnet integration options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Store the config entry so we can read current data + options."""
        self._config_entry = config_entry
        self._options_so_far: dict[str, Any] = {}
        self._rescanned_objects: list[dict[str, Any]] = []
        self._customize: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Step 1: General options (COV, polling, naming)
    # ------------------------------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """First options step — COV, polling interval, naming toggle."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # --- Validate polling interval ---
            polling = user_input.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL)
            if not isinstance(polling, int) or polling < MIN_POLLING_INTERVAL:
                errors["base"] = "invalid_polling_interval"

            rescan = user_input.pop(CONF_RESCAN_OBJECTS, False)

            if not errors:
                # Merge with existing options so domain mapping is preserved
                new_options = {**self._config_entry.options, **user_input}
                self._options_so_far = new_options
                if rescan:
                    return await self.async_step_rescan_objects()
                return await self.async_step_domain_mapping()

        # --- Current values (fallback to defaults) ---
        current_cov = self._config_entry.options.get(
            CONF_ENABLE_COV, DEFAULT_ENABLE_COV
        )
        current_poll = self._config_entry.options.get(
            CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL
        )
        current_desc = self._config_entry.options.get(
            CONF_USE_DESCRIPTION, DEFAULT_USE_DESCRIPTION
        )
        current_cov_inc = self._config_entry.options.get(
            CONF_COV_INCREMENT, DEFAULT_COV_INCREMENT
        )
        current_live_metadata = self._config_entry.options.get(
            CONF_LIVE_METADATA_PROPERTIES, DEFAULT_LIVE_METADATA_PROPERTIES
        )

        schema = vol.Schema(
            {
                vol.Optional(CONF_ENABLE_COV, default=current_cov): bool,
                vol.Optional(CONF_COV_INCREMENT, default=current_cov_inc): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0)
                ),
                vol.Optional(CONF_POLLING_INTERVAL, default=current_poll): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_POLLING_INTERVAL)
                ),
                vol.Optional(CONF_USE_DESCRIPTION, default=current_desc): bool,
                vol.Optional(
                    CONF_LIVE_METADATA_PROPERTIES, default=current_live_metadata
                ): cv.multi_select(
                    {
                        p: p.replace("_", " ").title()
                        for p in LIVE_METADATA_PROPERTY_CHOICES
                    }
                ),
                vol.Optional(CONF_RESCAN_OBJECTS, default=False): bool,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Optional step: rescan the device's Object_List and update selection
    # (issue #30 — object selection was previously a one-time setup step)
    # ------------------------------------------------------------------

    async def async_step_rescan_objects(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Reread the device's Object_List and let the user add/remove objects.

        Reuses the running entry's own BACnet client (it's already connected
        to this device), so no new socket or discovery step is needed.
        """
        errors: dict[str, str] = {}
        runtime_data = getattr(self._config_entry, "runtime_data", None)
        client = runtime_data.coordinator.client if runtime_data else None

        current_objects: list[dict[str, Any]] = self._config_entry.data.get(
            CONF_SELECTED_OBJECTS, []
        )
        current_keys = {_object_key(obj) for obj in current_objects}

        if user_input is not None:
            selected_keys = set(user_input.get(CONF_SELECTED_OBJECTS, []))
            if not selected_keys:
                errors["base"] = "no_objects_found"
            else:
                new_selected = _select_objects_by_key(
                    self._rescanned_objects, selected_keys
                )
                # Persist the updated object list on the entry's data (not
                # options) before finishing — mirrors how the initial config
                # flow stores selected_objects. domain_mapping/cov_overrides
                # for objects that stay selected are untouched; stale entries
                # for removed objects are harmless leftovers in options.
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    data={
                        **self._config_entry.data,
                        CONF_SELECTED_OBJECTS: new_selected,
                    },
                )
                return await self.async_step_domain_mapping()

        if client is None:
            return self.async_abort(reason="cannot_connect")

        try:
            self._rescanned_objects = await client.read_object_list(
                device_address=self._config_entry.data.get(CONF_DEVICE_ADDRESS, ""),
                device_id=self._config_entry.data[CONF_DEVICE_ID],
            )
        except Exception:
            _LOGGER.exception("Failed to rescan BACnet object list")
            return self.async_abort(reason="cannot_connect")

        if not self._rescanned_objects:
            errors["base"] = "no_objects_found"

        # Default selection: everything already imported, plus nothing new
        # (newly discovered objects are opt-in, matching the requested UX).
        object_options = {
            _object_key(obj): _object_label(obj) for obj in self._rescanned_objects
        }
        default_keys = [k for k in object_options if k in current_keys]

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SELECTED_OBJECTS, default=default_keys
                ): cv.multi_select(object_options),
            }
        )

        return self.async_show_form(
            step_id="rescan_objects",
            data_schema=schema,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Per-object customisation: pick objects, then edit only those
    # ------------------------------------------------------------------

    @property
    def _selected_objects(self) -> list[dict[str, Any]]:
        return self._config_entry.data.get(CONF_SELECTED_OBJECTS, [])

    @property
    def _global_cov(self) -> bool:
        return self._options_so_far.get(
            CONF_ENABLE_COV,
            self._config_entry.options.get(CONF_ENABLE_COV, DEFAULT_ENABLE_COV),
        )

    def _current(self, conf_key: str) -> dict[str, Any]:
        return self._config_entry.options.get(conf_key, {})

    async def async_step_domain_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pick which objects to customise; objects left unpicked use defaults.

        Objects that already have a custom setting are preselected, so a
        plain "Submit" keeps everything as it was.
        """
        objects = self._selected_objects
        if user_input is not None:
            picked = set(user_input.get(CONF_CUSTOMIZE_OBJECTS, []))
            self._customize = [o for o in objects if _object_key(o) in picked]
            if not self._customize:
                return self._save({})
            return await self.async_step_customize_objects()

        customised = (
            set(self._current(CONF_DOMAIN_MAPPING))
            | set(self._current(CONF_COV_OVERRIDES))
            | set(self._current(CONF_CLIMATE_TEMPERATURE_SOURCES))
        )
        options = {_object_key(o): _object_label(o) for o in objects}
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_CUSTOMIZE_OBJECTS,
                    default=[k for k in options if k in customised],
                ): cv.multi_select(options),
            }
        )
        return self.async_show_form(step_id="domain_mapping", data_schema=schema)

    async def async_step_customize_objects(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """HA type, COV and climate temperature source for the picked objects.

        Field names are the objects' own labels (a translation can't name a
        per-device object), so the form reads e.g. "Analog Value (4) — SP — type".
        """
        if user_input is not None:
            return self._save(
                {
                    _object_key(obj): {
                        what: user_input.get(self._field(obj, what))
                        for what in ("type", "COV", "temperature sensor")
                    }
                    for obj in self._customize
                }
            )

        domains = self._current(CONF_DOMAIN_MAPPING)
        covs = self._current(CONF_COV_OVERRIDES)
        sources = self._current(CONF_CLIMATE_TEMPERATURE_SOURCES)
        temperature_choices = {"": "—"} | {
            _object_key(o): _object_label(o)
            for o in self._selected_objects
            if o["object_type"] in _ANALOG_TYPES
        }
        fields: dict[Any, Any] = {}
        for obj in self._customize:
            key = _object_key(obj)
            domain = domains.get(key, default_domain_for(obj))
            fields[vol.Optional(self._field(obj, "type"), default=domain)] = vol.In(
                {d: d for d in SUPPORTED_DOMAINS}
            )
            fields[
                vol.Optional(
                    self._field(obj, "COV"), default=covs.get(key, self._global_cov)
                )
            ] = bool
            if domain == "climate":
                fields[
                    vol.Optional(
                        self._field(obj, "temperature sensor"),
                        default=sources.get(key, ""),
                    )
                ] = vol.In({k: v for k, v in temperature_choices.items() if k != key})
        return self.async_show_form(
            step_id="customize_objects", data_schema=vol.Schema(fields)
        )

    @staticmethod
    def _field(obj: dict[str, Any], what: str) -> str:
        return f"{_object_label(obj)} — {what}"

    def _save(self, edits: dict[str, dict[str, Any]]) -> FlowResult:
        """Store per-object settings, keeping only values that differ from defaults.

        Every selected object not in *edits* is reset to its defaults.
        Settings of objects no longer selected are kept, so they come back
        if the object is re-selected after a rescan (issue #30). Storing
        every form value would freeze today's defaults as overrides, so a
        later commandable change or enable_cov toggle would never apply.
        """
        domains = dict(self._current(CONF_DOMAIN_MAPPING))
        covs = dict(self._current(CONF_COV_OVERRIDES))
        sources = dict(self._current(CONF_CLIMATE_TEMPERATURE_SOURCES))
        for obj in self._selected_objects:
            key = _object_key(obj)
            for store in (domains, covs, sources):
                store.pop(key, None)
            edit = edits.get(key)
            if edit is None:
                continue
            if edit["type"] is not None and edit["type"] != default_domain_for(obj):
                domains[key] = edit["type"]
            if edit["COV"] is not None and edit["COV"] != self._global_cov:
                covs[key] = edit["COV"]
            if edit["temperature sensor"]:
                sources[key] = edit["temperature sensor"]

        return self.async_create_entry(
            title="",
            data={
                **self._options_so_far,
                CONF_DOMAIN_MAPPING: domains,
                CONF_COV_OVERRIDES: covs,
                CONF_CLIMATE_TEMPERATURE_SOURCES: sources,
            },
        )
