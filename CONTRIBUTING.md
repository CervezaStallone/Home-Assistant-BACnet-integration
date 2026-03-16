# Contributing to BACnet IP Integration for Home Assistant

Thank you for your interest in contributing! This project is a custom Home Assistant integration that brings BACnet/IP support to Home Assistant, built in Python on top of [BACpypes3](https://github.com/JoelBender/BACpypes3). Contributions of all kinds are welcome — bug reports, feature requests, documentation improvements, and code changes.

---

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [Making Changes](#making-changes)
- [Coding Standards](#coding-standards)
- [Submitting a Pull Request](#submitting-a-pull-request)
- [Reporting Bugs](#reporting-bugs)
- [Requesting Features](#requesting-features)
- [Security Issues](#security-issues)

---

## Code of Conduct

Please be respectful and constructive in all interactions. This project follows the [Contributor Covenant](https://www.contributor-covenant.org/) code of conduct. Harassment, discrimination, or disruptive behaviour will not be tolerated.

---

## Getting Started

1. **Fork** the repository on GitHub.
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/your-username/Home-Assistant-BACnet-integration.git
   cd Home-Assistant-BACnet-integration
   ```
3. Add the upstream remote so you can stay up to date:
   ```bash
   git remote add upstream https://github.com/CervezaStallone/Home-Assistant-BACnet-integration.git
   ```

---

## Development Setup

### Requirements

- **Python 3.11+**
- **Home Assistant** 2024.1.0 or newer (for local testing)
- A BACnet/IP device or simulator (e.g. [VTS](https://sourceforge.net/projects/vts/), [YABE](https://sourceforge.net/projects/yetanotherbacnetexplorer/), or a software BACnet server)

### Install dependencies

It is recommended to work inside a virtual environment:

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install bacpypes3 homeassistant
```

### Linking the integration to a local Home Assistant instance

Copy (or symlink) the `custom_components/bacnet` folder into your Home Assistant `config/custom_components/` directory:

```bash
cp -r custom_components/bacnet /path/to/your/ha-config/custom_components/bacnet
```

Restart Home Assistant to pick up the changes.

### Enable debug logging

Add the following to your `configuration.yaml` while developing:

```yaml
logger:
  logs:
    custom_components.bacnet: debug
```

---

## Project Structure

```
custom_components/bacnet/
├── __init__.py           # Integration setup & entry point
├── manifest.json         # Integration metadata (version, dependencies)
├── config_flow.py        # GUI setup wizard (ConfigFlow / OptionsFlow)
├── coordinator.py        # DataUpdateCoordinator — polling & COV management
├── bacnet_client.py      # BACpypes3 wrapper — device discovery & I/O
├── entity_base.py        # Shared base class for all HA entities
├── sensor.py             # Sensor entities (Analog/Multi-State Inputs & Values)
├── binary_sensor.py      # Binary sensor entities (Binary Inputs & Values)
├── switch.py             # Switch entities (Binary Outputs & Values)
├── number.py             # Number entities (Analog/Multi-State Outputs & Values)
├── strings.json          # Translatable UI strings
└── translations/
    └── en.json           # English translations
```

---

## Making Changes

1. **Sync** with upstream before starting:
   ```bash
   git fetch upstream
   git checkout master
   git merge upstream/master
   ```

2. **Create a feature branch** — use a descriptive name:
   ```bash
   git checkout -b feature/add-schedule-object-support
   # or
   git checkout -b fix/cov-resubscription-on-reconnect
   ```

3. **Make your changes** following the [coding standards](#coding-standards) below.

4. **Test your changes** against a real or simulated BACnet device and verify nothing is broken in the Home Assistant UI.

5. **Commit** with a clear, concise message:
   ```bash
   git commit -m "feat: add support for Schedule (BACnet type 17) objects"
   ```
   Preferred commit message prefixes:
   | Prefix | When to use |
   |--------|-------------|
   | `feat:` | New functionality |
   | `fix:` | Bug fixes |
   | `docs:` | Documentation only |
   | `refactor:` | Code restructuring without behaviour change |
   | `test:` | Adding or updating tests |
   | `chore:` | Dependency bumps, CI, tooling |

6. **Push** to your fork:
   ```bash
   git push origin feature/add-schedule-object-support
   ```

---

## Coding Standards

This project follows the [Home Assistant developer guidelines](https://developers.home-assistant.io/docs/development_guidelines) and general Python best practices.

### Style

- Follow **[PEP 8](https://peps.python.org/pep-0008/)**.
- Use **type hints** for all function signatures.
- Format code with **[Black](https://github.com/psf/black)** (default settings):
  ```bash
  pip install black
  black custom_components/bacnet/
  ```
- Sort imports with **[isort](https://pycqa.github.io/isort/)**:
  ```bash
  pip install isort
  isort custom_components/bacnet/
  ```

### Home Assistant conventions

- Use `DataUpdateCoordinator` for all polling logic — do not poll inside entity `update()` methods.
- Register entities via `async_setup_entry` — avoid synchronous setup.
- Follow the HA entity naming guidelines; use `has_entity_name = True` where applicable.
- Never store mutable state directly on an entity; always read from the coordinator's data.

### BACnet / BACpypes3

- Use `BACpypes3` async APIs only — no blocking calls inside coroutines.
- Respect the BACnet Priority Array for all writes (default priority 16).
- Log unexpected BACnet errors at `WARNING` level; log expected conditions (e.g. device offline) at `DEBUG`.

---

## Submitting a Pull Request

1. Open a Pull Request from your feature branch to `master` on this repository.
2. Fill in the PR description:
   - **What** was changed and **why**.
   - Steps to **reproduce** the issue being fixed (if applicable).
   - Screenshots or log output if relevant.
3. Ensure your branch is up to date with `master` before requesting a review.
4. A maintainer will review your PR. Please be responsive to feedback — small requested changes keep the review cycle short.
5. Once approved, your PR will be merged using **squash merge** to keep the history clean.

---

## Reporting Bugs

Please [open an issue](https://github.com/CervezaStallone/Home-Assistant-BACnet-integration/issues/new) and include:

- **Home Assistant version**
- **Integration version** (from HACS or `manifest.json`)
- **Python version**
- A clear **description** of the problem
- **Steps to reproduce**
- **Debug logs** (see [debug logging](#enable-debug-logging) above)
- BACnet device details (vendor, model) if relevant

---

## Requesting Features

[Open an issue](https://github.com/CervezaStallone/Home-Assistant-BACnet-integration/issues/new) with:

- A description of the **use case** and **problem** it solves.
- The BACnet object type(s) or service(s) involved, if applicable.
- Any relevant ASHRAE 135 section references.

---

## Security Issues

Please **do not** open a public issue for security vulnerabilities. Instead, follow the process described in [SECURITY.md](SECURITY.md).

---

## License

By contributing to this project you agree that your contributions will be licensed under the [GNU General Public License v3.0](LICENSE).

---

<p align="center">
  Developed by <strong><a href="https://brdc.nl">BRDC</a></strong><br>
  Powered by <a href="https://github.com/JoelBender/BACpypes3">BACpypes3</a> · Built for <a href="https://www.home-assistant.io/">Home Assistant</a>
</p>
