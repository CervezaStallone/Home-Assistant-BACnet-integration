"""
BACnet IP Integration for Home Assistant.

This integration provides full BACnet/IP support including:
- Local network and BBMD / Foreign Device Registration for cross-subnet communication
- Automatic device discovery via Who-Is / I-Am
- Per-object COV subscriptions with automatic polling fallback
- Read/write with proper Priority Array handling
- Dynamic domain mapping (sensor, switch, number, binary_sensor, climate)

All configuration is done via the GUI (config_flow / options_flow).
"""

from __future__ import annotations

import logging
from typing import Any

import homeassistant.helpers.config_validation as cv
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_BBMD_ADDRESS,
    CONF_BBMD_TTL,
    CONF_CLIMATE_TEMPERATURE_SOURCES,
    CONF_COV_INCREMENT,
    CONF_COV_OVERRIDES,
    CONF_DOMAIN_MAPPING,
    CONF_ENABLE_COV,
    CONF_LIVE_METADATA_PROPERTIES,
    CONF_LOCAL_IP,
    CONF_LOCAL_PORT,
    CONF_POLLING_INTERVAL,
    CONF_SELECTED_OBJECTS,
    CONF_USE_BBMD,
    CONF_USE_DESCRIPTION,
    DEFAULT_COV_INCREMENT,
    DEFAULT_ENABLE_COV,
    DEFAULT_LIVE_METADATA_PROPERTIES,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_USE_DESCRIPTION,
    DOMAIN,
)
from .helpers import (
    default_domain_for,
    object_key,
    object_label,
    stale_domain_overrides,
)

_LOGGER = logging.getLogger(__name__)

# All platforms that this integration can dynamically register entities on.
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.CLIMATE,
    Platform.SELECT,
    Platform.BUTTON,
]


# Config entries only — no YAML configuration.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


def _domain_for_object(obj: dict, domain_overrides: dict[str, str]) -> str:
    """Return the HA domain for a BACnet object, applying user overrides."""
    return domain_overrides.get(object_key(obj)) or default_domain_for(obj)


def _get_platforms_in_use(
    objects: list[dict], domain_overrides: dict[str, str]
) -> list[Platform]:
    """Determine which HA platforms are actually needed based on selected objects.

    This avoids setting up platform files that have zero entities, which
    keeps startup quick and log output clean.
    """
    domains_needed: set[str] = set()
    for obj in objects:
        domains_needed.add(_domain_for_object(obj, domain_overrides))
    return [Platform(d) for d in domains_needed if d in {p.value for p in PLATFORMS}]


# ---------------------------------------------------------------------------
# Entity registry migration
# ---------------------------------------------------------------------------


def _migrate_unique_ids(
    hass: HomeAssistant, entry: ConfigEntry, device_id: int | None
) -> None:
    """Migrate entity unique_ids from the 1.0.17 format to the 1.0.18+ format.

    In ≤1.0.17 the unique_id was:
        ``{entry.entry_id}_{object_type}_{instance}``

    In ≥1.0.18 it is:
        ``{DOMAIN}_{device_id}_{object_type}_{instance}``

    Using the BACnet device_id instead of the config-entry id makes unique_ids
    survive a remove-and-re-add of the integration.  However the format change
    itself was a regression: users who renamed entity IDs lost those names on
    update.  This migration repairs that.

    Two cases are handled:

    Case A — user is upgrading from 1.0.17 (old entry, no new entry yet):
        Rename the old unique_id → new unique_id so that when the platform
        creates the entity with the new unique_id it finds the existing entry
        and preserves the customised entity_id.

    Case B — user already updated to 1.0.18 (both old orphaned entry AND new
        active entry exist):
        If the orphaned entry had a different entity_id (i.e. the user had
        renamed it), apply that entity_id to the active new entry then remove
        the orphaned entry.
    """
    if device_id is None:
        return

    ent_reg = er.async_get(hass)
    old_prefix = f"{entry.entry_id}_"
    new_prefix = f"{DOMAIN}_{device_id}_"

    for entity_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        uid = entity_entry.unique_id
        if not uid.startswith(old_prefix):
            continue

        suffix = uid[len(old_prefix) :]
        new_uid = f"{new_prefix}{suffix}"

        # Does a new-format entry already exist?
        existing_entity_id = ent_reg.async_get_entity_id(
            entity_entry.domain, DOMAIN, new_uid
        )

        if existing_entity_id is None:
            # Case A: no new-format entry yet — just rename.
            ent_reg.async_update_entity(entity_entry.entity_id, new_unique_id=new_uid)
            _LOGGER.debug("Migrated entity unique_id %s → %s", uid, new_uid)
        else:
            # Case B: new-format entry already exists.
            # If the old entry had a customised entity_id, apply it to the
            # new entry (remove old first to free the entity_id name).
            old_entity_id = entity_entry.entity_id
            ent_reg.async_remove(old_entity_id)

            if old_entity_id != existing_entity_id:
                try:
                    ent_reg.async_update_entity(
                        existing_entity_id, new_entity_id=old_entity_id
                    )
                    _LOGGER.debug(
                        "Restored custom entity_id %s on %s (migration)",
                        old_entity_id,
                        new_uid,
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.debug(
                        "Could not restore entity_id %s → %s (skipping)",
                        old_entity_id,
                        new_uid,
                    )


# ---------------------------------------------------------------------------
# Repairs
# ---------------------------------------------------------------------------


def _async_check_stale_overrides(
    hass: HomeAssistant,
    entry: ConfigEntry,
    objects: list[dict[str, Any]],
    domain_overrides: dict[str, str],
) -> None:
    """Raise (or clear) a fixable issue for overrides left by the options-flow bug.

    Not auto-removed: the domain mapping is the user's, so they confirm the
    cleanup in Settings → Repairs (see repairs.py).
    """
    issue_id = f"stale_domain_overrides_{entry.entry_id}"
    stale = set(stale_domain_overrides(objects, domain_overrides))
    if not stale:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="stale_domain_overrides",
        translation_placeholders={
            "device": entry.data.get("device_name", "BACnet Device"),
            "objects": ", ".join(
                f"{object_label(o)} → {domain_overrides[object_key(o)]}"
                for o in objects
                if object_key(o) in stale
            ),
        },
        data={"entry_id": entry.entry_id},
    )


