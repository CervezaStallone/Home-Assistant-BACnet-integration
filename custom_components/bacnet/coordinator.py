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

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .bacnet_client import BACnetClient
from .const import (
    CONF_DOMAIN_MAPPING,
    CONF_ENABLE_COV,
    CONF_POLLING_INTERVAL,
    CONF_USE_DESCRIPTION,
    DEFAULT_DOMAIN_MAP,
    DEFAULT_ENABLE_COV,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_USE_DESCRIPTION,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# COV subscriptions are renewed at 80% of their lifetime to avoid expiry gaps
COV_LIFETIME_SECONDS = 300
COV_RENEWAL_FACTOR = 0.8


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
        entry: ConfigEntry | None = None,
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
            entry: The ConfigEntry for accessing device addressing info.
        """
        self.client = client
        self.objects = objects
        self.enable_cov = enable_cov
        self.polling_interval = polling_interval
        self.use_description = use_description
        self.domain_overrides = domain_overrides or {}
        self.entry = entry

        # Track which objects have active COV and which need polling
        self._cov_subscriptions: dict[str, str] = {}  # obj_key → sub_key
        self._polled_objects: list[dict[str, Any]] = []

        # COV renewal task handle
        self._cov_renewal_task: asyncio.Task | None = None

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
        initial poll of ALL objects (including COV-subscribed ones) so that
        entities have state immediately rather than waiting for the first
        COV notification.

        Subsequent calls only poll objects that are NOT covered by COV.

        Returns:
            Dict keyed by "object_type:instance" → {property: value}.
        """
        # Use existing data as base (COV may have already pushed updates)
        data: dict[str, Any] = dict(self.data) if self.data else {}

        # --- First run: set up COV and do initial poll of everything ---
        first_run = not self._cov_subscriptions and not self._polled_objects
        if first_run:
            await self._setup_subscriptions()

        # Determine which objects to poll this cycle:
        # First run → all objects (initial state), subsequent → polled only
        objects_to_poll = self.objects if first_run else self._polled_objects

        # --- Poll objects ---
        for obj in objects_to_poll:
            obj_key = f"{obj['object_type']}:{obj['instance']}"
            try:
                values = await self.client.read_multiple_properties(
                    device_address=self.device_address,
                    object_type=obj["object_type"],
                    instance=obj["instance"],
                    property_names=["presentValue", "statusFlags"],
                )
                data[obj_key] = values
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "Polling failed for %s: %s",
                    obj_key,
                    exc,
                )
                # Keep stale data rather than failing everything
                if obj_key not in data:
                    data[obj_key] = {"presentValue": None, "statusFlags": None}

        return data

    # ------------------------------------------------------------------
    # COV subscription management
    # ------------------------------------------------------------------

    async def _setup_subscriptions(self) -> None:
        """Attempt COV subscriptions for all objects. Objects that fail get polled."""
        self._polled_objects = []

        for obj in self.objects:
            obj_key = f"{obj['object_type']}:{obj['instance']}"

            if self.enable_cov:
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

            # COV disabled or failed — add to polling list
            self._polled_objects.append(obj)
            _LOGGER.debug("Polling fallback for %s", obj_key)

        # Start COV renewal background task if we have subscriptions
        if self._cov_subscriptions and self._cov_renewal_task is None:
            self._cov_renewal_task = asyncio.create_task(self._cov_renewal_loop())

    @callback
    def _handle_cov_notification(self, changed_values: dict[str, Any]) -> None:
        """Process an incoming COV notification and push update to entities.

        This is called from the BACnetClient when BACpypes3 delivers a COV
        notification. We merge the changed values into our data dict and
        tell HA to update affected entities.
        """
        if self.data is None:
            return

        # changed_values should contain the object key and updated properties
        data = dict(self.data)
        for obj_key, values in changed_values.items():
            if obj_key in data:
                data[obj_key].update(values)
            else:
                data[obj_key] = values

        self.async_set_updated_data(data)

    async def _cov_renewal_loop(self) -> None:
        """Periodically renew COV subscriptions before they expire."""
        renewal_interval = COV_LIFETIME_SECONDS * COV_RENEWAL_FACTOR
        while True:
            try:
                await asyncio.sleep(renewal_interval)
                _LOGGER.debug("Renewing %d COV subscriptions", len(self._cov_subscriptions))
                await self.client.renew_cov_subscriptions()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                _LOGGER.warning("COV renewal loop error (will retry)")

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def async_shutdown(self) -> None:
        """Cancel COV subscriptions and background tasks."""
        # Cancel renewal task
        if self._cov_renewal_task is not None:
            self._cov_renewal_task.cancel()
            try:
                await self._cov_renewal_task
            except asyncio.CancelledError:
                pass
            self._cov_renewal_task = None

        # Unsubscribe all COV
        for obj_key, sub_key in list(self._cov_subscriptions.items()):
            await self.client.unsubscribe_cov(sub_key)
        self._cov_subscriptions.clear()
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

    def get_domain_for_object(self, obj: dict[str, Any]) -> str:
        """Determine the HA domain for a BACnet object, respecting user overrides."""
        obj_key = f"{obj['object_type']}:{obj['instance']}"
        return self.domain_overrides.get(
            obj_key, DEFAULT_DOMAIN_MAP.get(obj["object_type"], "sensor")
        )

    def get_entity_name(self, obj: dict[str, Any]) -> str:
        """Return the entity display name, respecting the use_description option."""
        if self.use_description and obj.get("description"):
            return obj["description"]
        return obj.get("object_name", f"BACnet {obj['object_type']}:{obj['instance']}")
