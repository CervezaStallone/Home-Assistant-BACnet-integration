"""
Options flow for BACnet IP integration.

Provides two configuration steps accessible from the integration's "Configure" button:
  Step 1 (init)           – COV toggle, polling fallback interval, naming toggle
  Step 2 (domain_mapping) – Per-object HA domain override (sensor/switch/number/climate/…)
                             and per-object COV enable/disable override
"""

from __future__ import annotations

import logging
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_COV_INCREMENT,
    CONF_COV_OVERRIDES,
    CONF_DEVICE_ADDRESS,
    CONF_DEVICE_ID,
    CONF_DOMAIN_MAPPING,
    CONF_ENABLE_COV,
    CONF_LIVE_METADATA_PROPERTIES,
    CONF_POLLING_INTERVAL,
    CONF_RESCAN_OBJECTS,
    CONF_SELECTED_OBJECTS,
    CONF_USE_DESCRIPTION,
    DATA_CLIENT,
    DEFAULT_COV_INCREMENT,
    DEFAULT_DOMAIN_MAP,
    DEFAULT_ENABLE_COV,
    DEFAULT_LIVE_METADATA_PROPERTIES,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_USE_DESCRIPTION,
    DOMAIN,
    LIVE_METADATA_PROPERTY_CHOICES,
    SUPPORTED_DOMAINS,
)
from .helpers import object_key as _object_key
from .helpers import object_label as _object_label
from .helpers import select_objects_by_key as _select_objects_by_key

_LOGGER = logging.getLogger(__name__)


class BACnetOptionsFlow(config_entries.OptionsFlow):
    """Handle BACnet integration options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Store the config entry so we can read current data + options."""
        self._config_entry = config_entry
        self._options_so_far: dict[str, Any] = {}
        self._rescanned_objects: list[dict[str, Any]] = []

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
            if not isinstance(polling, int) or polling < 1:
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
                    vol.Coerce(int), vol.Range(min=1)
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
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id, {})
        client = entry_data.get(DATA_CLIENT)

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
    # Step 2: Per-object domain mapping
    # ------------------------------------------------------------------

    async def async_step_domain_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Second options step — let the user reassign HA domains per BACnet object.

        Each selected BACnet object is shown as a dropdown with the available
        HA domains (sensor, binary_sensor, switch, number, climate).
        The user can change the mapping and save.
        """
        errors: dict[str, str] = {}

        # Retrieve the selected objects from the config entry data
        selected_objects: list[dict[str, Any]] = self._config_entry.data.get(
            CONF_SELECTED_OBJECTS, []
        )

        if user_input is not None:
            # Build the domain mapping dict from form values
            domain_mapping: dict[str, str] = {}
            cov_overrides: dict[str, bool] = {}
            for obj in selected_objects:
                obj_key = f"{obj['object_type']}:{obj['instance']}"
                domain_field_key = f"domain_{obj_key}"
                if domain_field_key in user_input:
                    domain_mapping[obj_key] = user_input[domain_field_key]

                cov_field_key = f"cov_{obj_key}"
                if cov_field_key in user_input:
                    cov_overrides[obj_key] = user_input[cov_field_key]

            # Store in options and create entry
            final_options = {
                **self._options_so_far,
                CONF_DOMAIN_MAPPING: domain_mapping,
                CONF_COV_OVERRIDES: cov_overrides,
            }
            return self.async_create_entry(title="", data=final_options)

        # --- Build the form: one dropdown + one COV checkbox per BACnet object ---
        current_mapping: dict[str, str] = self._config_entry.options.get(
            CONF_DOMAIN_MAPPING, {}
        )
        current_cov_overrides: dict[str, bool] = self._config_entry.options.get(
            CONF_COV_OVERRIDES, {}
        )
        global_cov_default = self._options_so_far.get(
            CONF_ENABLE_COV,
            self._config_entry.options.get(CONF_ENABLE_COV, DEFAULT_ENABLE_COV),
        )

        schema_fields: dict[Any, Any] = {}
        for obj in selected_objects:
            obj_key = f"{obj['object_type']}:{obj['instance']}"

            # Current domain: user override → default map → "sensor"
            current_domain = current_mapping.get(
                obj_key, DEFAULT_DOMAIN_MAP.get(obj["object_type"], "sensor")
            )

            domain_field_key = f"domain_{obj_key}"
            schema_fields[
                vol.Optional(
                    domain_field_key,
                    default=current_domain,
                    description={"suggested_value": current_domain},
                )
            ] = vol.In({d: d for d in SUPPORTED_DOMAINS})

            # Current COV state: per-object override → device-wide default
            current_cov = current_cov_overrides.get(obj_key, global_cov_default)

            cov_field_key = f"cov_{obj_key}"
            schema_fields[vol.Optional(cov_field_key, default=current_cov)] = bool

        schema = vol.Schema(schema_fields)

        return self.async_show_form(
            step_id="domain_mapping",
            data_schema=schema,
            errors=errors,
        )
