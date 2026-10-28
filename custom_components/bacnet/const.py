"""Constants for the BACnet IP integration."""

from datetime import timedelta

# ---------------------------------------------------------------------------
# Integration identifiers
# ---------------------------------------------------------------------------
DOMAIN = "bacnet"
MANUFACTURER = "BACnet"

# ---------------------------------------------------------------------------
# Configuration keys  (config_flow / options_flow)
# ---------------------------------------------------------------------------
# Network
CONF_LOCAL_IP = "local_ip"
CONF_LOCAL_PORT = "local_port"
CONF_BBMD_ADDRESS = "bbmd_address"
CONF_BBMD_TTL = "bbmd_ttl"
CONF_USE_BBMD = "use_bbmd"

# Discovery / device
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_NAME = "device_name"
CONF_DEVICE_ADDRESS = "device_address"
CONF_TARGET_ADDRESS = "target_address"
CONF_TARGET_DEVICE_ID = "target_device_id"
CONF_VENDOR_NAME = "vendor_name"
CONF_MODEL_NAME = "model_name"
CONF_FIRMWARE_VERSION = "firmware_version"
CONF_SOFTWARE_VERSION = "software_version"

# Object selection
CONF_SELECTED_OBJECTS = "selected_objects"
CONF_SELECT_ALL = "select_all"
CONF_SELECT_TYPES = "select_types"  # setup/rescan: import every object of these types
CONF_ADD_OBJECTS = "add_objects"  # rescan: objects to import as well
CONF_REMOVE_OBJECTS = "remove_objects"  # rescan: imported objects to drop
CONF_RESCAN_OBJECTS = "rescan_objects"  # options-flow only, never persisted

# Naming
CONF_USE_DESCRIPTION = "use_description"

# COV & Polling
CONF_ENABLE_COV = "enable_cov"
CONF_POLLING_INTERVAL = "polling_interval"
CONF_COV_INCREMENT = "cov_increment"

# Domain mapping
CONF_DOMAIN_MAPPING = "domain_mapping"

# Override keys the user chose on purpose (options flow or the repair's
# "keep"), so the stale-override repair never flags them (issue #97).
CONF_KEPT_DOMAIN_OVERRIDES = "kept_domain_overrides"

# Options-flow only, never persisted: which objects to customise.
CONF_CUSTOMIZE_OBJECTS = "customize_objects"

# Climate objects whose current temperature comes from another selected
# object (climate obj_key -> temperature obj_key).
CONF_CLIMATE_TEMPERATURE_SOURCES = "climate_temperature_sources"

# Extra BACnet properties shown as their own entities
# (obj_key -> [camelCase property, ...]), picked per object in the options.
CONF_EXTRA_PROPERTIES = "extra_properties"

# Per-object COV override (obj_key -> bool). Objects with no entry fall back
# to the device-wide CONF_ENABLE_COV value.
CONF_COV_OVERRIDES = "cov_overrides"

# Per-object state class of analog sensors (obj_key -> "measurement",
# "total", "total_increasing" or STATE_CLASS_NONE). Objects with no entry
# get measurement, or total_increasing for meters (issue #137).
CONF_STATE_CLASSES = "state_classes"
STATE_CLASS_NONE = "none"  # no state class: no long-term statistics
STATE_CLASS_AUTO = "auto"  # options-flow only: the default, never stored
STATE_CLASS_CHOICES = [
    STATE_CLASS_AUTO,
    "measurement",
    "total_increasing",
    "total",
    STATE_CLASS_NONE,
]

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_PORT = 47808  # 0xBAC0 — standard BACnet/IP port
DEFAULT_BBMD_TTL = 900  # seconds (15 min)
DEFAULT_POLLING_INTERVAL = 30  # seconds
# Polling always runs (COV only adds faster updates in between), so a very
# short interval loads every device and the network for little gain.
MIN_POLLING_INTERVAL = 10  # seconds
DEFAULT_ENABLE_COV = True
DEFAULT_USE_DESCRIPTION = False
DEFAULT_COV_INCREMENT = 0.0  # 0 = keep the device's own covIncrement

