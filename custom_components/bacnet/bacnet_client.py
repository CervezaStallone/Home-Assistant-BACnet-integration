"""
BACnet client module — isolates all BACpypes3 interaction.

Responsibilities:
- Network connection (local bind + optional Foreign Device Registration with BBMD)
- Device discovery via Who-Is / I-Am
- Object list and property reads (ReadProperty / ReadPropertyMultiple)
- Property writes with Priority Array support and Null/Relinquish
- COV subscription management
- Commandability/writability detection
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any, Callable, Union

from bacpypes3.apdu import ErrorRejectAbortNack
from bacpypes3.ipv4.app import ForeignApplication, NormalApplication
from bacpypes3.local.device import DeviceObject
from bacpypes3.pdu import Address, IPv4Address
from bacpypes3.primitivedata import (
    CharacterString,
    Enumerated,
    Null,
    ObjectIdentifier,
    Real,
    Unsigned,
)

from .const import (
    DEFAULT_WRITE_PRIORITY,
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
    OBJECT_TYPE_BINARY_INPUT,
    OBJECT_TYPE_BINARY_OUTPUT,
    OBJECT_TYPE_BINARY_VALUE,
    OBJECT_TYPE_MULTI_STATE_INPUT,
    OBJECT_TYPE_MULTI_STATE_OUTPUT,
    OBJECT_TYPE_MULTI_STATE_VALUE,
)
from .helpers import mask_address as _mask_address

_LOGGER = logging.getLogger(__name__)


# BACnet object types we support importing as HA entities
SUPPORTED_OBJECT_TYPES: set[int] = {
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
    OBJECT_TYPE_BINARY_INPUT,
    OBJECT_TYPE_BINARY_OUTPUT,
    OBJECT_TYPE_BINARY_VALUE,
    OBJECT_TYPE_MULTI_STATE_INPUT,
    OBJECT_TYPE_MULTI_STATE_OUTPUT,
    OBJECT_TYPE_MULTI_STATE_VALUE,
}

# Object types that are inherently commandable (have a Priority Array)
COMMANDABLE_TYPES: set[int] = {
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_BINARY_OUTPUT,
    OBJECT_TYPE_MULTI_STATE_OUTPUT,
}

# Object types that *may* be writable (Values can optionally be commandable)
POTENTIALLY_WRITABLE_TYPES: set[int] = {
    OBJECT_TYPE_ANALOG_VALUE,
    OBJECT_TYPE_BINARY_VALUE,
    OBJECT_TYPE_MULTI_STATE_VALUE,
}

# Type alias for the application — either Normal or Foreign
_AppType = Union[NormalApplication, ForeignApplication]


class BACnetClient:
    """Wrapper around BACpypes3 providing a clean async API for HA.

    Usage:
        client = BACnetClient(local_ip="192.168.1.100", local_port=47808)
        await client.connect()                       # NormalApplication
        await client.connect(bbmd_address="x.x.x.x") # ForeignApplication
        devices = await client.discover_devices(timeout=5)
        objects = await client.read_object_list(device_address, device_id)
        value = await client.read_property(address, obj_type, instance, prop_id)
        await client.write_property(address, obj_type, instance, prop_id, value, priority=8)
        await client.disconnect()
    """

    def __init__(
        self,
        local_ip: str = "",
        local_port: int = 47808,
        device_instance: int | None = None,
    ) -> None:
        self._local_ip = local_ip
        self._local_port = local_port
        # Use caller-supplied instance or derive one from the port.
        # Range 3900000–4194302 is the high end of the BACnet instance space —
        # unlikely to collide with real building automation devices.
        self._device_instance = (
            device_instance
            if device_instance is not None
            else self._derive_device_instance(local_ip, local_port)
        )
        self._app: _AppType | None = None
        self._cov_tasks: dict[str, asyncio.Task] = {}
        # Per-device RPM support cache: True = supported (or untested), False = rejected
        self._rpm_supported: dict[str, bool] = {}

    @staticmethod
    def _derive_device_instance(local_ip: str, local_port: int) -> int:
        """Derive a stable, unique device instance from the local address.

        Uses SHA-256 (not Python's hash()) so the result is identical across
        every process restart — Python's built-in hash() is randomised by
        PYTHONHASHSEED and changes every time HA restarts.

        Maps into 3900000–4194302, the high end of the BACnet instance space
        that is unlikely to be used by real building automation devices.
        """
        seed = f"{local_ip}:{local_port}".encode()
        digest = hashlib.sha256(seed).digest()
        raw = int.from_bytes(digest[:4], "big")
        return 3900000 + (raw % 294303)  # 3900000–4194302

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _build_app_args(self) -> tuple[DeviceObject, IPv4Address]:
        """Prepare IPv4Address and device object for app construction."""
        if self._local_ip:
            local_addr = IPv4Address(f"{self._local_ip}:{self._local_port}")
        else:
            local_addr = IPv4Address(f"0.0.0.0:{self._local_port}")

        device_object = DeviceObject(
            objectIdentifier=("device", self._device_instance),
            objectName="HomeAssistant-BACnet",
            vendorIdentifier=0,
            maxApduLengthAccepted=1476,
            maxSegmentsAccepted=64,
            segmentationSupported="segmented-both",
        )
        return device_object, local_addr

    async def connect(
        self,
        bbmd_address: str | None = None,
        bbmd_ttl: int = 900,
    ) -> None:
        """Create the BACpypes3 application and bind to the network.

        BACpypes3 uses asyncio UDP transport internally, so the application
        constructor MUST be called from an async context with a running
        event loop.

        If bbmd_address is provided, a ForeignApplication is created and
        registered with the BBMD automatically.  Otherwise a
        NormalApplication is created for local-subnet communication.

        Args:
            bbmd_address: IP:port of the BBMD for cross-subnet communication.
                          If None, no BBMD registration is performed.
            bbmd_ttl: Time-to-live for foreign device registration (seconds).
        """
        device_object, local_addr = self._build_app_args()

        if bbmd_address:
            _LOGGER.debug(
                "Creating Foreign BACnet application on %s (BBMD=%s)",
                local_addr,
                _mask_address(bbmd_address),
            )
            self._app = ForeignApplication(device_object, local_addr)
            bbmd_addr = IPv4Address(bbmd_address)
            self._app.register(bbmd_addr, bbmd_ttl)
            _LOGGER.info(
                "BACnet Foreign Device registered with BBMD at %s (TTL=%ds)",
                _mask_address(bbmd_address),
                bbmd_ttl,
            )
        else:
            _LOGGER.debug("Creating Normal BACnet application on %s", local_addr)
            self._app = NormalApplication(device_object, local_addr)
            _LOGGER.info(
                "BACnet client connected on %s (type=%s)",
                local_addr,
                type(self._app).__name__,
            )

        # Wait for the UDP transport to be ready.  The NormalApplication
        # constructor schedules UDP endpoint creation as background tasks.
        # If we don't await them here, the first who_is / read_property may
        # silently fail because the socket is not yet bound.
        try:
            await self._wait_for_transport()
        except Exception:
            # Transport failed — clean up the app so the port is released.
            try:
                self._app.close()
            except Exception:  # noqa: BLE001
                pass
            self._app = None
            raise

    def _get_datagram_server(self):
        """Return the IPv4DatagramServer from the application stack.

        NormalApplication stores it at ``app.normal.server``;
        ForeignApplication stores it at ``app.server``.
        """
        if self._app is None:
            return None
        # NormalApplication wraps it inside NormalLinkLayer
        if hasattr(self._app, "normal"):
            return getattr(self._app.normal, "server", None)
        # ForeignApplication exposes it directly
        return getattr(self._app, "server", None)

    async def _wait_for_transport(self, timeout: float = 5.0) -> None:
        """Await the UDP transport tasks so the socket is actually bound.

        BACpypes3 schedules ``create_datagram_endpoint`` as background tasks
        in the ``IPv4DatagramServer`` constructor.  If the requested port is
        already in use, ``retrying_create_datagram_endpoint`` keeps retrying
        forever — our timeout detects that and raises early.
        """
        server = self._get_datagram_server()
        if server is None:
            _LOGGER.warning(
                "Cannot locate IPv4DatagramServer — skipping transport check"
            )
            return

        tasks = getattr(server, "_transport_tasks", [])
        if tasks:
            _LOGGER.debug("Waiting up to %.0fs for UDP transport …", timeout)
            try:
                await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
                server._transport_tasks = []
            except asyncio.TimeoutError:
                _LOGGER.error(
                    "UDP socket failed to bind within %.0fs — port %d may "
                    "already be in use. Try a different 'Local port' (e.g. 47809).",
                    timeout,
                    self._local_port,
                )
                raise RuntimeError(
                    f"UDP port {self._local_port} could not be bound "
                    f"(already in use?). Choose a different local port."
                ) from None

        # Log the actual bound address
        transport = getattr(server, "local_transport", None)
        if transport is not None:
            sock = transport.get_extra_info("socket")
            if sock is not None:
                bound = sock.getsockname()
                _LOGGER.info(
                    "UDP transport ready — actually bound to %s:%s", bound[0], bound[1]
                )
            else:
                _LOGGER.debug("UDP transport ready (socket details unavailable)")
        else:
            _LOGGER.warning(
                "UDP transport is None after awaiting tasks — "
                "network communication will likely fail"
            )

    async def disconnect(self) -> None:
        """Shut down the BACpypes3 application and release the UDP socket."""
        # Cancel all COV tasks
        for task in self._cov_tasks.values():
            task.cancel()
        self._cov_tasks.clear()

        if self._app is not None:
            try:
                # close() is synchronous in BACpypes3
                self._app.close()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Exception during app close (ignored)")
            self._app = None
            _LOGGER.info("BACnet client disconnected")

    # ------------------------------------------------------------------
    # Device discovery - Who-Is / I-Am
    # ------------------------------------------------------------------

    async def discover_devices(
        self,
        timeout: float = 5.0,
        target_device_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Send a Who-Is and collect I-Am responses.

        If *target_device_id* is provided, a targeted Who-Is is sent with
        low_limit == high_limit == target_device_id so the BACnet network
        only returns that specific device.  Otherwise a global broadcast
        is sent.

        Returns a list of dicts with keys: device_id, device_name, address.
        """
        if self._app is None:
            raise RuntimeError("Client not connected")

        devices: list[dict[str, Any]] = []
        seen_ids: set[int] = set()

        if target_device_id:
            _LOGGER.debug(
                "Sending targeted Who-Is for device %d (timeout=%.1fs)",
                target_device_id,
                timeout,
            )
        else:
            _LOGGER.debug("Sending global Who-Is broadcast (timeout=%.1fs)", timeout)

        try:
            # who_is() returns a Future that resolves to a list of I-Am APDUs
            who_is_kwargs: dict[str, Any] = {"timeout": timeout}
            if target_device_id:
                who_is_kwargs["low_limit"] = target_device_id
                who_is_kwargs["high_limit"] = target_device_id
            i_am_list = await self._app.who_is(**who_is_kwargs)

            for i_am in i_am_list:
                device_id = i_am.iAmDeviceIdentifier[1]
                if device_id in seen_ids:
                    continue
                seen_ids.add(device_id)

                # Try to read the device name
                device_name = f"Device {device_id}"
                try:
                    name = await self._app.read_property(
                        i_am.pduSource,
                        ObjectIdentifier(("device", device_id)),
                        "objectName",
                    )
                    if name:
                        device_name = str(name)
                except Exception:  # noqa: BLE001
                    pass  # use default name

                # Read vendor, model, firmware, software version
                extras = await self._read_device_extras(
                    Address(str(i_am.pduSource)), device_id
                )

                devices.append(
                    {
                        "device_id": device_id,
                        "device_name": device_name,
                        "address": str(i_am.pduSource),
                        **extras,
                    }
                )
                _LOGGER.debug(
                    "Discovered device: %s (%d) at %s",
                    device_name,
                    device_id,
                    _mask_address(i_am.pduSource),
                )
        except asyncio.TimeoutError:
            pass  # normal - discovery just timed out
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Error during Who-Is discovery: %s", exc)

        _LOGGER.info("Discovery complete: found %d device(s)", len(devices))
        return devices

    # ------------------------------------------------------------------
    # Manual device identification (unicast)
    # ------------------------------------------------------------------

    async def _read_device_extras(
        self, addr: Address, device_id: int
    ) -> dict[str, str]:
        """Read optional device identity properties (vendor, model, versions).

        Returns a dict with keys: vendor_name, model_name, firmware_version,
        software_version.  Values default to empty string if unreadable.
        """
        oid = ObjectIdentifier(("device", device_id))
        extras: dict[str, str] = {
            "vendor_name": "",
            "model_name": "",
            "firmware_version": "",
            "software_version": "",
        }
        prop_map = {
            "vendorName": "vendor_name",
            "modelName": "model_name",
            "firmwareRevision": "firmware_version",
            "applicationSoftwareVersion": "software_version",
        }
        for bacnet_prop, key in prop_map.items():
            val = await self._safe_read(addr, oid, bacnet_prop)
            if val is not None:
                extras[key] = str(val)
        _LOGGER.debug(
            "Device %d extras: vendor=%s, model=%s, fw=%s, sw=%s",
            device_id,
            extras["vendor_name"],
            extras["model_name"],
            extras["firmware_version"],
            extras["software_version"],
        )
        return extras

    async def read_device_info(
        self,
        device_address: str,
        device_id: int | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any] | None:
        """Read device identity from a known IP address (unicast).

        Sends a directed Who-Is to a specific address, or falls back to
        reading the Device object directly.  When *device_id* is provided
        the Who-Is uses low/high limits and the fallback reads that
        specific Device object instead of guessing common IDs.

        Returns a dict compatible with the discovery result format:
            {"device_id": int, "device_name": str, "address": str}

        Returns None if the device does not respond within *timeout* seconds.
        """
        try:
            return await asyncio.wait_for(
                self._read_device_info_inner(device_address, device_id),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "Timeout (%.0fs) reaching device at %s",
                timeout,
                _mask_address(device_address),
            )
            return None

    async def _read_device_info_inner(
        self, device_address: str, known_device_id: int | None = None
    ) -> dict[str, Any] | None:
        """Internal implementation of read_device_info (no outer timeout)."""
        if self._app is None:
            raise RuntimeError("Client not connected")

        addr = Address(device_address)
        _LOGGER.debug(
            "read_device_info: address=%s (parsed=%r), known_id=%s, app=%s",
            _mask_address(device_address),
            addr,
            known_device_id,
            type(self._app).__name__,
        )

        # Verify transport is live
        server = self._get_datagram_server()
        if server is not None:
            transport = getattr(server, "local_transport", None)
            if transport is None:
                _LOGGER.error(
                    "UDP transport is not ready — cannot send BACnet packets. "
                    "Port %d may already be in use on this host.",
                    self._local_port,
                )
                return None
            sock = transport.get_extra_info("socket")
            if sock is not None:
                _LOGGER.debug("UDP socket bound to %s", sock.getsockname())
        else:
            _LOGGER.warning("Cannot locate IPv4DatagramServer for transport check")

        # Strategy 1: directed Who-Is → I-Am
        try:
            who_is_kwargs: dict[str, Any] = {"address": addr, "timeout": 5}
            if known_device_id is not None:
                who_is_kwargs["low_limit"] = known_device_id
                who_is_kwargs["high_limit"] = known_device_id
            _LOGGER.debug("Strategy 1: Sending directed Who-Is %s", who_is_kwargs)
            i_am_list = await self._app.who_is(**who_is_kwargs)
            _LOGGER.debug(
                "Who-Is returned %d I-Am(s)", len(i_am_list) if i_am_list else 0
            )
            if i_am_list:
                i_am = i_am_list[0]
                device_id = i_am.iAmDeviceIdentifier[1]
                device_name = f"Device {device_id}"
                try:
                    name = await asyncio.wait_for(
                        self._app.read_property(
                            addr,
                            ObjectIdentifier(("device", device_id)),
                            "objectName",
                        ),
                        timeout=3,
                    )
                    if name and not isinstance(name, ErrorRejectAbortNack):
                        device_name = str(name)
                except (asyncio.TimeoutError, ErrorRejectAbortNack, Exception):  # noqa: BLE001
                    _LOGGER.debug("Could not read objectName, using default")
                extras = await self._read_device_extras(addr, device_id)
                return {
                    "device_id": device_id,
                    "device_name": device_name,
                    "address": device_address,
                    **extras,
                }
        except (ErrorRejectAbortNack, Exception) as exc:  # noqa: BLE001
            _LOGGER.debug(
                "Strategy 1 (Who-Is) failed for %s: %s (%s)",
                _mask_address(device_address),
                exc,
                type(exc).__name__,
            )

        # Strategy 2: read the Device object directly via ReadProperty (unicast)
        ids_to_try: list[int]
        if known_device_id is not None:
            ids_to_try = [known_device_id]
        else:
            ids_to_try = [1, 0, 2, 100, 1000]

        _LOGGER.debug("Strategy 2: Trying ReadProperty for device IDs %s", ids_to_try)
        for test_id in ids_to_try:
            try:
                oid = ObjectIdentifier(("device", test_id))
                _LOGGER.debug(
                    "  Trying ReadProperty %s objectIdentifier from %s ...",
                    oid,
                    _mask_address(device_address),
                )
                obj_id = await asyncio.wait_for(
                    self._app.read_property(addr, oid, "objectIdentifier"),
                    timeout=3,
                )
                _LOGGER.debug("  ReadProperty returned: %s", obj_id)
                if obj_id is not None and not isinstance(obj_id, ErrorRejectAbortNack):
                    device_id = obj_id[1]
                    device_name = f"Device {device_id}"
                    try:
                        name = await asyncio.wait_for(
                            self._app.read_property(addr, oid, "objectName"),
                            timeout=3,
                        )
                        if name and not isinstance(name, ErrorRejectAbortNack):
                            device_name = str(name)
                    except (asyncio.TimeoutError, ErrorRejectAbortNack, Exception):  # noqa: BLE001
                        _LOGGER.debug("  Could not read objectName")
                    extras = await self._read_device_extras(addr, device_id)
                    return {
                        "device_id": device_id,
                        "device_name": device_name,
                        "address": device_address,
                        **extras,
                    }
            except asyncio.TimeoutError:
                _LOGGER.debug("  ReadProperty timeout for device,%d", test_id)
                continue
            except ErrorRejectAbortNack as exc:
                _LOGGER.debug("  BACnet error for device,%d: %s", test_id, exc)
                continue
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug(
                    "  ReadProperty error for device,%d: %s (%s)",
                    test_id,
                    exc,
                    type(exc).__name__,
                )
                continue

        _LOGGER.warning(
            "Could not identify device at %s. "
            "Verify: (1) the device is on the same subnet or reachable via BBMD, "
            "(2) UDP port 47808 is not blocked by a firewall, "
            "(3) if Home Assistant runs in Docker, use --network=host, "
            "(4) try a different 'Local port' (e.g. 47809) in case port %d is "
            "already in use on this host.",
            _mask_address(device_address),
            self._local_port,
        )
        return None

    # ------------------------------------------------------------------
    # Object list and property reads
    # ------------------------------------------------------------------

    async def _read_object_list_property(
        self, addr: Address, device_oid: ObjectIdentifier, device_address: str
    ) -> list | None:
        """Read the objectList property, with automatic fallback.

        Strategy:
        1. Attempt a bulk read of the entire objectList property.
        2. If that fails (segmentation-not-supported, timeout, or other
           BACnet error), fall back to BACnet standard array indexing
           (Clause 12.19): read objectList[0] for the array length, then
           read each element individually via objectList[1]…objectList[N].

        Returns the list of object identifiers, or None on failure.
        """
        assert self._app is not None  # noqa: S101

        # --- Strategy 1: bulk read ------------------------------------------
        try:
            result = await asyncio.wait_for(
                self._app.read_property(addr, device_oid, "objectList"),
                timeout=15,
            )
            if isinstance(result, ErrorRejectAbortNack):
                raise result  # handled below
            if result is not None and hasattr(result, "__iter__"):
                _LOGGER.debug(
                    "Bulk objectList read succeeded for %s",
                    _mask_address(device_address),
                )
                return list(result)
        except asyncio.CancelledError:
            raise
        except (ErrorRejectAbortNack, asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
            _LOGGER.info(
                "Bulk objectList read failed for %s: %s (%s) — "
                "falling back to element-by-element array indexing",
                _mask_address(device_address),
                exc,
                type(exc).__name__,
            )

        # --- Strategy 2: array indexing (BACnet Clause 12.19) ----------------
        try:
            count_raw = await asyncio.wait_for(
                self._app.read_property(addr, device_oid, "objectList", array_index=0),
                timeout=10,
            )
            if isinstance(count_raw, ErrorRejectAbortNack):
                _LOGGER.error(
                    "Cannot read objectList[0] from %s: %s",
                    _mask_address(device_address),
                    count_raw,
                )
                return None
            count = int(count_raw)
            _LOGGER.debug(
                "objectList[0] = %d for %s",
                count,
                _mask_address(device_address),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error(
                "Failed to read objectList[0] from %s: %s (%s)",
                _mask_address(device_address),
                exc,
                type(exc).__name__,
            )
            return None

        object_list: list = []
        for idx in range(1, count + 1):
            try:
                oid = await asyncio.wait_for(
                    self._app.read_property(
                        addr, device_oid, "objectList", array_index=idx
                    ),
                    timeout=5,
                )
                if isinstance(oid, ErrorRejectAbortNack):
                    _LOGGER.warning(
                        "Error reading objectList[%d] from %s: %s",
                        idx,
                        _mask_address(device_address),
                        oid,
                    )
                    continue
                object_list.append(oid)
            except asyncio.CancelledError:
                _LOGGER.warning(
                    "objectList read cancelled at index %d for %s",
                    idx,
                    _mask_address(device_address),
                )
                break
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "Failed to read objectList[%d] from %s: %s (%s)",
                    idx,
                    _mask_address(device_address),
                    exc,
                    type(exc).__name__,
                )
                continue

        _LOGGER.info(
            "Array-indexed objectList read got %d/%d entries from %s",
            len(object_list),
            count,
            _mask_address(device_address),
        )
        return object_list

    async def read_object_list(
        self, device_address: str, device_id: int
    ) -> list[dict[str, Any]]:
        """Read the Object List from a device and fetch metadata for each supported object.

        For each object we read: objectName, description, presentValue, units,
        statusFlags, outOfService. We also detect if the object is commandable.

        Returns a list of object dicts ready for storage in the config entry.
        """
        if self._app is None:
            raise RuntimeError("Client not connected")

        addr = Address(device_address)
        device_oid = ObjectIdentifier(("device", device_id))
        objects: list[dict[str, Any]] = []

        # 1. Read the Object List property from the Device object.
        #    First attempt a bulk read; if that fails (e.g. segmentation-not-
        #    supported), fall back to BACnet standard array indexing
        #    (Clause 12.19): read objectList[0] for the count, then each
        #    element individually via objectList[1] … objectList[N].
        _LOGGER.debug(
            "Reading objectList from %s device,%d",
            _mask_address(device_address),
            device_id,
        )
        object_list = await self._read_object_list_property(
            addr, device_oid, device_address
        )

        if object_list is None:
            _LOGGER.warning("objectList is None for device %s", device_id)
            return objects

        try:
            list_len = len(object_list) if hasattr(object_list, "__len__") else -1
        except Exception:  # noqa: BLE001
            list_len = -1
        _LOGGER.debug(
            "objectList returned %d entries (type: %s)",
            list_len,
            type(object_list).__name__,
        )

        # 2. Iterate and read metadata for each supported object type
        for oid in object_list:
            try:
                obj_type_str, instance = oid
            except (TypeError, ValueError) as exc:
                _LOGGER.warning(
                    "Skipping unparseable objectList entry %r: %s", oid, exc
                )
                continue

            # Convert to plain int — ObjectType is an int subclass with
            # a custom __str__ that returns hyphenated names, which would
            # create inconsistent keys after JSON round-tripping.
            obj_type_int = (
                int(obj_type_str)
                if isinstance(obj_type_str, int)
                else self._object_type_str_to_int(obj_type_str)
            )
            if obj_type_int is None or obj_type_int not in SUPPORTED_OBJECT_TYPES:
                _LOGGER.debug(
                    "Skipping unsupported object type: %s (int=%s)", oid, obj_type_int
                )
                continue

            try:
                obj_info = await self._read_object_metadata(
                    addr, oid, obj_type_int, instance
                )
                if obj_info is not None:
                    objects.append(obj_info)
                    _LOGGER.debug(
                        "Read metadata for %s:%d — name=%s",
                        oid,
                        instance,
                        obj_info.get("object_name", "?"),
                    )
                else:
                    _LOGGER.warning(
                        "Metadata read returned None for %s:%d", oid, instance
                    )
            except asyncio.CancelledError:
                _LOGGER.warning("Metadata read cancelled for %s:%d", oid, instance)
                # Return whatever we have so far rather than losing everything
                break
            except (ErrorRejectAbortNack, Exception) as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "Unexpected error reading metadata for %s:%d — %s (%s)",
                    oid,
                    instance,
                    exc,
                    type(exc).__name__,
                )
                continue

        _LOGGER.info(
            "Read %d supported objects from device %s (%d)",
            len(objects),
            _mask_address(device_address),
            device_id,
        )
        return objects

    async def _read_object_metadata(
        self,
        addr: Address,
        oid: ObjectIdentifier,
        obj_type: int,
        instance: int,
    ) -> dict[str, Any] | None:
        """Read metadata properties for one BACnet object.

        Returns a dict suitable for storage in the config entry, or None on failure.
        """
        try:
            # Read commonly needed properties individually (safer than RPM for
            # devices that don't support ReadPropertyMultiple)
            object_name = (
                await self._safe_read(addr, oid, "objectName") or f"Object {instance}"
            )
            description = await self._safe_read(addr, oid, "description") or ""
            units = await self._safe_read(addr, oid, "units")
            present_value = await self._safe_read(addr, oid, "presentValue")

            _LOGGER.debug(
                "Raw values for %s:%d — name=%r, desc=%r, units=%r, pv=%r (type=%s)",
                oid,
                instance,
                object_name,
                description,
                units,
                present_value,
                type(present_value).__name__ if present_value is not None else "None",
            )

            # Determine if this object is commandable (has a Priority Array)
            commandable = obj_type in COMMANDABLE_TYPES
            if obj_type in POTENTIALLY_WRITABLE_TYPES:
                # Try to read priority array - if it exists the object is commandable
                pa = await self._safe_read(addr, oid, "priorityArray")
                if pa is not None:
                    commandable = True

            return {
                "object_type": int(obj_type),
                "instance": int(instance),
                "object_name": str(object_name),
                "description": str(description),
                "units": str(units) if units is not None else None,
                "present_value": self._coerce_value(present_value),
                "commandable": bool(commandable),
            }
        except asyncio.CancelledError:
            _LOGGER.warning("Metadata read cancelled for %s:%d", oid, instance)
            raise
        except (ErrorRejectAbortNack, Exception) as exc:  # noqa: BLE001
            _LOGGER.warning(
                "Failed to read metadata for %s:%d - %s (%s)",
                oid,
                instance,
                exc,
                type(exc).__name__,
                exc_info=True,
            )
            return None

    async def _safe_read(
        self, addr: Address, oid: ObjectIdentifier, prop_name: str
    ) -> Any | None:
        """Read a single property, returning None on any error or timeout.

        BACpypes3 v0.0.99 quirk: when a device responds with a BACnet Error
        (e.g. ``unknown-property``), the library raises
        ``ErrorRejectAbortNack`` which extends ``BaseException`` — NOT
        ``Exception``.  A bare ``except Exception`` will miss it, so we
        catch it explicitly.
        """
        try:
            result = await asyncio.wait_for(
                self._app.read_property(addr, oid, prop_name),
                timeout=5,
            )
            # read_property may also RETURN an error object instead of raising
            if isinstance(result, ErrorRejectAbortNack):
                return None
            return result
        except asyncio.TimeoutError:
            return None
        except asyncio.CancelledError:
            raise
        except ErrorRejectAbortNack:
            # BACnet error response (e.g. property does not exist on device)
            return None
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # Single property read (for coordinator polling)
    # ------------------------------------------------------------------

    async def read_property(
        self,
        device_address: str,
        object_type: int,
        instance: int,
        property_name: str = "presentValue",
    ) -> Any | None:
        """Read a single property from a BACnet object.

        Args:
            device_address: Target device IP address string.
            object_type: BACnet object type integer.
            instance: Object instance number.
            property_name: Property to read (default: presentValue).

        Returns:
            The property value, or None on error.
        """
        if self._app is None:
            raise RuntimeError("Client not connected")

        addr = Address(device_address)
        oid = ObjectIdentifier((self._int_to_object_type_str(object_type), instance))
        return await self._safe_read(addr, oid, property_name)

    async def poll_objects(
        self,
        device_address: str,
        objects: list[dict[str, Any]],
        property_names: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Read properties for a batch of objects in one network round-trip.

        Attempts ReadPropertyMultiple (RPM) first so all objects are fetched
        in a single request.  Falls back to per-object reads if the device
        rejects RPM (once rejected, individual reads are used for all future
        polls of that device).

        Returns dict keyed by "object_type:instance" → {property: value}.
        """
        if self._app is None:
            raise RuntimeError("Client not connected")

        if property_names is None:
            property_names = ["presentValue", "statusFlags"]

        if self._rpm_supported.get(device_address, True):
            result = await self._try_rpm_poll(device_address, objects, property_names)
            if result is not None:
                return result

        return await self._fallback_poll(device_address, objects, property_names)

    async def _try_rpm_poll(
        self,
        device_address: str,
        objects: list[dict[str, Any]],
        property_names: list[str],
    ) -> dict[str, dict[str, Any]] | None:
        """Attempt one ReadPropertyMultiple request for all objects.

        Returns the parsed result dict on success, or None if RPM failed so
        the caller can fall back to individual reads.

        Permanently marks the device as not supporting RPM when the device
        sends an ``ErrorRejectAbortNack`` (service-not-supported).  Transient
        failures (timeout, unexpected exceptions) return None without updating
        the cache so the next poll retries RPM.
        """
        rpm_props = [self._CAMEL_TO_HYPHEN.get(p, p) for p in property_names]

        # Build flat alternating list: [oid_str, [props], oid_str, [props], ...]
        param_list: list = []
        for obj in objects:
            type_str = self._INT_TO_TYPE_STR.get(obj["object_type"])
            if type_str is None:
                continue
            param_list.append(f"{type_str},{obj['instance']}")
            param_list.append(rpm_props)

        if not param_list:
            return {}

        addr = Address(device_address)
        try:
            results = await asyncio.wait_for(
                self._app.read_property_multiple(addr, param_list),
                timeout=30.0,
            )

            data: dict[str, dict[str, Any]] = {}
            for obj_id, prop_id, _arr_idx, value in results:
                obj_type_int = self._object_type_str_to_int(str(obj_id[0]))
                instance = int(obj_id[1])
                if obj_type_int is None:
                    continue
                obj_key = f"{obj_type_int}:{instance}"
                prop_camel = self._HYPHEN_TO_CAMEL.get(str(prop_id), str(prop_id))
                # Per-property errors arrive as exception-like objects in the tuple
                coerced = (
                    None
                    if isinstance(value, BaseException)
                    else self._coerce_value(value)
                )
                data.setdefault(obj_key, {})[prop_camel] = coerced

            _LOGGER.debug(
                "RPM poll: %d objects from %s", len(data), _mask_address(device_address)
            )
            return data

        except asyncio.TimeoutError:
            _LOGGER.debug("RPM poll timed out for %s", _mask_address(device_address))
            return None
        except ErrorRejectAbortNack as exc:
            _LOGGER.info(
                "Device %s rejected ReadPropertyMultiple (%s) — "
                "switching to individual reads for all future polls",
                _mask_address(device_address),
                exc,
            )
            self._rpm_supported[device_address] = False
            return None
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug(
                "RPM poll error for %s: %s", _mask_address(device_address), exc
            )
            return None

    async def _fallback_poll(
        self,
        device_address: str,
        objects: list[dict[str, Any]],
        property_names: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Read each object's properties individually (fallback when RPM is unavailable)."""
        data: dict[str, dict[str, Any]] = {}
        for obj in objects:
            obj_key = f"{obj['object_type']}:{obj['instance']}"
            obj_data: dict[str, Any] = {}
            for prop in property_names:
                value = await self.read_property(
                    device_address, obj["object_type"], obj["instance"], prop
                )
                obj_data[prop] = self._coerce_value(value)
            data[obj_key] = obj_data
        return data

    # ------------------------------------------------------------------
    # Property write with Priority Array support
    # ------------------------------------------------------------------

    async def write_property(
        self,
        device_address: str,
        object_type: int,
        instance: int,
        property_name: str,
        value: Any,
        priority: int = DEFAULT_WRITE_PRIORITY,
    ) -> bool:
        """Write a value to a BACnet property with proper Priority Array handling.

        BACnet Standard compliance:
        - For commandable objects, writes go through the Priority Array at the
          specified priority level (default 16 = lowest).
        - To relinquish a commanded value (release the override), write Null
          at the previously written priority level.
        - For non-commandable/writable objects, priority is not used.

        Args:
            device_address: Target device address.
            object_type: BACnet object type integer.
            instance: Object instance number.
            property_name: Property to write (usually "presentValue").
            value: The value to write. Use None to send Null (relinquish).
            priority: BACnet priority level (1-16). Only used for commandable objects.

        Returns:
            True on success, False on failure.
        """
        if self._app is None:
            raise RuntimeError("Client not connected")

        addr = Address(device_address)
        type_str = self._int_to_object_type_str(object_type)
        oid = ObjectIdentifier((type_str, instance))

        # Determine if this is a commandable object that needs priority
        is_commandable = object_type in (COMMANDABLE_TYPES | POTENTIALLY_WRITABLE_TYPES)

        # Convert None -> Null for relinquish
        if value is None:
            bacnet_value = Null()
        else:
            bacnet_value = self._python_to_bacnet_value(value, object_type)

        try:
            _LOGGER.debug(
                "Writing %s to %s:%d.%s (priority=%s, commandable=%s)",
                value,
                type_str,
                instance,
                property_name,
                priority if is_commandable else "N/A",
                is_commandable,
            )

            if is_commandable:
                result = await self._app.write_property(
                    addr, oid, property_name, bacnet_value, priority=priority
                )
            else:
                result = await self._app.write_property(
                    addr, oid, property_name, bacnet_value
                )

            # BACpypes3 returns ErrorRejectAbortNack on failure instead of
            # raising it.  We must check the return value explicitly.
            if isinstance(result, ErrorRejectAbortNack):
                _LOGGER.error(
                    "Write rejected by device for %s:%d.%s = %s: %s",
                    type_str,
                    instance,
                    property_name,
                    value,
                    result,
                )
                return False

            _LOGGER.debug("Write successful")
            return True

        except (ErrorRejectAbortNack, Exception) as exc:  # noqa: BLE001
            _LOGGER.error(
                "Write failed for %s:%d.%s = %s: %s",
                type_str,
                instance,
                property_name,
                value,
                exc,
            )
            return False

    async def relinquish(
        self,
        device_address: str,
        object_type: int,
        instance: int,
        priority: int = DEFAULT_WRITE_PRIORITY,
    ) -> bool:
        """Send a Null write (relinquish) to release a previously commanded value.

        This clears the specified priority level in the Priority Array, allowing
        lower-priority values (or the Relinquish Default) to take effect.
        """
        return await self.write_property(
            device_address=device_address,
            object_type=object_type,
            instance=instance,
            property_name="presentValue",
            value=None,  # Null = relinquish
            priority=priority,
        )

    # ------------------------------------------------------------------
    # COV (Change of Value) subscriptions
    # ------------------------------------------------------------------

    async def subscribe_cov(
        self,
        device_address: str,
        object_type: int,
        instance: int,
        callback: Callable[[str, dict[str, Any]], None],
        lifetime: int = 300,
    ) -> str | None:
        """Subscribe to Change of Value notifications for one object.

        BACpypes3 COV uses an async context manager (change_of_value)
        that keeps a queue of incoming property-value notifications.  We
        start a long-running task that reads from the queue and invokes
        callback for each notification.

        If the device does not support COV or the subscription fails,
        returns None so the caller can fall back to polling.

        Args:
            device_address: Target device address.
            object_type: Object type integer.
            instance: Object instance number.
            callback: callback(obj_key, {"presentValue": v, ...})
                      invoked on each COV notification.
            lifetime: COV subscription lifetime in seconds.  BACpypes3
                      automatically renews the subscription before it
                      expires when using the context manager.

        Returns:
            A subscription key string on success, or None on failure.
        """
        if self._app is None:
            raise RuntimeError("Client not connected")

        addr = IPv4Address(device_address)
        type_str = self._int_to_object_type_str(object_type)
        oid = ObjectIdentifier((type_str, instance))
        sub_key = f"{device_address}:{object_type}:{instance}"
        obj_key = f"{object_type}:{instance}"

        # Event set by _cov_reader_task once the subscription context manager
        # has entered (i.e. the device acknowledged the SubscribeCOV request).
        # Also set on failure so subscribe_cov() is never left waiting forever.
        ready_event: asyncio.Event = asyncio.Event()

        try:
            _LOGGER.debug(
                "Subscribing to COV for %s:%d at %s", type_str, instance, device_address
            )

            task = asyncio.create_task(
                self._cov_reader_task(
                    addr, oid, lifetime, sub_key, obj_key, callback, ready_event
                )
            )
            self._cov_tasks[sub_key] = task

            # Wait for the subscription to be confirmed (or to fail) rather
            # than sleeping a fixed 0.5 s.  BACpypes3 enters the async-with
            # block only after the SubscribeCOV is acknowledged by the device,
            # so ready_event being set means the subscription is truly active.
            try:
                await asyncio.wait_for(ready_event.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "COV subscription timed out for %s:%d at %s. Falling back to polling.",
                    type_str,
                    instance,
                    device_address,
                )
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                self._cov_tasks.pop(sub_key, None)
                return None

            if task.done():
                # Task exited before or immediately after setting the event — failure
                _LOGGER.warning(
                    "COV subscription rejected for %s:%d at %s. Falling back to polling.",
                    type_str,
                    instance,
                    device_address,
                )
                self._cov_tasks.pop(sub_key, None)
                return None

            _LOGGER.info("COV subscription active for %s:%d", type_str, instance)
            return sub_key

        except (ErrorRejectAbortNack, Exception) as exc:  # noqa: BLE001
            _LOGGER.warning(
                "COV subscription failed for %s:%d at %s: %s. Falling back to polling.",
                type_str,
                instance,
                device_address,
                exc,
            )
            self._cov_tasks.pop(sub_key, None)
            return None

    async def _cov_reader_task(
        self,
        addr: IPv4Address,
        oid: ObjectIdentifier,
        lifetime: int,
        sub_key: str,
        obj_key: str,
        callback: Callable[[str, dict[str, Any]], None],
        ready_event: asyncio.Event,
    ) -> None:
        """Long-running task that reads from a COV subscription queue.

        Uses the BACpypes3 change_of_value() async context manager.
        The context manager handles subscription, renewal, and
        unsubscription automatically.

        Sets *ready_event* as soon as the subscription context is entered
        (device acknowledged) so subscribe_cov() can stop waiting.
        Also sets it on failure so the caller is never left blocked.

        Batches all property changes queued from a single notification
        into one callback call by yielding briefly after the first value
        and draining any immediately-available follow-up properties.
        """
        try:
            scm = self._app.change_of_value(addr, oid, lifetime=lifetime)
            async with scm:
                ready_event.set()  # Subscription confirmed — unblock subscribe_cov()
                while True:
                    # Wait for the first property change from this notification
                    prop_id, value = await scm.get_value()
                    changes: dict[str, Any] = {str(prop_id): self._coerce_value(value)}
                    # Yield so the event loop can deliver any other properties
                    # queued from the same ConfirmedCOVNotification, then drain
                    # them — this produces one callback call per notification
                    # instead of one per property.
                    await asyncio.sleep(0)
                    try:
                        while True:
                            extra_id, extra_val = await asyncio.wait_for(
                                scm.get_value(), timeout=0.05
                            )
                            changes[str(extra_id)] = self._coerce_value(extra_val)
                    except asyncio.TimeoutError:
                        pass
                    _LOGGER.debug("COV notification %s: %s", sub_key, changes)
                    try:
                        callback(obj_key, changes)
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception("Error in COV callback for %s", sub_key)
        except asyncio.CancelledError:
            _LOGGER.debug("COV task cancelled for %s", sub_key)
        except (ErrorRejectAbortNack, Exception):  # noqa: BLE001
            if not ready_event.is_set():
                ready_event.set()  # Unblock subscribe_cov() on failure
            _LOGGER.warning("COV task ended for %s", sub_key, exc_info=True)

    async def unsubscribe_cov(self, sub_key: str) -> None:
        """Cancel a COV subscription by cancelling its reader task.

        The async-with context manager will send the unsubscribe
        request when the task is cancelled.
        """
        task = self._cov_tasks.pop(sub_key, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            _LOGGER.debug("COV unsubscribed: %s", sub_key)

    async def unsubscribe_all_cov(self) -> None:
        """Cancel all COV subscriptions."""
        for sub_key in list(self._cov_tasks):
            await self.unsubscribe_cov(sub_key)

    # ------------------------------------------------------------------
    # Value conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_value(value: Any) -> Any:
        """Convert a BACpypes3 value to a plain Python type for JSON storage.

        Order matters: check specific BACpypes3 types before falling back to
        Python builtins, because some BACpypes3 types subclass list or int.
        """
        if value is None:
            return None
        if isinstance(value, Real):
            return float(value)
        if isinstance(value, Unsigned):
            return int(value)
        if isinstance(value, CharacterString):
            return str(value)
        # BitString subclasses (e.g. StatusFlags) are list subclasses.
        # str(StatusFlags([False,False,False,False])) returns '' — useless.
        # Convert to a plain list of booleans so callers get [False,False,False,False].
        if isinstance(value, list):
            return [bool(x) for x in value]
        if isinstance(value, (int, float, str, bool)):
            return value
        # Generic fallback
        return str(value)

    @staticmethod
    def _python_to_bacnet_value(value: Any, object_type: int) -> Any:
        """Convert a Python value to the appropriate BACpypes3 type.

        The correct BACnet type depends on the object type:
        - Analog types -> Real (float)
        - Binary types -> Unsigned or enumeration
        - Multi-state types -> Unsigned
        """
        if value is None:
            return Null()

        if object_type in {
            OBJECT_TYPE_ANALOG_INPUT,
            OBJECT_TYPE_ANALOG_OUTPUT,
            OBJECT_TYPE_ANALOG_VALUE,
        }:
            return Real(float(value))

        if object_type in {
            OBJECT_TYPE_BINARY_INPUT,
            OBJECT_TYPE_BINARY_OUTPUT,
            OBJECT_TYPE_BINARY_VALUE,
        }:
            # BACnet binary PV is an Enumerated type: 0=inactive, 1=active
            # Using Enumerated (not Unsigned) per ASHRAE 135 - strict devices
            # will reject Unsigned for BinaryPV properties.
            return Enumerated(int(bool(value)))

        if object_type in {
            OBJECT_TYPE_MULTI_STATE_INPUT,
            OBJECT_TYPE_MULTI_STATE_OUTPUT,
            OBJECT_TYPE_MULTI_STATE_VALUE,
        }:
            return Unsigned(int(value))

        # Generic fallback
        return Real(float(value))

    # ------------------------------------------------------------------
    # Object type string - integer mapping
    # ------------------------------------------------------------------

    _TYPE_STR_TO_INT: dict[str, int] = {
        # camelCase (BACpypes3 internal)
        "analogInput": OBJECT_TYPE_ANALOG_INPUT,
        "analogOutput": OBJECT_TYPE_ANALOG_OUTPUT,
        "analogValue": OBJECT_TYPE_ANALOG_VALUE,
        "binaryInput": OBJECT_TYPE_BINARY_INPUT,
        "binaryOutput": OBJECT_TYPE_BINARY_OUTPUT,
        "binaryValue": OBJECT_TYPE_BINARY_VALUE,
        "multiStateInput": OBJECT_TYPE_MULTI_STATE_INPUT,
        "multiStateOutput": OBJECT_TYPE_MULTI_STATE_OUTPUT,
        "multiStateValue": OBJECT_TYPE_MULTI_STATE_VALUE,
        # hyphenated (ASHRAE 135 / BACpypes3 str() output)
        "analog-input": OBJECT_TYPE_ANALOG_INPUT,
        "analog-output": OBJECT_TYPE_ANALOG_OUTPUT,
        "analog-value": OBJECT_TYPE_ANALOG_VALUE,
        "binary-input": OBJECT_TYPE_BINARY_INPUT,
        "binary-output": OBJECT_TYPE_BINARY_OUTPUT,
        "binary-value": OBJECT_TYPE_BINARY_VALUE,
        "multi-state-input": OBJECT_TYPE_MULTI_STATE_INPUT,
        "multi-state-output": OBJECT_TYPE_MULTI_STATE_OUTPUT,
        "multi-state-value": OBJECT_TYPE_MULTI_STATE_VALUE,
    }

    # Property name conversions for ReadPropertyMultiple (RPM uses hyphenated names)
    _CAMEL_TO_HYPHEN: dict[str, str] = {
        "presentValue": "present-value",
        "statusFlags": "status-flags",
        "outOfService": "out-of-service",
        "priorityArray": "priority-array",
        "covIncrement": "cov-increment",
    }
    _HYPHEN_TO_CAMEL: dict[str, str] = {v: k for k, v in _CAMEL_TO_HYPHEN.items()}

    # Use hyphenated names for BACpypes3 ObjectIdentifier construction
    # (matching ASHRAE 135 and BACpypes3's native convention)
    _INT_TO_TYPE_STR: dict[int, str] = {
        OBJECT_TYPE_ANALOG_INPUT: "analog-input",
        OBJECT_TYPE_ANALOG_OUTPUT: "analog-output",
        OBJECT_TYPE_ANALOG_VALUE: "analog-value",
        OBJECT_TYPE_BINARY_INPUT: "binary-input",
        OBJECT_TYPE_BINARY_OUTPUT: "binary-output",
        OBJECT_TYPE_BINARY_VALUE: "binary-value",
        OBJECT_TYPE_MULTI_STATE_INPUT: "multi-state-input",
        OBJECT_TYPE_MULTI_STATE_OUTPUT: "multi-state-output",
        OBJECT_TYPE_MULTI_STATE_VALUE: "multi-state-value",
    }

    @classmethod
    def _object_type_str_to_int(cls, type_str: str | int) -> int | None:
        """Convert BACpypes3 object type string to integer ID.

        BACpypes3 ObjectType is an int subclass with a custom __str__
        that returns hyphenated names (e.g. 'analog-input').  We always
        return a plain int to avoid surprises after JSON round-tripping.

        Accepts both camelCase ('analogInput') and hyphenated ('analog-input')
        formats, case-insensitively.
        """
        if isinstance(type_str, int):
            return int(type_str)  # strip ObjectType wrapper → plain int
        s = str(type_str)
        # Direct lookup (handles both camelCase and hyphenated)
        result = cls._TYPE_STR_TO_INT.get(s)
        if result is not None:
            return result
        # Case-insensitive fallback
        s_lower = s.lower()
        for key, val in cls._TYPE_STR_TO_INT.items():
            if key.lower() == s_lower:
                return val
        return None

    @classmethod
    def _int_to_object_type_str(cls, type_int: int) -> str:
        """Convert integer object type to BACpypes3 type string."""
        return cls._INT_TO_TYPE_STR.get(type_int, f"type-{type_int}")
