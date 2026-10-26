"""Turn a "Device compatibility report" issue into a tested devices entry.

Run by .github/workflows/device-report.yml when the maintainer labels a
report "verified": adds the entry to .github/tested_devices.json and regenerates the
table in TESTED_DEVICES.md. The issue text is untrusted; it only ever reaches this
script through an environment variable, never a shell command.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

_FIELDS = {
    "Manufacturer": "vendor",
    "Model": "model",
    "Firmware version": "firmware",
    "BACnet integration version": "version",
    "Connection": "connection",
    "What works": "works",
    "Notes": "notes",
}


def _sections(body: str) -> dict[str, str]:
    """{heading: text} of an issue form body ("### Heading" blocks)."""
    sections: dict[str, str] = {}
    for block in re.split(r"^### ", body, flags=re.MULTILINE)[1:]:
        heading, _, text = block.partition("\n")
        text = text.strip()
        sections[heading.strip()] = "" if text == "_No response_" else text
    return sections


def entry_from_issue(body: str, issue: int) -> dict:
    """The devices.json entry for a report issue."""
    sections = _sections(body)
    entry: dict = {key: sections.get(heading, "") for heading, key in _FIELDS.items()}
    entry["works"] = re.findall(r"^- \[[xX]\] (.+)$", entry["works"], re.MULTILINE)
    entry["issue"] = issue
    return entry


_HEADER = "| Manufacturer | Model | Firmware |\n|---|---|---|"


def _clean(text: str, limit: int = 60) -> str:
    """Plain table text: no markdown, HTML or line breaks from the report."""
    text = re.sub(r"\S*://\S*|www\.\S*", "", str(text))  # no links
    text = re.sub(r"[^\w .,:;/+&'#%-]", "", text)
    return " ".join(text.split())[:limit].strip()


def render_table(devices: list[dict]) -> str:
    """Markdown table of the tested devices, sorted by manufacturer and model.

    One row per make and model (case-insensitive); each firmware reported
    for it gets its own line in the firmware cell. Only what it works on;
    the report details stay in tested_devices.json.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for d in sorted(devices, key=lambda d: (d["vendor"].lower(), d["model"].lower())):
        groups.setdefault((d["vendor"].lower(), d["model"].lower()), []).append(d)
    rows = [_HEADER]
    for same in groups.values():
        firmware = sorted({_clean(d.get("firmware", "")) for d in same} - {""})
        rows.append(
            f"| {_clean(same[0]['vendor'])} | {_clean(same[0]['model'])} "
            f"| {'<br>'.join(firmware) or '—'} |"
        )
    return "\n".join(rows)


_START = "<!-- tested-devices:start -->"
_END = "<!-- tested-devices:end -->"


def add_report(body: str, issue: int, devices_path: Path, table_path: Path) -> None:
    """Add (or replace) the report's entry and regenerate the table."""
    devices = json.loads(devices_path.read_text(encoding="utf-8"))
    devices = [d for d in devices if d["issue"] != issue]
    devices.append(entry_from_issue(body, issue))
    devices.sort(key=lambda d: (d["vendor"].lower(), d["model"].lower()))
    devices_path.write_text(
        json.dumps(devices, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    text = table_path.read_text(encoding="utf-8")
    before, rest = text.split(_START, 1)
    _, after = rest.split(_END, 1)
    table_path.write_text(
        f"{before}{_START}\n{render_table(devices)}\n{_END}{after}", encoding="utf-8"
    )


if __name__ == "__main__":
    # Called from the workflow with the issue in environment variables, so
    # the untrusted issue text never passes through a shell.
    root = Path(__file__).resolve().parent.parent
    add_report(
        os.environ["ISSUE_BODY"],
        int(os.environ["ISSUE_NUMBER"]),
        root / ".github" / "tested_devices.json",
        root / "TESTED_DEVICES.md",
    )
