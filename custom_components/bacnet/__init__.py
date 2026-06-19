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

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_BBMD_ADDRESS,
    CONF_BBMD_TTL,
    CONF_COV_INCREMENT,
    CONF_COV_OVERRIDES,
    CONF_DOMAIN_MAPPING,
    CONF_ENABLE_COV,
    CONF_FIRMWARE_VERSION,
    CONF_LOCAL_IP,
    CONF_LOCAL_PORT,
    CONF_MODEL_NAME,
    CONF_POLLING_INTERVAL,
    CONF_SELECTED_OBJECTS,
    CONF_SOFTWARE_VERSION,
    CONF_USE_BBMD,
    CONF_USE_DESCRIPTION,
    CONF_VENDOR_NAME,
    DATA_CLIENT,
    DATA_COORDINATOR,
    DATA_DEVICE_INFO,
    DATA_OBJECTS,
    DATA_UNSUB,
    DEFAULT_COV_INCREMENT,
    DEFAULT_DOMAIN_MAP,
    DEFAULT_ENABLE_COV,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_USE_DESCRIPTION,
    DOMAIN,
    OBJECT_TYPE_ANALOG_VALUE,
    OBJECT_TYPE_BINARY_VALUE,
    OBJECT_TYPE_MULTI_STATE_VALUE,
)

_LOGGER = logging.getLogger(__name__)

# All platforms that this integration can dynamically register entities on.
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.CLIMATE,
]


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


# Value-type objects that need commandability check before assigning domain
_VALUE_TYPES = {
    OBJECT_TYPE_ANALOG_VALUE,
    OBJECT_TYPE_BINARY_VALUE,
    OBJECT_TYPE_MULTI_STATE_VALUE,
}


def _domain_for_object(obj: dict, domain_overrides: dict[str, str]) -> str:
    """Return the HA domain for a BACnet object, applying commandable-aware defaults.

    Mirrors coordinator._default_domain_for() so that _get_platforms_in_use()
    and the coordinator always agree on which domain an object belongs to.
    Must stay in sync with BACnetCoordinator._default_domain_for().
    """
    obj_key = f"{obj['object_type']}:{obj['instance']}"
    if obj_key in domain_overrides:
        return domain_overrides[obj_key]
    obj_type = obj["object_type"]
    if obj_type in _VALUE_TYPES:
        commandable = obj.get("commandable", False)
        if obj_type == OBJECT_TYPE_BINARY_VALUE:
            return "switch" if commandable else "binary_sensor"
        # AV and MSV
        return "number" if commandable else "sensor"
    return DEFAULT_DOMAIN_MAP.get(obj_type, "sensor")


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
# Integration lifecycle
# ---------------------------------------------------------------------------


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up BACnet IP from a config entry.

    This is called by Home Assistant when the user completes the config flow
    or when HA starts and an existing config entry is loaded.

    Lifecycle:
    1. Create a BACnetClient and connect it to the network.
    2. Optionally register as a Foreign Device with a BBMD.
    3. Build the data coordinator for COV + polling fallback.
    4. Store runtime references in hass.data so platforms can access them.
    5. Forward setup to the required platform files.
    """
    # Lazy import to avoid loading BACpypes3 at integration discovery time
    from .bacnet_client import BACnetClient  # noqa: WPS433
    from .coordinator import BACnetCoordinator  # noqa: WPS433

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
        except Exception as exc:  # noqa: BLE001
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
    )

    # Perform the first data refresh so entities have initial state
    await coordinator.async_config_entry_first_refresh()

    # ---- 5. Store runtime data ----
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_CLIENT: client,
        DATA_COORDINATOR: coordinator,
        DATA_OBJECTS: selected_objects,
        DATA_DEVICE_INFO: {
            "device_id": entry.data.get("device_id"),
            "device_name": entry.data.get("device_name", "BACnet Device"),
            "device_address": entry.data.get("device_address", ""),
            "vendor_name": entry.data.get(CONF_VENDOR_NAME, ""),
            "model_name": entry.data.get(CONF_MODEL_NAME, ""),
            "firmware_version": entry.data.get(CONF_FIRMWARE_VERSION, ""),
            "software_version": entry.data.get(CONF_SOFTWARE_VERSION, ""),
        },
        DATA_UNSUB: [],
    }

    # ---- 6. Migrate legacy unique_ids (1.0.17 → 1.0.18+ format) ----
    # Must run before platforms load so entities find the migrated registry entries.
    _migrate_unique_ids(hass, entry, entry.data.get("device_id"))

    # ---- 7. Forward to platforms ----
    needed_platforms = _get_platforms_in_use(selected_objects, domain_overrides)
    await hass.config_entries.async_forward_entry_setups(entry, needed_platforms)

    # ---- 8. Listen for option changes ----
    unsub = entry.add_update_listener(_async_options_updated)
    hass.data[DOMAIN][entry.entry_id][DATA_UNSUB].append(unsub)

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
    - hass.data references
    """
    entry_data = hass.data[DOMAIN].get(entry.entry_id)
    if entry_data is None:
        return True

    # Determine which platforms were loaded
    domain_overrides: dict[str, str] = entry.options.get(CONF_DOMAIN_MAPPING, {})
    selected_objects = entry_data.get(DATA_OBJECTS, [])
    needed_platforms = _get_platforms_in_use(selected_objects, domain_overrides)

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, needed_platforms
    )

    if unload_ok:
        # Cancel update listener subscriptions
        for unsub in entry_data.get(DATA_UNSUB, []):
            unsub()

        # Shut down coordinator — cancels only this entry's COV subscriptions
        coordinator = entry_data.get(DATA_COORDINATOR)
        if coordinator is not None:
            await coordinator.async_shutdown()

        # Release the shared client reference.  Only disconnect the underlying
        # UDP socket when the last config entry using this port is unloaded.
        client = entry_data.get(DATA_CLIENT)
        if client is not None:
            local_port = entry.data.get(CONF_LOCAL_PORT, 47808)
            port_clients = hass.data[DOMAIN].get("_port_clients", {})
            if local_port in port_clients:
                port_clients[local_port]["ref_count"] -= 1
                if port_clients[local_port]["ref_count"] <= 0:
                    port_clients.pop(local_port)
                    await client.disconnect()
                    _LOGGER.debug(
                        "Disconnected shared BACnet client on port %d", local_port
                    )
                else:
                    _LOGGER.debug(
                        "Released client reference for port %d (ref_count=%d remaining)",
                        local_port,
                        port_clients[local_port]["ref_count"],
                    )
            else:
                # Fallback for entries created before shared-client support
                await client.disconnect()

        hass.data[DOMAIN].pop(entry.entry_id)
        _LOGGER.info("BACnet integration unloaded for entry %s", entry.entry_id)

    return unload_ok


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update.

    When the user changes options (COV, polling interval, naming, domain mapping)
    we reload the entire config entry so all entities and the coordinator
    pick up the new settings cleanly.
    """
    _LOGGER.debug("Options updated for BACnet entry %s — reloading", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)