# Resilience thresholds for outage recovery (issue #18).
# After MAX_SILENT_FAILURES consecutive failed polls, raise UpdateFailed so HA
# surfaces the outage and applies its native backoff.  After RECONNECT_THRESHOLD
# consecutive failures, the underlying BACnetClient is reconnected (re-binds the
# UDP socket and re-registers with the BBMD, which is needed when the foreign
# device registration TTL has expired during a network outage).
MAX_SILENT_FAILURES = 3
RECONNECT_THRESHOLD = 10

# Network load limits per device.
# RPM_MAX_OBJECTS caps one ReadPropertyMultiple request; larger requests can
# exceed a controller's max APDU / segmentation support. On a size-related
# abort the per-device chunk size is halved automatically.
# MAX_CONCURRENT_REQUESTS caps parallel individual requests (fallback reads,
# COV subscribes, metadata reads) — small controllers only handle a few
# outstanding confirmed requests at a time.
RPM_MAX_OBJECTS = 25
MAX_CONCURRENT_REQUESTS = 4

# Static object metadata (objectName, description, units, commandable) has no
# COV/push mechanism in BACnet — a device-side edit is only visible on a fresh
# ReadProperty. Two refresh paths pick that up (issue #26):
#   1. COV-triggered: a COV notification proves the device is actively talking
#      about that object right now, so it's the cheapest moment to also check
#      its static properties. Throttled per-object so a fast-changing analog
#      doesn't cause a metadata re-read on every notification.
#   2. Periodic sweep: the safety net for polling-only objects, which never
#      produce a COV notification to trigger off of.
# COV-triggered metadata changes arriving within this window are persisted
# together — one config-entry reload instead of one per object.
METADATA_PERSIST_DELAY = 5  # seconds
COV_METADATA_CHECK_INTERVAL = 300  # seconds (5 min) — per-object COV throttle
DEFAULT_METADATA_REFRESH_INTERVAL = 3600  # seconds (1 hour) — polled-object sweep

# Properties eligible for a LIVE SubscribeCOVProperty subscription (ASHRAE
# 135-2012 Addendum ar) instead of the ReadProperty-based refresh above.
# Opt-in per property because each one is a SEPARATE COV subscription on the
# device — SubscribeCOVProperty has no "any property" wildcard
# (monitoredPropertyIdentifier is singular) — and devices often cap total COV
# subscriptions in the 10s-100s. Default is empty so upgrading users don't
# silently multiply their subscription count (issue #26).
CONF_LIVE_METADATA_PROPERTIES = "live_metadata_properties"
LIVE_METADATA_PROPERTY_CHOICES: list[str] = ["object_name", "description", "units"]
DEFAULT_LIVE_METADATA_PROPERTIES: list[str] = []

# snake_case (our config key) -> BACnet camelCase property name
LIVE_METADATA_PROPERTY_TO_BACNET: dict[str, str] = {
    "object_name": "objectName",
    "description": "description",
    "units": "units",
}
LIVE_METADATA_BACNET_TO_PROPERTY: dict[str, str] = {
    v: k for k, v in LIVE_METADATA_PROPERTY_TO_BACNET.items()
}

# ---------------------------------------------------------------------------
# BACnet object type IDs  (from ASHRAE 135)
# ---------------------------------------------------------------------------
OBJECT_TYPE_ANALOG_INPUT = 0
OBJECT_TYPE_ANALOG_OUTPUT = 1
OBJECT_TYPE_ANALOG_VALUE = 2
OBJECT_TYPE_BINARY_INPUT = 3
OBJECT_TYPE_BINARY_OUTPUT = 4
OBJECT_TYPE_BINARY_VALUE = 5
OBJECT_TYPE_MULTI_STATE_INPUT = 13
OBJECT_TYPE_MULTI_STATE_OUTPUT = 14
OBJECT_TYPE_MULTI_STATE_VALUE = 19
OBJECT_TYPE_SCHEDULE = 17
OBJECT_TYPE_TREND_LOG = 20

