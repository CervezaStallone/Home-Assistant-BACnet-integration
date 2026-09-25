<p align="center">
  <img src="img/logo.png" alt="BACnet IP Integration" width="256">

<h1 align="center">BACnet IP Integration for Home Assistant</h1>

<p align="center">
  Connect your building automation system to Home Assistant — no YAML, no hassle.<br>
  Monitor sensors, control outputs, and automate your BACnet/IP devices through a simple GUI.
</p>

<p align="center">
  <strong>HACS Compatible</strong> · GUI-only setup · Real-time COV updates · ASHRAE 135 compliant
</p>

---

<a href="https://buymeacoffee.com/Cervezastalone" target="_blank" title="buymeacoffee">
  <img src="https://iili.io/JoQ1MeS.md.png"  alt="buymeacoffee-yellow-badge" style="width: 208px;">
</a>

<br>
<br>

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=CervezaStallone&repository=Home-Assistant-BACnet-integration&category=integration)
</p>

## What does this integration do?

This custom integration lets Home Assistant talk to **BACnet/IP** devices — the protocol used in professional building automation for HVAC, lighting, access control, and energy management.

In plain terms: if your building has BACnet controllers, thermostats, sensors, or actuators, this integration brings them into Home Assistant so you can monitor and control them from your dashboard, automations, and scripts.

**No YAML configuration required.** Everything is set up through the Home Assistant GUI.

---

## Features at a glance

- **Three-step setup wizard** — network → discover devices → select objects
- **Automatic device discovery** — finds BACnet devices on your network via Who-Is / I-Am
- **Cross-subnet support** — reach devices on other VLANs through BBMD / Foreign Device Registration
- **Real-time updates via COV** — Change of Value subscriptions for instant state changes, with automatic polling fallback
- **Configurable COV increment** — control how sensitive COV notifications are for analog objects
- **Read and write** — monitor sensors and control outputs with proper BACnet Priority Array handling
- **Device identity** — automatically reads vendor name, model, and firmware version from the device
- **Custom device naming** — choose your own device name during setup
- **Flexible entity naming** — use BACnet `objectName` or `description` for display names
- **Per-object customization** — override the entity type or COV setting of individual objects (e.g. make a sensor into a number)
- **Selective import** — only import the objects you actually need
- **Configurable write priority** — per-device select entity to change the BACnet write priority (disabled by default)
- **Relinquish & one-off writes** — `bacnet.relinquish` and `bacnet.write_value` services for full Priority Array control
- **Device limits & state texts** — number limits from `minPresValue`/`maxPresValue`/`resolution`, multi-state objects as a select with their `stateText` labels
- **Climate with a real room temperature** — pair a setpoint with a separate temperature object
- **Automatic outage recovery** — detects network outages, reconnects automatically and tells you in Repairs when a device stays unreachable
- **Reconfigure** — change IP, port, BBMD or the device address without re-adding the integration
- **Diagnostics** — a diagnostics download and health sensors for troubleshooting
- **Efficient on the network** — batched ReadPropertyMultiple reads that adapt to what the device can handle
- **English and Dutch** user interface

---

## Supported object types

| BACnet Object | Default entity | Writable? |
|---|---|---|
| Analog Input | Sensor | No |
| Analog Output | Number | Yes |
| Analog Value | Sensor | Auto-detected |
| Binary Input | Binary sensor | No |
| Binary Output | Switch | Yes |
| Binary Value | Switch | Auto-detected |
| Multi-State Input | Sensor | No |
| Multi-State Output | Number | Yes |
| Multi-State Value | Sensor | Auto-detected |

