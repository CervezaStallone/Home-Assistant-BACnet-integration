"""
Options flow for BACnet IP integration.

Steps behind the integration's "Configure" button:
  init              – menu: settings, rescan_objects or domain_mapping
  settings          – COV toggle, polling interval, naming, live metadata
  rescan_objects    – re-read the device's object list, then domain_mapping
  domain_mapping    – pick which objects to customise (unpicked = defaults)
  customize_objects – per picked object: HA type, COV on/off, state class
                      and, for climate objects, the temperature object
"""

from __future__ import annotations

import logging
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .bacnet_client import BACnetClient
from .const import (
    CONF_ADD_OBJECTS,
    CONF_CLIMATE_TEMPERATURE_SOURCES,
    CONF_COV_INCREMENT,
    CONF_COV_OVERRIDES,
    CONF_CUSTOMIZE_OBJECTS,
    CONF_DEVICE_ADDRESS,
    CONF_DEVICE_ID,
    CONF_DOMAIN_MAPPING,
    CONF_ENABLE_COV,
    CONF_EXTRA_PROPERTIES,
    CONF_KEPT_DOMAIN_OVERRIDES,
    CONF_LIVE_METADATA_PROPERTIES,
    CONF_POLLING_INTERVAL,
    CONF_REMOVE_OBJECTS,
    CONF_SELECT_TYPES,
    CONF_SELECTED_OBJECTS,
    CONF_SHARE_USAGE,
    CONF_STATE_CLASSES,
    CONF_USE_DESCRIPTION,
    DEFAULT_COV_INCREMENT,
    DEFAULT_ENABLE_COV,
    DEFAULT_LIVE_METADATA_PROPERTIES,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_USE_DESCRIPTION,
    EXTRA_PROPERTY_SPECS,
    LIVE_METADATA_PROPERTY_CHOICES,
    MIN_POLLING_INTERVAL,
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
    PRIVACY_URL,
    STATE_CLASS_AUTO,
    STATE_CLASS_CHOICES,
    SUPPORTED_DOMAINS,
)
from .helpers import default_domain_for, property_label
from .helpers import object_key as _object_key
from .helpers import object_label as _object_label
from .helpers import object_picker as _object_picker
from .helpers import object_type_options as _object_type_options
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
        self._options_so_far: dict[str, Any] = dict(config_entry.options)
        self._rescanned_objects: list[dict[str, Any]] = []
        self._customize: list[dict[str, Any]] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu: general settings, rescan the device, or customise objects."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", "rescan_objects", "domain_mapping"],
        )

    # ------------------------------------------------------------------
    # General options (COV, polling, naming)
    # ------------------------------------------------------------------

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """COV, polling interval, naming, live metadata, usage statistics."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # --- Validate polling interval ---
            polling = user_input.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL)
            if not isinstance(polling, int) or polling < MIN_POLLING_INTERVAL:
                errors["base"] = "invalid_polling_interval"

            if not errors:
                # Merge with existing options so per-object settings stay
                return self.async_create_entry(
                    title="", data={**self._config_entry.options, **user_input}
                )

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
        current_share = self._config_entry.options.get(CONF_SHARE_USAGE, False)

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
                vol.Optional(CONF_SHARE_USAGE, default=current_share): bool,
            }
        )

        return self.async_show_form(
            step_id="settings",
            data_schema=schema,
            errors=errors,
            description_placeholders={"privacy_url": PRIVACY_URL},
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
            # Objects the device no longer has drop out by themselves
            found = {_object_key(obj) for obj in self._rescanned_objects}
            types = {int(t) for t in user_input.get(CONF_SELECT_TYPES, [])}
            selected_keys = (
                (current_keys & found) - set(user_input.get(CONF_REMOVE_OBJECTS, []))
                | set(user_input.get(CONF_ADD_OBJECTS, []))
                | {
                    _object_key(obj)
                    for obj in self._rescanned_objects
                    if obj["object_type"] in types
                }
            )
            if not selected_keys:
                errors["base"] = "no_objects_selected"
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
        else:
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

        # Three fields, all empty: submitting unchanged keeps the selection.
        # Big devices made one preselected list of hundreds unusable
        # (issue #90); objects not imported yet are marked NEW (issue #61).
        new_objects = [
            o for o in self._rescanned_objects if _object_key(o) not in current_keys
        ]
        imported = [
            o for o in self._rescanned_objects if _object_key(o) in current_keys
        ]
        schema = vol.Schema(
            {
                vol.Optional(CONF_ADD_OBJECTS, default=[]): _object_picker(
                    {_object_key(o): _object_label(o, new=True) for o in new_objects}
                ),
                vol.Optional(CONF_SELECT_TYPES, default=[]): cv.multi_select(
                    _object_type_options(new_objects)
                ),
                vol.Optional(CONF_REMOVE_OBJECTS, default=[]): _object_picker(
                    {_object_key(o): _object_label(o) for o in imported}
                ),
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
            | set(self._current(CONF_EXTRA_PROPERTIES))
            | set(self._current(CONF_STATE_CLASSES))
        )
        options = {_object_key(o): _object_label(o) for o in objects}
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_CUSTOMIZE_OBJECTS,
                    default=[k for k in options if k in customised],
                ): _object_picker(options),
            }
        )
        return self.async_show_form(step_id="domain_mapping", data_schema=schema)

    async def async_step_customize_objects(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """HA type, COV, climate temperature source and state class.

        Field names are the objects' own labels (a translation can't name a
        per-device object), so the form reads e.g. "Analog Value (4) — SP — type".
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            edits = {
                _object_key(obj): {
                    what: user_input.get(self._field(obj, what))
                    for what in (
                        "type",
                        "COV",
                        "temperature sensor",
                        "extra properties",
                        "other properties",
                        "state class",
                    )
                }
                for obj in self._customize
            }
            try:
                for edit in edits.values():
                    edit["other properties"] = [
                        BACnetClient.normalize_property(p)
                        for p in (edit["other properties"] or "").split(",")
                        if p.strip()
                    ]
            except ValueError:
                errors["base"] = "unknown_property"
            else:
                return self._save(edits)

        domains = self._current(CONF_DOMAIN_MAPPING)
        covs = self._current(CONF_COV_OVERRIDES)
        sources = self._current(CONF_CLIMATE_TEMPERATURE_SOURCES)
        extras = self._current(CONF_EXTRA_PROPERTIES)
        state_classes = self._current(CONF_STATE_CLASSES)
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
            if domain == "sensor" and obj["object_type"] in _ANALOG_TYPES:
                fields[
                    vol.Optional(
                        self._field(obj, "state class"),
                        default=state_classes.get(key, STATE_CLASS_AUTO),
                    )
                ] = SelectSelector(
                    SelectSelectorConfig(
                        options=STATE_CLASS_CHOICES,
                        mode=SelectSelectorMode.LIST,
                        translation_key="state_class",
                    )
                )
            offered = {
                prop: f"{property_label(prop)} ({prop})"
                for prop, (_, types, _) in EXTRA_PROPERTY_SPECS.items()
                if obj["object_type"] in types
            }
            current = extras.get(key, [])
            if offered:
                fields[
                    vol.Optional(
                        self._field(obj, "extra properties"),
                        default=[p for p in current if p in offered],
                    )
                ] = cv.multi_select(offered)
            fields[
                vol.Optional(
                    self._field(obj, "other properties"),
                    default=", ".join(p for p in current if p not in offered),
                )
            ] = str
        return self.async_show_form(
            step_id="customize_objects", data_schema=vol.Schema(fields), errors=errors
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
        extras = dict(self._current(CONF_EXTRA_PROPERTIES))
        states = dict(self._current(CONF_STATE_CLASSES))
        selected = {_object_key(o) for o in self._selected_objects}
        kept = [
            k
            for k in self._config_entry.options.get(CONF_KEPT_DOMAIN_OVERRIDES, [])
            if k not in selected
        ]
        for obj in self._selected_objects:
            key = _object_key(obj)
            for store in (domains, covs, sources, extras, states):
                store.pop(key, None)
            edit = edits.get(key)
            if edit is None:
                continue
            if edit["type"] is not None and edit["type"] != default_domain_for(obj):
                domains[key] = edit["type"]
                kept.append(key)
            if edit["COV"] is not None and edit["COV"] != self._global_cov:
                covs[key] = edit["COV"]
            if edit["temperature sensor"]:
                sources[key] = edit["temperature sensor"]
            # Picked first, then typed; a property picked twice counts once.
            picked = list(
                dict.fromkeys(
                    (edit.get("extra properties") or [])
                    + (edit.get("other properties") or [])
                )
            )
            if picked:
                extras[key] = picked
            if edit.get("state class") not in (None, STATE_CLASS_AUTO):
                states[key] = edit["state class"]

        return self.async_create_entry(
            title="",
            data={
                **self._options_so_far,
                CONF_DOMAIN_MAPPING: domains,
                CONF_COV_OVERRIDES: covs,
                CONF_CLIMATE_TEMPERATURE_SOURCES: sources,
                CONF_EXTRA_PROPERTIES: extras,
                CONF_STATE_CLASSES: states,
                CONF_KEPT_DOMAIN_OVERRIDES: kept,
            },
        )