# Human-readable labels for BACnet object types
OBJECT_TYPE_NAMES: dict[int, str] = {
    OBJECT_TYPE_ANALOG_INPUT: "Analog Input",
    OBJECT_TYPE_ANALOG_OUTPUT: "Analog Output",
    OBJECT_TYPE_ANALOG_VALUE: "Analog Value",
    OBJECT_TYPE_BINARY_INPUT: "Binary Input",
    OBJECT_TYPE_BINARY_OUTPUT: "Binary Output",
    OBJECT_TYPE_BINARY_VALUE: "Binary Value",
    OBJECT_TYPE_MULTI_STATE_INPUT: "Multi-State Input",
    OBJECT_TYPE_MULTI_STATE_OUTPUT: "Multi-State Output",
    OBJECT_TYPE_MULTI_STATE_VALUE: "Multi-State Value",
    OBJECT_TYPE_SCHEDULE: "Schedule",
    OBJECT_TYPE_TREND_LOG: "Trend Log",
}
# Common BACnet abbreviations, in the labels so the object picker finds them
OBJECT_TYPE_ABBREVIATIONS: dict[int, str] = {
    OBJECT_TYPE_ANALOG_INPUT: "AI",
    OBJECT_TYPE_ANALOG_OUTPUT: "AO",
    OBJECT_TYPE_ANALOG_VALUE: "AV",
    OBJECT_TYPE_BINARY_INPUT: "BI",
    OBJECT_TYPE_BINARY_OUTPUT: "BO",
    OBJECT_TYPE_BINARY_VALUE: "BV",
    OBJECT_TYPE_MULTI_STATE_INPUT: "MSI",
    OBJECT_TYPE_MULTI_STATE_OUTPUT: "MSO",
    OBJECT_TYPE_MULTI_STATE_VALUE: "MSV",
    OBJECT_TYPE_SCHEDULE: "SCH",
    OBJECT_TYPE_TREND_LOG: "TL",
}

# ---------------------------------------------------------------------------
# Supported HA domains  (for dynamic domain mapping)
# ---------------------------------------------------------------------------
SUPPORTED_DOMAINS: list[str] = [
    "sensor",
    "binary_sensor",
    "switch",
    "number",
    "climate",
    "select",  # multi-state objects: pick a state by its stateText
]

# BACnet engineering units (bacpypes3 hyphenated names) → (HA unit, sensor
# device class). A device class is only set where HA accepts that unit for
# it; "parts-per-million" stays generic (CO2, VOC, … all use ppm).
BACNET_UNITS: dict[str, tuple[str, str | None]] = {
    "degrees-celsius": ("°C", "temperature"),
    "degrees-fahrenheit": ("°F", "temperature"),
    "degrees-kelvin": ("K", "temperature"),
    "percent": ("%", None),
    "percent-relative-humidity": ("%", "humidity"),
    "pascals": ("Pa", "pressure"),
    "hectopascals": ("hPa", "pressure"),
    "kilopascals": ("kPa", "pressure"),
    "bars": ("bar", "pressure"),
    "pounds-force-per-square-inch": ("psi", "pressure"),
    "watts": ("W", "power"),
    "kilowatts": ("kW", "power"),
    "megawatts": ("MW", "power"),
    "volt-amperes": ("VA", "apparent_power"),
    "watt-hours": ("Wh", "energy"),
    "kilowatt-hours": ("kWh", "energy"),
    "megawatt-hours": ("MWh", "energy"),
    "amperes": ("A", "current"),
    "milliamperes": ("mA", "current"),
    "volts": ("V", "voltage"),
    "millivolts": ("mV", "voltage"),
    "kilovolts": ("kV", "voltage"),
    "hertz": ("Hz", "frequency"),
    "liters-per-second": ("L/s", "volume_flow_rate"),
    "liters-per-minute": ("L/min", "volume_flow_rate"),
    "cubic-meters-per-hour": ("m³/h", "volume_flow_rate"),
    "liters": ("L", "volume"),
    "cubic-meters": ("m³", "volume"),
    "meters-per-second": ("m/s", "speed"),
    "luxes": ("lx", "illuminance"),
    "parts-per-million": ("ppm", None),
    "kilograms": ("kg", "weight"),
    "seconds": ("s", "duration"),
    "minutes": ("min", "duration"),
    "hours": ("h", "duration"),
}