> Value objects may or may not support writes — the integration auto-detects this by checking for a Priority Array during discovery.
>
> Any object type can be changed to another entity type per object (see [Customize objects](#customize-objects)). Multi-state objects can also become a **select** that shows their `stateText` labels (e.g. *Off / Heat / Cool*).

---

## Installation

### Via HACS (recommended)

1. Click the **HACS badge** at the top of this page, or:
   - Open **HACS** → **Integrations** → **⋮** → **Custom repositories**
   - Paste this repository URL and select category **Integration**
2. Click **Download**
3. **Restart** Home Assistant

### Manual installation

Copy the `custom_components/bacnet` folder into your Home Assistant `config/custom_components/` directory and restart.

---

## Getting started

### 1. Add the integration

Go to **Settings → Devices & Services → Add Integration** and search for **BACnet IP**.

### 2. Configure your network

| Setting | What it does | Default |
|---|---|---|
| **Local IP address** | Which network interface to use (leave empty for auto-detect) | Auto |
| **Local port** | BACnet/IP UDP port | `47808` |
| **Target device address** | Direct IP of a specific device (leave empty to discover all) | — |
| **Device ID** | BACnet Device Object Instance number (only needed with target address) | Auto |
| **Enable BBMD** | Turn on if devices are on a different subnet/VLAN | Off |
| **BBMD address** | IP:port of the BBMD router | — |
| **BBMD TTL** | Foreign device registration lifetime in seconds | `900` |

### 3. Select your device

The integration discovers BACnet devices on the network and shows them in a dropdown. Pick the one you want to add.

### 4. Choose objects and name your device

- Enter a **device name** (pre-filled with the BACnet device name — you can change it)
- Use **Select All** or pick individual objects to import
- Click **Submit** — your entities are created and data starts flowing immediately

---

## Configuration options

After setup, click **Configure** on the integration card to adjust:

| Option | What it does | Default |
|---|---|---|
| **Enable COV** | Use Change of Value subscriptions for real-time updates | On |
| **COV increment** | Minimum value change before a COV notification is sent (analog objects only). Set to `0` to use the device default. | `0.1` |
| **Polling interval** | How often all objects are polled, in seconds (minimum 10). Polling always runs — COV only adds faster updates in between. | `30` |
| **Use description** | Show BACnet `description` (property 28) instead of `objectName` as entity name | Off |
| **Live metadata properties** | Opt-in per property (`object_name`, `description`, `units`) for a *live* push update via BACnet's SubscribeCOVProperty service, instead of periodic re-reads. Each one is a separate COV subscription on the device — leave off unless you need instant updates and know your device supports it. | None |
| **Rescan device** | Re-read the device's object list to add or remove objects | — |

After this page comes [Customize objects](#customize-objects). Changes take effect immediately — the integration reloads automatically.

### Customize objects

The second options page lists your objects by name. Tick the ones you want to change — objects that already have a custom setting are ticked. The next page shows, for just those objects:

- **type** — the Home Assistant entity type (`sensor`, `binary_sensor`, `switch`, `number`, `climate`, or `select` for multi-state objects)
- **COV** — use COV for this object, overriding the device-wide **Enable COV** setting
- **temperature sensor** (climate objects only) — the object that provides the room temperature. Without one, the climate entity shows its setpoint as the current temperature. Just changed an object to climate? Save, then open the options again to pick its sensor.

Unticked objects use the defaults. Settings of objects you remove with a rescan are remembered and come back if you add the object again.

> The default type depends on whether a Value object is commandable: a non-commandable Binary Value is a `binary_sensor`, a commandable one a `switch`. Versions before 1.0.47 could save the wrong type when you saved the options; if that happened, a **Repairs** message offers to reset those objects.

### Reconfigure

To change the local IP address, port, BBMD settings or the device's address, open the integration's **⋮** menu and choose **Reconfigure**. Entities, their history and all options are kept.

### Write Priority entity

A **Write Priority** select entity (under *Configuration* on the device page) is added to every BACnet device. It is **disabled by default** to keep dashboards clean.

To use it:
1. Go to **Settings → Devices & Services → BACnet** → your device
2. Find the **Write Priority** entity and enable it
3. Use the dropdown to select your desired priority level

Available levels: `8` (Manual Operator), `9`, `12`, `13`, `14`, `15`, `16` (default — lowest)

> BACnet Priority Array levels run 1–16. Priority 16 is the safest default. Use level 8 if your BAS requires Manual Operator override. The selected priority persists across HA restarts.

### Live metadata properties

By default, changes to an object's metadata on the BACnet device (`objectName`, `description`, `units`, `stateText`, limits) are picked up automatically within about an hour (or sooner if the object also has active COV traffic) — see [issue #26](https://github.com/CervezaStallone/Home-Assistant-BACnet-integration/issues/26). No action is needed for this baseline behavior. The **Refresh object metadata** button (under *Configuration* on the device page) checks immediately.

If you need those changes reflected immediately, enable one or more properties under **Live metadata properties** in **Configure**. This uses BACnet's `SubscribeCOVProperty` service (ASHRAE 135-2012 Addendum ar) to get a live push the moment the property changes on the device — but:

- It's a **separate COV subscription per property, per object**. Enabling all three on 20 objects adds 60 extra subscriptions on top of the 20 already used for live values — many devices cap total COV subscriptions well below that.
- Not all devices implement this addendum. Unsupported properties/devices silently fall back to the default periodic behavior above — no error, no user action needed.

---

## Entity attributes

Every entity exposes additional BACnet metadata as state attributes:

| Attribute | Description |
|---|---|
| `bacnet_object_type` | BACnet object type name (e.g. "Analog Input") |
| `bacnet_instance` | BACnet object instance number |
| `bacnet_commandable` | Whether this object supports writes |
| `bacnet_units` | Engineering units (e.g. "degrees-celsius") |
| `bacnet_description` | BACnet description property |
| `bacnet_status_flags` | BACnet status flags array |
| `bacnet_update_method` | How this entity is updated: `COV` or `polling` |
| `bacnet_cov_increment` | Configured COV sensitivity (analog objects with active COV only) |
| `bacnet_state_text` | The `stateText` label of the current state (multi-state objects only) |

Only `bacnet_status_flags` and `bacnet_state_text` are stored in the recorder history — the other attributes don't change and aren't stored again with every state change.

An entity is **unavailable** when the device doesn't respond, or when the object's status flags report **FAULT** (the device itself says the value is unreliable). In-alarm, overridden and out-of-service objects stay available.

Analog sensors keep the full precision of the device value; Home Assistant shows 2 decimals by default, which you can change per entity.

### Diagnostic sensors

Every device also gets diagnostic sensors (under *Diagnostic* on the device page):

| Sensor | Enabled by default |
|---|---|
| Last successful poll | Yes |
| COV subscriptions | No |
| Consecutive failed polls | No |

They stay available while the device is offline, which is exactly when they're useful.

---

## Device information

The integration automatically reads device identity from BACnet during discovery:

- **Manufacturer** — from BACnet `vendorName` (property 121)
- **Model** — from BACnet `modelName` (property 70)
- **Software version** — from `applicationSoftwareVersion`
- **Firmware version** — from `firmwareRevision`

This information appears in the Home Assistant device registry, so you can see exactly what hardware you're working with.

---

## How writes work

### Priority Array

All writes to commandable objects use the BACnet **Priority Array** (ASHRAE 135):

- **Turn ON / Set value** → writes at the configured priority level (default: 16)
- **Turn OFF** → writes `inactive` (0) at the same priority level — this *commands* the output off, it doesn't release it
- **Climate → Off** → relinquishes the setpoint (writes Null), so the device's own schedule or Relinquish Default takes over

The priority level is controlled by the **Write Priority** select entity on the device (disabled by default — see [Configuration options](#configuration-options) above). Number entities use the object's `minPresValue`, `maxPresValue` and `resolution` as limits and step when the device provides them.

If the device rejects a write, Home Assistant shows an error message instead of silently keeping the old state.

Binary outputs use the `Enumerated` BACnet type (`0 = inactive`, `1 = active`), compliant with ASHRAE 135.

### Services

| Service | What it does |
|---|---|
| `bacnet.relinquish` | Release Home Assistant's command on an object (write Null) so lower priorities or the Relinquish Default take over. Optional `priority` (1–16), default: the device's Write Priority. |
| `bacnet.write_value` | Write a value once at a chosen `priority`, without changing the device's Write Priority setting. Binary objects: `0` = inactive, `1` = active. |

Both work on BACnet switch, number and climate entities:

```yaml
action: bacnet.relinquish
target:
  entity_id: switch.ahu_1_supply_fan
data:
  priority: 8
```

---

## Troubleshooting

| Problem | Likely cause | Solution |
|---|---|---|
| "No devices found" | Device not running or on a different subnet | Verify the device is reachable on UDP 47808 |
| "Cannot connect" | Port 47808 already in use | Stop other BACnet software or use a different port |
| Entities show "Unavailable" | Device went offline, or the object reports FAULT | Check the device (and **Settings → Repairs**) — entities recover automatically. A single unavailable entity with others fine: check its `bacnet_status_flags`. |
| "The BACnet device rejected the write" | Wrong write priority, or object not writable | Check the log for the reason; enable the **Write Priority** entity or check `bacnet_commandable` |
| Want to release an override | Turn off commands `inactive`, it doesn't release | Use the `bacnet.relinquish` service |
| COV not working | Device doesn't support COV | This is normal — polling activates as fallback |
| Write has no effect | Object is not commandable | Check the `bacnet_commandable` attribute |
| Values don't update | COV increment too high, or polling interval too long | Lower the COV increment or reduce the polling interval |
| Entities go unavailable after network blip | Expected — the integration detects outages and reconnects automatically. Entities recover once the device is reachable again. | No action needed |
| Write has no effect at expected priority | Device requires a specific priority level | Enable the **Write Priority** entity and select the correct level |

### Debug logging

Add this to your `configuration.yaml` for detailed BACnet logs:

```yaml
logger:
  logs:
    custom_components.bacnet: debug
```

### Diagnostics download

When reporting a problem, attach the diagnostics file: **Settings → Devices & Services → BACnet IP → ⋮ → Download diagnostics**. Network addresses are redacted.

### Advanced tuning

The defaults suit most controllers. For unusual devices they can be changed in `custom_components/bacnet/const.py`:

| Constant | Default | Meaning |
|---|---|---|
| `RPM_MAX_OBJECTS` | `25` | Objects per ReadPropertyMultiple poll request (halved automatically when the device rejects a request as too large) |
| `MAX_CONCURRENT_REQUESTS` | `4` | Parallel requests per device when individual reads are needed |
| `METADATA_PERSIST_DELAY` | `5` s | Metadata changes found within this window cause one reload together |

---

## Requirements

- **Home Assistant** 2024.4.0 or newer
- **Python** 3.12+
- **Network** UDP port 47808 accessible between HA and BACnet devices
- **Cross-subnet** A BBMD or BACnet router if devices are on a different network

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-improvement`)
3. Submit a Pull Request

---

## License

GPL-3.0 — see [LICENSE](LICENSE) for details.

---

<p align="center">
  Developed by <strong><a href="https://brdc.nl">BRDC</a></strong><br>
  Powered by <a href="https://github.com/JoelBender/BACpypes3">BACpypes3</a> · Built for <a href="https://www.home-assistant.io/">Home Assistant</a>
</p>
