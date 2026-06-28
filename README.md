[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=CervezaStallone&repository=Home-Assistant-BACnet-integration&category=integration)

<p align="center">
  <img src="img/logo.png" alt="BACnet IP Integration" width="256">
</p>

<h1 align="center">BACnet IP Integration for Home Assistant</h1>

<p align="center">
  Connect your building automation system to Home Assistant — no YAML, no hassle.<br>
  Monitor sensors, control outputs, and automate your BACnet/IP devices through a simple GUI.
</p>

<p align="center">
  <strong>HACS Compatible</strong> · GUI-only setup · Real-time COV updates · ASHRAE 135 compliant
</p>

---

<a href="buymeacoffee.com/Cervezastalone" target="_blank" title="buymeacoffee">
  <img src="https://iili.io/JoQ1MeS.md.png"  alt="buymeacoffee-yellow-badge" style="width: 208px;">
</a>

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
- **Domain mapping** — override default entity types per object (e.g. make a sensor into a number)
- **Selective import** — only import the objects you actually need

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
| **Polling interval** | How often to poll objects without COV support (in seconds) | `30` |
| **Use description** | Show BACnet `description` (property 28) instead of `objectName` as entity name | Off |
| **Domain mapping** | Change the HA entity type per object (e.g. sensor → number, switch → binary_sensor) | Auto |
| **Per-object COV override** | Override **Enable COV** for an individual object — shown as a checkbox next to its domain mapping. Objects with no override use the device-wide **Enable COV** setting. | Follows **Enable COV** |

Changes take effect immediately — the integration reloads automatically.

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

- **Turn ON / Set value** → writes at priority level 16 (safe default)
- **Turn OFF** → writes `inactive` (0) at priority 16

Binary outputs use the `Enumerated` BACnet type (`0 = inactive`, `1 = active`), compliant with ASHRAE 135.

---

## Troubleshooting

| Problem | Likely cause | Solution |
|---|---|---|
| "No devices found" | Device not running or on a different subnet | Verify the device is reachable on UDP 47808 |
| "Cannot connect" | Port 47808 already in use | Stop other BACnet software or use a different port |
| Entities show "Unavailable" | Device went offline | Restart the device — entities recover automatically |
| COV not working | Device doesn't support COV | This is normal — polling activates as fallback |
| Write has no effect | Object is not commandable | Check the `bacnet_commandable` attribute |
| Values don't update | COV increment too high, or polling interval too long | Lower the COV increment or reduce the polling interval |

### Debug logging

Add this to your `configuration.yaml` for detailed BACnet logs:

```yaml
logger:
  logs:
    custom_components.bacnet: debug
```

---

## Requirements

- **Home Assistant** 2024.1.0 or newer
- **Python** 3.11+
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