# ---------------------------------------------------------------------------
# Integration lifecycle
# ---------------------------------------------------------------------------


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the integration-wide services (once, not per entry)."""
    from .services import async_setup_services  # noqa: WPS433

    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up BACnet IP from a config entry.

    This is called by Home Assistant when the user completes the config flow
    or when HA starts and an existing config entry is loaded.

    Lifecycle:
    1. Create a BACnetClient and connect it to the network.
    2. Optionally register as a Foreign Device with a BBMD.
    3. Build the data coordinator for COV + polling fallback.
    4. Store runtime references in entry.runtime_data for the platforms.
    5. Forward setup to the required platform files.
    """
    # Lazy import to avoid loading BACpypes3 at integration discovery time
    from .bacnet_client import BACnetClient  # noqa: WPS433
    from .coordinator import BACnetCoordinator, BACnetRuntimeData  # noqa: WPS433

    hass.data.setdefault(DOMAIN, {})

    # ---- 1. Extract configuration ----
    local_ip: str = entry.data.get(CONF_LOCAL_IP, "")
    local_port: int = entry.data.get(CONF_LOCAL_PORT, 47808)
    use_bbmd: bool = entry.data.get(CONF_USE_BBMD, False)
    bbmd_address: str = entry.data.get(CONF_BBMD_ADDRESS, "")
    bbmd_ttl: int = entry.data.get(CONF_BBMD_TTL, 900)
    selected_objects: list[dict[str, Any]] = entry.data.get(CONF_SELECTED_OBJECTS, [])

    # Options (may be updated at runtime via options_flow)
    enable_cov: bool = entry.options.get(CONF_ENABLE_COV, DEFAULT_ENABLE_COV)
    polling_interval: int = entry.options.get(
        CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL
    )
    use_description: bool = entry.options.get(
        CONF_USE_DESCRIPTION, DEFAULT_USE_DESCRIPTION
    )
    domain_overrides: dict[str, str] = entry.options.get(CONF_DOMAIN_MAPPING, {})
    cov_overrides: dict[str, bool] = entry.options.get(CONF_COV_OVERRIDES, {})
    cov_increment: float = entry.options.get(CONF_COV_INCREMENT, DEFAULT_COV_INCREMENT)
    live_metadata_properties: list[str] = entry.options.get(
        CONF_LIVE_METADATA_PROPERTIES, DEFAULT_LIVE_METADATA_PROPERTIES
    )
    climate_temperature_sources: dict[str, str] = entry.options.get(
        CONF_CLIMATE_TEMPERATURE_SOURCES, {}
    )

    # ---- 2. Get or create a shared BACnet client for this port ----
    # A single UDP socket (one BACnetClient) can communicate with any number
    # of remote BACnet devices — there is no protocol reason to have one socket
    # per target device.  We key shared clients by local_port so that a second
    # config entry on the same port reuses the already-bound socket rather than
    # trying to bind the same port a second time (which would fail at the OS level).
    port_clients: dict = hass.data[DOMAIN].setdefault("_port_clients", {})

    if local_port in port_clients:
        client = port_clients[local_port]["client"]
        port_clients[local_port]["ref_count"] += 1
        _LOGGER.info(
            "Reusing shared BACnet client on port %d (ref_count=%d)",
            local_port,
            port_clients[local_port]["ref_count"],
        )
    else:
        client = BACnetClient(
            local_ip=local_ip,
            local_port=local_port,
        )
        try:
            await client.connect(
                bbmd_address=bbmd_address if use_bbmd else None,
                bbmd_ttl=bbmd_ttl,
            )
        except Exception as exc:
            _LOGGER.error("Failed to start BACnet client: %s", exc)
            raise ConfigEntryNotReady(
                f"Cannot connect to BACnet network: {exc}"
            ) from exc
        port_clients[local_port] = {"client": client, "ref_count": 1}
        _LOGGER.info("Created shared BACnet client on port %d", local_port)

    # ---- 4. Build coordinator ----
    coordinator = BACnetCoordinator(
        hass=hass,
        client=client,
        objects=selected_objects,
        enable_cov=enable_cov,
        polling_interval=polling_interval,
        use_description=use_description,
        domain_overrides=domain_overrides,
        cov_overrides=cov_overrides,
        entry=entry,
        cov_increment=cov_increment,
        live_metadata_properties=live_metadata_properties,
        climate_temperature_sources=climate_temperature_sources,
    )

    # Perform the first data refresh so entities have initial state. On
    # failure HA never calls async_unload_entry, so undo this setup here —
    # otherwise the shared socket's ref_count (and any COV subscription
    # already created on it) leaks on every retry.
    try:
        await coordinator.async_config_entry_first_refresh()
    except BaseException:
        await coordinator.async_shutdown()
        await _async_release_client(hass, client)
        raise

    # ---- 5. Store runtime data ----
    entry.runtime_data = BACnetRuntimeData(coordinator=coordinator)

    # ---- 6. Migrate legacy unique_ids (1.0.17 → 1.0.18+ format) ----
    # Must run before platforms load so entities find the migrated registry entries.
    _migrate_unique_ids(hass, entry, entry.data.get("device_id"))

    _async_check_stale_overrides(hass, entry, selected_objects, domain_overrides)

    # ---- 7. Forward to platforms ----
    needed_platforms = _get_platforms_in_use(selected_objects, domain_overrides)
    # SELECT (write priority), BUTTON (metadata refresh) and SENSOR
    # (diagnostics) are device-level, not object-dependent.
    for device_level_platform in (Platform.SELECT, Platform.BUTTON, Platform.SENSOR):
        if device_level_platform not in needed_platforms:
            needed_platforms.append(device_level_platform)
    entry.runtime_data.platforms = needed_platforms
    await hass.config_entries.async_forward_entry_setups(entry, needed_platforms)

    # ---- 8. Listen for option changes ----
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    _LOGGER.info(
        "BACnet integration setup complete for device '%s' with %d objects",
        entry.data.get("device_name", "unknown"),
        len(selected_objects),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a BACnet config entry.

    Called when the user removes the integration or during HA shutdown.
    Cleans up:
    - COV subscriptions
    - Polling tasks
    - BACnet network connection
    """
    runtime_data = getattr(entry, "runtime_data", None)
    if runtime_data is None:
        return True

    # Unload platforms (the update listener is removed via async_on_unload)
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, runtime_data.platforms
    )

    if unload_ok:
        # Shut down coordinator — cancels only this entry's COV subscriptions
        coordinator = runtime_data.coordinator
        await coordinator.async_shutdown()

        await _async_release_client(hass, coordinator.client)

        _LOGGER.info("BACnet integration unloaded for entry %s", entry.entry_id)

    return unload_ok


async def _async_release_client(hass: HomeAssistant, client: Any) -> None:
    """Drop one reference to a shared per-port client; disconnect the last one."""
    if client is None:
        return
    # The client's own port, not entry.data: a reconfigure may have changed
    # the configured port before this reload.
    local_port = client.local_port
    port_clients = hass.data[DOMAIN].get("_port_clients", {})
    shared = port_clients.get(local_port)
    if shared is None or shared["client"] is not client:
        # Fallback for entries created before shared-client support
        await client.disconnect()
        return
    shared["ref_count"] -= 1
    if shared["ref_count"] <= 0:
        port_clients.pop(local_port)
        await client.disconnect()
        _LOGGER.debug("Disconnected shared BACnet client on port %d", local_port)
    else:
        _LOGGER.debug(
            "Released client reference for port %d (ref_count=%d remaining)",
            local_port,
            shared["ref_count"],
        )


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update.

    When the user changes options (COV, polling interval, naming, domain mapping)
    we reload the entire config entry so all entities and the coordinator
    pick up the new settings cleanly.
    """
    _LOGGER.debug("Options updated for BACnet entry %s — reloading", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop this entry's repair issue so it can't outlive the device."""
    ir.async_delete_issue(hass, DOMAIN, f"stale_domain_overrides_{entry.entry_id}")