# Default mapping: BACnet object type → HA domain
DEFAULT_DOMAIN_MAP: dict[int, str] = {
    OBJECT_TYPE_ANALOG_INPUT: "sensor",
    OBJECT_TYPE_ANALOG_OUTPUT: "number",
    OBJECT_TYPE_ANALOG_VALUE: "sensor",
    OBJECT_TYPE_BINARY_INPUT: "binary_sensor",
    OBJECT_TYPE_BINARY_OUTPUT: "switch",
    OBJECT_TYPE_BINARY_VALUE: "switch",
    OBJECT_TYPE_MULTI_STATE_INPUT: "sensor",
    OBJECT_TYPE_MULTI_STATE_OUTPUT: "number",
    OBJECT_TYPE_MULTI_STATE_VALUE: "sensor",
    # Read-only: shows the value the schedule currently commands. The
    # schedule itself goes through bacnet.read_schedule / write_schedule.
    OBJECT_TYPE_SCHEDULE: "sensor",
    # Read-only: the number of records in the log. The records themselves
    # go through bacnet.read_trend_log.
    OBJECT_TYPE_TREND_LOG: "sensor",
}

# ---------------------------------------------------------------------------
# BACnet property IDs  (commonly used subset)
# ---------------------------------------------------------------------------
PROP_OBJECT_NAME = 77
PROP_DESCRIPTION = 28
PROP_PRESENT_VALUE = 85
PROP_STATUS_FLAGS = 111
PROP_UNITS = 117
PROP_OBJECT_TYPE = 79
PROP_OBJECT_IDENTIFIER = 75
PROP_OBJECT_LIST = 76
PROP_PRIORITY_ARRAY = 87
PROP_RELINQUISH_DEFAULT = 104
PROP_OUT_OF_SERVICE = 81
PROP_POLARITY = 84

# ---------------------------------------------------------------------------
# Priority levels for BACnet writes (1-16, lower = higher priority)
# ---------------------------------------------------------------------------
DEFAULT_WRITE_PRIORITY = 16  # Lowest priority — safe default
CONF_WRITE_PRIORITY = "write_priority"
WRITE_PRIORITY_OPTIONS: list[int] = [8, 9, 12, 13, 14, 15, 16]  # BACnet: 1-16

# ---------------------------------------------------------------------------
# Usage report (telemetry.py)
# ---------------------------------------------------------------------------
TELEMETRY_SCHEMA = 1  # payload contract version, shared with the server
CONF_SHARE_USAGE = "share_usage"  # entry option; ticked by default in setup
ISSUE_SHARE_USAGE = "share_usage"  # one-time question for existing installations
TELEMETRY_URL = "https://ha-bacnet.telemetry.brdc.nl/v1/report"
PRIVACY_URL = "https://github.com/CervezaStallone/Home-Assistant-BACnet-integration/blob/master/PRIVACY.md"
TELEMETRY_TIMEOUT = 10  # seconds; fire-and-forget, no retries
TELEMETRY_FIRST_DELAY = 600  # seconds after start; unpolled devices are left out
TELEMETRY_INTERVAL = timedelta(hours=24)

# ---------------------------------------------------------------------------
# Extra properties offered per object in the options flow
# ---------------------------------------------------------------------------
_ANALOG = frozenset({0, 1, 2})
_BINARY = frozenset({3, 4, 5})
_MULTI_STATE = frozenset({13, 14, 19})
_PRESENT_VALUE_TYPES = _ANALOG | _BINARY | _MULTI_STATE

# property -> (HA platform, object types it exists on, unit). The unit is
# "object" for the object's own units, an HA unit, or None. Any other
# property the user types in becomes a read-only sensor.
EXTRA_PROPERTY_SPECS: dict[str, tuple[str, frozenset[int], str | None]] = {
    "minimumOnTime": ("number", frozenset({4, 5}), "s"),
    "minimumOffTime": ("number", frozenset({4, 5}), "s"),
    "timeDelay": ("number", _PRESENT_VALUE_TYPES, "s"),
    "highLimit": ("number", _ANALOG, "object"),
    "lowLimit": ("number", _ANALOG, "object"),
    "deadband": ("number", _ANALOG, "object"),
    "covIncrement": ("number", _ANALOG, "object"),
    "relinquishDefault": ("number", frozenset({1, 2}), "object"),
    "outOfService": ("switch", _PRESENT_VALUE_TYPES | {17}, None),
    "eventState": ("sensor", _PRESENT_VALUE_TYPES, None),
    "reliability": ("sensor", _PRESENT_VALUE_TYPES | {17}, None),
    "changeOfStateCount": ("sensor", _BINARY | _MULTI_STATE, None),
    "elapsedActiveTime": ("sensor", _BINARY, "s"),
}
