"""Subprocess adapter over scripts/unifi.py.

Reuses the existing, unmodified OpenClaw skill script as the single source of
truth for UniFi auth, HTTP, site-resolution, and caching logic, instead of
reimplementing it for the MCP server.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _resolve_script() -> Path:
    # Dev checkout: src/unifi_mcp/cli.py -> repo root -> scripts/unifi.py
    dev = Path(__file__).resolve().parents[2] / "scripts" / "unifi.py"
    if dev.exists():
        return dev
    # Installed wheel: the script ships as package data (see pyproject
    # force-include) — a repo-relative path cannot work here.
    packaged = Path(__file__).resolve().parent / "_data" / "unifi.py"
    if packaged.exists():
        return packaged
    raise FileNotFoundError(
        "unifi.py not found (looked for a dev checkout and packaged data); "
        "reinstall unifi-mcp >= 0.2.0"
    )


SCRIPT_PATH = _resolve_script()

# scripts/unifi.py assumes ~/.openclaw/ already exists (true on an OpenClaw host,
# which creates it) — make sure it's there when run standalone via this MCP server.
(Path.home() / ".openclaw").mkdir(parents=True, exist_ok=True)

FLAG_NAMES = {
    "site": "--site",
    "id": "--id",
    "limit": "--limit",
    "next_token": "--next-token",
    "metric_type": "--metric-type",
    "begin": "--begin",
    "end": "--end",
    "duration": "--duration",
    "from_zone": "--from-zone",
    "to_zone": "--to-zone",
}


def run(subcommand: str, **flags) -> dict | list:
    argv = [sys.executable, str(SCRIPT_PATH), subcommand]
    for key, value in flags.items():
        if value is None:
            continue
        flag = FLAG_NAMES.get(key)
        if flag is None:
            raise ValueError(f"Unknown flag: {key}")
        argv.extend([flag, str(value)])

    result = subprocess.run(argv, capture_output=True, text=True, timeout=30)

    if result.returncode != 0:
        message = result.stderr.strip()
        try:
            message = json.loads(message).get("error", message)
        except (json.JSONDecodeError, AttributeError):
            pass
        if "401" in message or "Unauthorized" in message:
            message += (
                " — hint: UniFi API keys expire; check/rotate the key at "
                "unifi.ui.com -> Settings -> Control Plane -> Integrations."
            )
        raise RuntimeError(message or f"unifi.py exited with code {result.returncode}")

    return json.loads(result.stdout)
