"""
Data update coordinator for BACnet IP integration.

Manages two update strategies per BACnet object:
  1. COV (Change of Value) — preferred, event-driven, low latency
  2. Polling fallback — used when COV is disabled, unsupported, or subscription fails

The coordinator also handles:
  - COV subscription lifecycle (subscribe, renew, unsubscribe)
  - Aggregating updates from both COV and polling into a single data dict
  - Triggering HA entity state updates via async_set_updated_data
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .bacnet_client import BACnetClient
from .const import (
    CONF_SELECTED_OBJECTS,
    COV_METADATA_CHECK_INTERVAL,
    DEFAULT_COV_INCREMENT,
    DEFAULT_DOMAIN_MAP,
    DEFAULT_ENABLE_COV,
    DEFAULT_METADATA_REFRESH_INTERVAL,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_USE_DESCRIPTION,
    DEFAULT_WRITE_PRIORITY,
    DOMAIN,
    LIVE_METADATA_BACNET_TO_PROPERTY,
    LIVE_METADATA_PROPERTY_TO_BACNET,
    MAX_SILENT_FAILURES,
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
    OBJECT_TYPE_BINARY_VALUE,
    OBJECT_TYPE_MULTI_STATE_VALUE,
    RECONNECT_THRESHOLD,
)

_LOGGER = logging.getLogger(__name__)

# COV subscription lifetime.  BACpypes3's change_of_value() context manager
# automatically renews the subscription before it expires.
COV_LIFETIME_SECONDS = 300


class BACnetCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate BACnet data updates for one device.

    self.data is a dict keyed by "object_type:instance", each value being a dict
    of the latest known property values for that object. Example:

        {
            "0:1": {"presentValue": 23.5, "statusFlags": [0,0,0,0]},
            "4:3": {"presentValue": 1, "statusFlags": [0,0,0,0]},
        }
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: BACnetClient,
        objects: list[dict[str, Any]],
        enable_cov: bool = DEFAULT_ENABLE_COV,
        polling_interval: int = DEFAULT_POLLING_INTERVAL,
        use_description: bool = DEFAULT_USE_DESCRIPTION,
        domain_overrides: dict[str, str] | None = None,
        cov_overrides: dict[str, bool] | None = None,
        entry: ConfigEntry | None = None,
        cov_increment: float = DEFAULT_COV_INCREMENT,
        live_metadata_properties: list[str] | None = None,
    ) -> None:
        """Initialise the coordinator.

        Args:
            hass: Home Assistant instance.
            client: Connected BACnetClient.
            objects: List of selected BACnet object dicts from config entry.
            enable_cov: Whether COV subscriptions should be attempted.
            polling_interval: Fallback polling interval in seconds.
            use_description: If True, use description (prop 28) for entity names.
            domain_overrides: Per-object HA domain overrides from options flow.
            cov_overrides: Per-object COV enable/disable overrides from options
                flow. Objects with no entry fall back to `enable_cov`.
            entry: The ConfigEntry for accessing device addressing info.
            cov_increment: COV increment for analog objects (0.0 = device default).
            live_metadata_properties: Which static properties (object_name,
                description, units) get a live SubscribeCOVProperty
                subscription instead of the periodic/COV-triggered
                ReadProperty refresh. Empty by default (issue #26).
        """
        self.client = client
        self.objects = objects
        self.enable_cov = enable_cov
        self.polling_interval = polling_interval
        self.use_description = use_description
        self.domain_overrides = domain_overrides or {}
        self.cov_overrides = cov_overrides or {}
        self.entry = entry
        self.cov_increment = cov_increment
        self.live_metadata_properties = live_metadata_properties or []
        self.write_priority: int = DEFAULT_WRITE_PRIORITY

        # Track which objects have active COV and which need polling
        self._cov_subscriptions: dict[str, str] = {}  # obj_key → sub_key
        self._cov_property_subscriptions: dict[
            str, str
        ] = {}  # "obj_key:prop" → sub_key
        self._polled_objects: list[dict[str, Any]] = []

        # Outage-recovery state (issue #18).
        # _consecutive_failures counts polls in a row that returned no usable
        # data.  At MAX_SILENT_FAILURES we raise UpdateFailed so HA surfaces
        # the outage + applies native backoff.  At RECONNECT_THRESHOLD we
        # additionally reconnect the underlying BACnetClient once (re-registers
        # with the BBMD whose TTL has expired).  _needs_resubscribe is set
        # after a reconnect so the next successful poll re-creates COV subs.
        self._consecutive_failures: int = 0
        self._needs_resubscribe: bool = False

        # Metadata refresh (issue #26). Skip an immediate refresh right after
        # setup — the config flow just read fresh metadata during discovery.
        self._last_metadata_refresh: datetime = datetime.now(timezone.utc)
        self._last_object_metadata_check: dict[str, datetime] = {}

        # Device address for reads/writes (from config entry data)
        self.device_address: str = ""
        if entry is not None:
            self.device_address = entry.data.get("device_address", "")

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id if entry else 'unknown'}",
            update_interval=timedelta(seconds=polling_interval),
        )

    # ------------------------------------------------------------------
    # First refresh — sets up COV subscriptions
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest data for all objects.

        On the first call this also sets up COV subscriptions and does an
        initial poll of ALL objects so that entities have state immediately.

        **Every** subsequent call polls ALL objects too, regardless of COV
        status.  COV provides faster intermediate updates between polls,
        but polling is the reliable baseline that guarantees values are
        always refreshed — even when a device accepts a COV subscription
        but never actually sends notifications.

        Outage recovery (issue #18): if every object fails to return a real
        presentValue for MAX_SILENT_FAILURES polls in a row, raise
        UpdateFailed so HA surfaces the outage and applies native backoff.
        At RECONNECT_THRESHOLD consecutive failures, additionally reconnect
        the underlying BACnetClient once (re-registers with the BBMD whose
        TTL has expired).  After a successful poll following a reconnect,
        COV subscriptions are re-created.

        Returns:
            Dict keyed by "object_type:instance" → {property: value}.

        Raises:
            UpdateFailed: when the device has been unreachable for
                          MAX_SILENT_FAILURES consecutive polls.
        """
        # Use existing data as base (COV may have already pushed updates)
        data: dict[str, Any] = dict(self.data) if self.data else {}

        # --- First run: set up COV subscriptions ---
        first_run = not self._cov_subscriptions and not self._polled_objects
        if first_run:
            await self._setup_subscriptions()

        # Always poll ALL objects — COV is supplementary, polling is the
        # reliable baseline.  This ensures values update even when COV
        # subscriptions are accepted but notifications never arrive.
        # poll_objects() batches all objects into one ReadPropertyMultiple
        # request (single round-trip) and falls back to individual reads for
        # devices that reject RPM.
        try:
            polled = await self.client.poll_objects(
                device_address=self.device_address,
                objects=self.objects,
                property_names=["presentValue", "statusFlags"],
            )
            data.update(polled)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Batch poll failed: %s — keeping stale data", exc)
            polled = None
            for obj in self.objects:
                obj_key = f"{obj['object_type']}:{obj['instance']}"
                if obj_key not in data:
                    data[obj_key] = {"presentValue": None, "statusFlags": None}

        # --- Outage detection -------------------------------------------------
        # A poll is "successful" if at least one object returned a non-None
        # presentValue.  Empty / all-None results mean the device is not
        # responding — even when poll_objects() returned without raising,
        # BACpypes3 timeouts surface as None values rather than exceptions.
        if self._poll_yielded_data(polled):
            self._consecutive_failures = 0
            # After a reconnect, rebuild COV subscriptions once the device is
            # confirmed reachable again.
            if self._needs_resubscribe:
                await self._restore_subscriptions()

            # Piggyback the metadata refresh on this same successful cycle
            # rather than a dedicated timer — the device is already confirmed
            # reachable, so this is the cheapest point to check for changes.
            elapsed = datetime.now(timezone.utc) - self._last_metadata_refresh
            if elapsed >= timedelta(seconds=DEFAULT_METADATA_REFRESH_INTERVAL):
                await self.async_refresh_metadata()
        else:
            await self._handle_poll_failure()

        return data

    @staticmethod
    def _poll_yielded_data(
        polled: dict[str, dict[str, Any]] | None,
    ) -> bool:
        """Return True if at least one object returned a real presentValue."""
        if not polled:
            return False
        for obj_data in polled.values():
            if obj_data.get("presentValue") is not None:
                return True
        return False

    async def _handle_poll_failure(self) -> None:
        """Account for one failed poll and trigger recovery if needed.

        - Below MAX_SILENT_FAILURES: keep stale data (avoids flapping on
          brief network blips) and return normally.
        - At/above MAX_SILENT_FAILURES: raise UpdateFailed so HA surfaces
          the outage and applies native backoff.
        - At exactly RECONNECT_THRESHOLD: reconnect the BACnetClient once
          (re-registers with the BBMD after TTL expiry).
        """
        self._consecutive_failures += 1
        failures = self._consecutive_failures
        _LOGGER.warning(
            "BACnet device %s unresponsive (%d consecutive failed polls)",
            self.device_address or "(no address)",
            failures,
        )

        if failures == RECONNECT_THRESHOLD:
            _LOGGER.warning(
                "Attempting BACnet client reconnect after %d failed polls",
                failures,
            )
            try:
                await self.client.reconnect()
                self._needs_resubscribe = True
                _LOGGER.info(
                    "BACnet client reconnected — next poll will resubscribe COV"
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.error("BACnet client reconnect failed: %s", exc)

        if failures >= MAX_SILENT_FAILURES:
            raise UpdateFailed(
                f"BACnet device {self.device_address or '(no address)'} not "
                f"responding ({failures} consecutive failed polls)"
            )

    async def _restore_subscriptions(self) -> None:
        """Re-create COV subscriptions after a reconnect.

        The previous _cov_subscriptions mapping references sub_keys whose
        reader tasks died during the outage, so we clear both bookkeeping
        dicts and re-run _setup_subscriptions() which re-subscribes COV
        where possible and rebuilds the polling fallback list.
        """
        _LOGGER.info("Restoring COV subscriptions after reconnect")
        # Cancel any lingering sub keys (tasks are already dead post-reconnect
        # but clearing the mapping ensures a clean rebuild).
        self._cov_subscriptions.clear()
        self._cov_property_subscriptions.clear()
        self._polled_objects.clear()
        await self._setup_subscriptions()
        self._needs_resubscribe = False

    # ------------------------------------------------------------------
    # Static metadata refresh — issue #26
    # ------------------------------------------------------------------

    @staticmethod
    def _diff_metadata(obj: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
        """Return the object_name/description/units/commandable fields that
        differ between *obj* and *fresh* (empty dict if nothing changed).

        Deliberately does NOT mutate *obj*. self.objects is the SAME list
        of dicts as entry.data[CONF_SELECTED_OBJECTS] (assigned once by
        async_setup_entry) — HA's async_update_entry only fires the reload
        listener when `entry.data != data`, so mutating these dicts in
        place before that comparison makes old and new data compare equal
        and the reload silently never fires. unit_of_measurement/
        device_class then stay stale forever, even though
        extra_state_attributes (a live read of the mutated dict) looks
        correct. Confirmed on the v1.0.42-beta pre-release via issue #26.
        Callers must build a genuinely new dict/list instead — see
        _replace_object().
        """
        changed: dict[str, Any] = {}
        for key in ("object_name", "description", "units", "commandable"):
            if obj.get(key) != fresh.get(key):
                changed[key] = fresh[key]
        return changed

    def _replace_object(self, obj_key: str, updates: dict[str, Any]) -> None:
        """Point self.objects at a new list with obj_key's dict replaced by
        a copy carrying *updates* merged in — see _diff_metadata for why
        this must be a genuinely new object, not an in-place mutation.
        """
        self.objects = [
            {**o, **updates} if f"{o['object_type']}:{o['instance']}" == obj_key else o
            for o in self.objects
        ]

    def _persist_metadata_change(self) -> None:
        """Reload the config entry so entities pick up new units/device_class.

        A reload (not just an in-place dict mutation) is required because HA
        sensor entities read units/device_class once at __init__. Reuses the
        same options-update-listener path already used when the user edits
        options.
        """
        if self.entry is None:
            return
        _LOGGER.info("BACnet object metadata changed on device — reloading entry")
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={**self.entry.data, CONF_SELECTED_OBJECTS: self.objects},
        )

    async def async_refresh_metadata(self) -> bool:
        """Re-read objectName/description/units/commandable for every object.

        Safety-net sweep for polling-only objects, which never produce a COV
        notification to trigger the cheaper per-object check below. Runs on
        DEFAULT_METADATA_REFRESH_INTERVAL, piggybacked on the regular poll
        cycle rather than a dedicated timer (issue #26).

        Returns True if any object's metadata changed.
        """
        self._last_metadata_refresh = datetime.now(timezone.utc)
        new_objects: list[dict[str, Any]] = []
        changed = False

        for obj in self.objects:
            try:
                fresh = await self.client.refresh_object_metadata(
                    device_address=self.device_address,
                    object_type=obj["object_type"],
                    instance=obj["instance"],
                    current_commandable=obj.get("commandable", False),
                    current_object_name=obj.get("object_name"),
                    current_description=obj.get("description"),
                    current_units=obj.get("units"),
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug(
                    "Metadata refresh failed for %s:%s: %s",
                    obj["object_type"],
                    obj["instance"],
                    exc,
                )
                new_objects.append(obj)
                continue

            if fresh is None:
                new_objects.append(obj)
                continue

            updates = self._diff_metadata(obj, fresh)
            if updates:
                for key, value in updates.items():
                    _LOGGER.info(
                        "BACnet object %s:%s metadata changed: %s %r → %r",
                        obj["object_type"],
                        obj["instance"],
                        key,
                        obj.get(key),
                        value,
                    )
                obj = {**obj, **updates}
                changed = True
            new_objects.append(obj)

        if changed:
            self.objects = new_objects
            self._persist_metadata_change()

        return changed

    async def _refresh_object_metadata_now(self, obj_key: str) -> None:
        """Re-read metadata for one object, triggered by a live COV notification.

        BACnet COV only ever carries presentValue/statusFlags — there is no
        protocol-level push for a property like units changing. A COV
        notification is still the best available signal that the device is
        live and actively talking about this object right now, so it's used
        as the trigger point for a real ReadProperty check instead of waiting
        on the periodic sweep (issue #26).
        """
        obj = next(
            (
                o
                for o in self.objects
                if f"{o['object_type']}:{o['instance']}" == obj_key
            ),
            None,
        )
        if obj is None:
            return

        try:
            fresh = await self.client.refresh_object_metadata(
                device_address=self.device_address,
                object_type=obj["object_type"],
                instance=obj["instance"],
                current_commandable=obj.get("commandable", False),
                current_object_name=obj.get("object_name"),
                current_description=obj.get("description"),
                current_units=obj.get("units"),
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug(
                "COV-triggered metadata check failed for %s: %s", obj_key, exc
            )
            return

        if fresh is None:
            return

        updates = self._diff_metadata(obj, fresh)
        if not updates:
            return

        for key, value in updates.items():
            _LOGGER.info(
                "BACnet object %s metadata changed: %s %r → %r",
                obj_key,
                key,
                obj.get(key),
                value,
            )
        self._replace_object(obj_key, updates)
        self._persist_metadata_change()

    # ------------------------------------------------------------------
    # COV subscription management
    # ------------------------------------------------------------------

    # Analog object types that support covIncrement
    _ANALOG_TYPES: ClassVar = {
        OBJECT_TYPE_ANALOG_INPUT,
        OBJECT_TYPE_ANALOG_OUTPUT,
        OBJECT_TYPE_ANALOG_VALUE,
    }

    async def _setup_subscriptions(self) -> None:
        """Attempt COV subscriptions for all objects. Objects that fail get polled."""
        self._polled_objects = []

        for obj in self.objects:
            obj_key = f"{obj['object_type']}:{obj['instance']}"
            cov_for_object = self.cov_overrides.get(obj_key, self.enable_cov)

            if cov_for_object:
                # For analog objects, write the covIncrement to the device
                # before subscribing so the device uses the user's threshold.
                if self.cov_increment > 0 and obj["object_type"] in self._ANALOG_TYPES:
                    try:
                        await self.client.write_property(
                            device_address=self.device_address,
                            object_type=obj["object_type"],
                            instance=obj["instance"],
                            property_name="covIncrement",
                            value=self.cov_increment,
                        )
                        _LOGGER.debug(
                            "Set covIncrement=%.2f for %s",
                            self.cov_increment,
                            obj_key,
                        )
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug(
                            "Could not write covIncrement for %s (device may "
                            "not support it — using device default)",
                            obj_key,
                        )

                # Live metadata push (issue #26) — opt-in per property since
                # each one is a SEPARATE COV subscription on the device.
                # Rejected/unsupported properties simply keep using the
                # existing poll-cycle/COV-triggered ReadProperty refresh.
                for prop_key in self.live_metadata_properties:
                    bacnet_prop = LIVE_METADATA_PROPERTY_TO_BACNET.get(prop_key)
                    if bacnet_prop is None:
                        continue
                    prop_sub_key = await self.client.subscribe_cov_property(
                        device_address=self.device_address,
                        object_type=obj["object_type"],
                        instance=obj["instance"],
                        property_name=bacnet_prop,
                        callback=self._handle_cov_property_notification,
                        lifetime=COV_LIFETIME_SECONDS,
                    )
                    if prop_sub_key is not None:
                        self._cov_property_subscriptions[f"{obj_key}:{prop_key}"] = (
                            prop_sub_key
                        )
                        _LOGGER.debug(
                            "Live COV-Property active: %s for %s", prop_key, obj_key
                        )

                sub_key = await self.client.subscribe_cov(
                    device_address=self.device_address,
                    object_type=obj["object_type"],
                    instance=obj["instance"],
                    callback=self._handle_cov_notification,
                    lifetime=COV_LIFETIME_SECONDS,
                )
                if sub_key is not None:
                    self._cov_subscriptions[obj_key] = sub_key
                    _LOGGER.debug("COV active for %s", obj_key)
                    continue

            # COV disabled (globally or via per-object override) or failed —
            # add to polling list
            self._polled_objects.append(obj)
            _LOGGER.debug("Polling fallback for %s", obj_key)

        _LOGGER.info(
            "COV subscriptions: %d active, %d polling fallback",
            len(self._cov_subscriptions),
            len(self._polled_objects),
        )

        # BACpypes3 change_of_value() context manager handles renewal
        # automatically — no background renewal task needed.

    @callback
    def _handle_cov_notification(
        self, obj_key: str, changed_values: dict[str, Any]
    ) -> None:
        """Process an incoming COV notification and push update to entities.

        Called by the BACnetClient COV reader task whenever a property
        change is received.  We merge the changed properties into our
        data dict and tell HA to update affected entities.

        IMPORTANT: We update self.data directly and notify listeners
        instead of using async_set_updated_data(), because the latter
        resets the polling timer.  If COV notifications arrive frequently,
        that would prevent the scheduled _async_update_data poll from
        ever firing.

        Args:
            obj_key: Object identifier string ("object_type:instance").
            changed_values: Dict of changed property names → new values,
                            e.g. {"presentValue": 23.5}.
        """
        if self.data is None:
            return

        data = dict(self.data)
        if obj_key in data:
            data[obj_key].update(changed_values)
        else:
            data[obj_key] = changed_values

        # Update data and notify listeners WITHOUT resetting the poll timer.
        self.data = data
        self.async_update_listeners()

        self._maybe_schedule_metadata_check(obj_key)

    def _maybe_schedule_metadata_check(self, obj_key: str) -> None:
        """Schedule a COV-triggered metadata check for *obj_key*, throttled.

        _handle_cov_notification is a sync @callback (invoked directly by the
        COV reader task), so the actual ReadProperty check has to run as a
        background task — the same pattern used elsewhere in this integration
        for scheduling async cleanup from a sync HA callback.
        """
        now = datetime.now(timezone.utc)
        last_check = self._last_object_metadata_check.get(obj_key)
        if last_check is not None and (now - last_check) < timedelta(
            seconds=COV_METADATA_CHECK_INTERVAL
        ):
            return
        self._last_object_metadata_check[obj_key] = now
        self.hass.async_create_task(self._refresh_object_metadata_now(obj_key))

    @callback
    def _handle_cov_property_notification(
        self, obj_key: str, bacnet_property_name: str, value: Any
    ) -> None:
        """Process a live COV-Property notification (issue #26).

        Unlike _handle_cov_notification (presentValue/statusFlags →
        coordinator data), this replaces the object's own metadata dict
        with an updated copy and, if the value actually changed, reloads
        the entry the same way async_refresh_metadata does —
        units/device_class are read once at entity __init__, so an
        in-place dict mutation alone wouldn't update an already-created
        sensor entity (see _diff_metadata for why it must be a genuinely
        new dict, not a mutation).
        """
        internal_key = LIVE_METADATA_BACNET_TO_PROPERTY.get(bacnet_property_name)
        if internal_key is None:
            return

        obj = next(
            (
                o
                for o in self.objects
                if f"{o['object_type']}:{o['instance']}" == obj_key
            ),
            None,
        )
        if obj is None:
            return

        if obj.get(internal_key) == value:
            return

        _LOGGER.info(
            "BACnet object %s metadata changed via live COV-Property: %s %r → %r",
            obj_key,
            internal_key,
            obj.get(internal_key),
            value,
        )
        self._replace_object(obj_key, {internal_key: value})
        self._persist_metadata_change()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def async_shutdown(self) -> None:
        """Cancel this coordinator's COV subscriptions and clean up.

        Only unsubscribes subscriptions owned by this coordinator so that a
        shared BACnetClient (used by multiple coordinators on the same port)
        is not disrupted when one config entry is unloaded.
        """
        for sub_key in list(self._cov_subscriptions.values()):
            await self.client.unsubscribe_cov(sub_key)
        self._cov_subscriptions.clear()

        for sub_key in list(self._cov_property_subscriptions.values()):
            await self.client.unsubscribe_cov_property(sub_key)
        self._cov_property_subscriptions.clear()

        self._polled_objects.clear()

        _LOGGER.debug("Coordinator shutdown complete")

    # ------------------------------------------------------------------
    # Helpers for entity access
    # ------------------------------------------------------------------

    def get_object_value(self, obj_key: str, prop: str = "presentValue") -> Any:
        """Get the latest value for a specific object and property."""
        if self.data is None:
            return None
        obj_data = self.data.get(obj_key, {})
        return obj_data.get(prop)

    # Value-type objects that may or may not have a Priority Array
    _VALUE_TYPES: ClassVar = {
        OBJECT_TYPE_ANALOG_VALUE,
        OBJECT_TYPE_BINARY_VALUE,
        OBJECT_TYPE_MULTI_STATE_VALUE,
    }

    def get_domain_for_object(self, obj: dict[str, Any]) -> str:
        """Determine the HA domain for a BACnet object, respecting user overrides.

        For Value-type objects (AV, BV, MSV) the default domain depends on
        whether the object is commandable (has a Priority Array).  Non-commandable
        Value objects must not be mapped to writable domains (switch/number)
        because the device will reject writes or behave incorrectly.
        """
        obj_key = f"{obj['object_type']}:{obj['instance']}"
        if obj_key in self.domain_overrides:
            return self.domain_overrides[obj_key]
        return self._default_domain_for(obj)

    def _default_domain_for(self, obj: dict[str, Any]) -> str:
        """Return the default HA domain for a BACnet object based on type + commandability."""
        obj_type = obj["object_type"]
        if obj_type in self._VALUE_TYPES:
            commandable = obj.get("commandable", False)
            if obj_type == OBJECT_TYPE_BINARY_VALUE:
                return "switch" if commandable else "binary_sensor"
            if obj_type in {OBJECT_TYPE_ANALOG_VALUE, OBJECT_TYPE_MULTI_STATE_VALUE}:
                return "number" if commandable else "sensor"
        return DEFAULT_DOMAIN_MAP.get(obj_type, "sensor")

    def get_entity_name(self, obj: dict[str, Any]) -> str:
        """Return the entity display name, respecting the use_description option."""
        if self.use_description and obj.get("description"):
            return obj["description"]
        return obj.get("object_name", f"BACnet {obj['object_type']}:{obj['instance']}")

    def is_cov_subscribed(self, obj_key: str) -> bool:
        """Return True if this object has an active COV subscription."""
        return obj_key in self._cov_subscriptions

    def get_update_method(self, obj_key: str) -> str:
        """Return 'COV' or 'polling' for how this object is updated."""
        return "COV" if self.is_cov_subscribed(obj_key) else "polling"

    def get_cov_increment_for(self, obj_key: str) -> float | None:
        """Return the configured COV increment for analog objects, None for binary."""
        if not self.is_cov_subscribed(obj_key):
            return None
        parts = obj_key.split(":")
        if len(parts) == 2:
            obj_type = int(parts[0])
            if obj_type in self._ANALOG_TYPES:
                return self.cov_increment if self.cov_increment > 0 else None
        return None
