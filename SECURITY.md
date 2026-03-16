# Security Policy

## Supported Versions

Only the latest release of the BACnet IP integration is actively maintained and will receive security fixes.

| Version | Supported          |
| ------- | ------------------ |
| Latest  | ✅ Yes             |
| Older   | ❌ No              |

## Reporting a Vulnerability

We take security seriously — especially given that this integration communicates directly with BACnet/IP devices on building automation networks, which may control physical infrastructure (HVAC, lighting, access control, etc.).

**Please do NOT open a public GitHub issue to report a security vulnerability.**

Instead, report vulnerabilities privately using one of the following methods:

- **GitHub Private Security Advisory** (preferred):
  Go to [Security → Advisories](https://github.com/CervezaStallone/Home-Assistant-BACnet-integration/security/advisories/new) and open a draft advisory. This keeps the report confidential until a fix is available.
- **Email**: Contact the maintainer directly via their [GitHub profile](https://github.com/CervezaStallone).

### What to include in your report

To help us triage and reproduce the issue quickly, please include:

- A clear description of the vulnerability and its potential impact
- The version(s) of the integration affected
- Steps to reproduce the issue or a proof-of-concept (if applicable)
- Any relevant logs, screenshots, or configuration snippets (with sensitive data redacted)
- Your suggested severity (Critical / High / Medium / Low)

## Response Timeline

| Stage                          | Target timeframe |
| ------------------------------ | ---------------- |
| Acknowledgement of report      | Within 48 hours  |
| Initial assessment & triage    | Within 7 days    |
| Fix developed & tested         | Within 30 days   |
| Public disclosure (coordinated)| After fix is released |

We follow **coordinated disclosure**: we will work with you to agree on a disclosure timeline before publishing details of the vulnerability.

## Scope

The following are considered **in scope** for security reports:

- **Credential exposure**: API keys, tokens, or credentials being logged or stored insecurely
- **Unauthorized BACnet write access**: Logic flaws that allow unintended writes to BACnet objects (e.g. bypassing priority levels)
- **Injection vulnerabilities**: Crafted BACnet responses that cause unsafe behaviour in Home Assistant
- **Denial of service**: Issues that cause Home Assistant to crash or become unresponsive via malformed BACnet traffic
- **Dependency vulnerabilities**: Critical CVEs in `bacpypes3`, `voluptuous`, or `homeassistant` that directly affect this integration's security posture

The following are **out of scope**:

- Vulnerabilities in Home Assistant Core itself — report those to the [Home Assistant security team](https://www.home-assistant.io/security/)
- Vulnerabilities in the BACnet protocol or third-party BACnet devices
- Issues that require physical access to the network
- Social engineering

## Security Best Practices for Users

When deploying this integration, we recommend the following:

- **Network isolation**: Place BACnet/IP devices on a dedicated VLAN, isolated from the general network and the internet.
- **Firewall rules**: Restrict UDP port 47808 (BACnet/IP) to trusted hosts only. Never expose BACnet directly to the internet.
- **BBMD security**: If using a BBMD (BACnet Broadcast Management Device) for cross-subnet communication, ensure it is behind a firewall and not publicly reachable.
- **Home Assistant security**: Keep Home Assistant updated and enable two-factor authentication on your account.
- **Least privilege**: Use read-only BACnet object mappings where write access is not required.
- **Logging**: Monitor Home Assistant logs for unexpected BACnet write operations.

## Disclosure Policy

Once a fix has been released, we will publish a security advisory on the [GitHub Security Advisories](https://github.com/CervezaStallone/Home-Assistant-BACnet-integration/security/advisories) page, crediting the reporter (unless they prefer to remain anonymous).

## Acknowledgements

We thank all security researchers who responsibly disclose vulnerabilities and help make this integration safer for everyone.
