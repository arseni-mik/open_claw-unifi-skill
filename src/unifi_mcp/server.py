"""MCP server for read-only UniFi network queries.

Wraps the existing OpenClaw skill script (scripts/unifi.py) via subprocess —
that script remains the single source of truth for auth, HTTP, site
resolution, and caching. This server only translates ~18 grouped tools into
the script's 44 existing subcommands + flags.

Tool groups follow UniFi's own developer.ui.com API documentation categories
(Site Manager: Hosts, Sites, Devices, ISP Metrics, SD-WAN Configs; Network:
Application Info, Sites, UniFi Devices, Clients, Networks, WiFi Broadcasts,
Hotspot, Firewall, ACL Rules, DNS Policies, Traffic Matching Lists, Supporting
Resources) rather than one tool per endpoint, to keep the tool list small.

Note: UniFi's "Switching" API category has no corresponding subcommand in
scripts/unifi.py today, so there is no unifi_net_switching tool here.
"""

from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastmcp import FastMCP
from pydantic import Field
from typing_extensions import Annotated

from . import cli

mcp = FastMCP("unifi")

SUPPORTING_RESOURCES = {
    "wans": "wans",
    "vpn-tunnels": "vpn-tunnels",
    "vpn-servers": "vpn-servers",
    "radius-profiles": "radius",
    "device-tags": "device-tags",
    "dpi-categories": "dpi-categories",
    "dpi-applications": "dpi-applications",
    "countries": "countries",
}


def _dump(data) -> str:
    # Compact separators: this JSON is consumed by an LLM, indentation is pure
    # token overhead.
    return json.dumps(data, separators=(",", ":"))


def _run(subcommand: str, **flags) -> str:
    return _dump(cli.run(subcommand, **flags))


# ---------- Compaction (Site Manager objects are huge: ~13 KB per console) ----------

def compact_host(host: dict) -> dict:
    rs = host.get("reportedState") or {}
    wans = rs.get("wans") or []
    out = {
        "id": host.get("id"),
        "name": rs.get("name") or rs.get("hostname"),
        "type": host.get("type"),
        "ipAddress": host.get("ipAddress"),
        "state": rs.get("state"),
        "version": rs.get("version"),
        "releaseChannel": rs.get("releaseChannel"),
        "isBlocked": host.get("isBlocked"),
        "lastConnectionStateChange": host.get("lastConnectionStateChange"),
        "latestBackupTime": host.get("latestBackupTime"),
        "internetIssues5min": bool((rs.get("internetIssues5min") or {}).get("periods")),
        "firmwareUpdateAvailable": bool((rs.get("firmwareUpdate") or {}).get("latestAvailableVersion")),
        "wans": [
            {k: w.get(k) for k in ("name", "type", "ipv4", "status") if w.get(k) is not None}
            for w in wans
        ] or None,
    }
    return {k: v for k, v in out.items() if v is not None}


def compact_sm_device(dev: dict) -> dict:
    keep = (
        "name", "model", "ip", "mac", "status", "version",
        "firmwareStatus", "updateAvailable", "isConsole", "productLine",
    )
    out = {k: dev.get(k) for k in keep if dev.get(k) not in (None, "")}
    return out


def compact_sm_device_groups(groups: list) -> list:
    return [
        {
            "hostName": g.get("hostName"),
            "hostId": g.get("hostId"),
            "devices": [compact_sm_device(d) for d in (g.get("devices") or [])],
        }
        for g in groups
    ]


def filter_by_query(items: list, query: str) -> list:
    """Case-insensitive substring match across common identity fields."""
    q = query.lower()
    fields = ("name", "hostname", "ipAddress", "ip", "macAddress", "mac", "model")
    return [
        it for it in items
        if any(q in str(it.get(f, "")).lower() for f in fields)
    ]


# ---------- Meta ----------


@mcp.tool(
    name="unifi_library",
    description=(
        "List discovered UniFi sites (label, name, hardware, connection state, API "
        "capabilities). Run this first to find the `site` hint for other tools when "
        "the account has more than one site."
    ),
)
async def unifi_library() -> str:
    return await asyncio.to_thread(_run, "library")


@mcp.tool(
    name="unifi_health_summary",
    description=(
        "One-call account-wide health check: every console's connection state, "
        "version, WAN status and internet-issue flag, plus device totals and any "
        "non-online devices per site. Composed from Site Manager hosts + devices — "
        "use this instead of stitching together multiple list calls."
    ),
)
async def unifi_health_summary() -> str:
    hosts, device_groups = await asyncio.gather(
        asyncio.to_thread(cli.run, "hosts"),
        asyncio.to_thread(cli.run, "cloud-devices"),
    )
    consoles = [compact_host(h) for h in hosts] if isinstance(hosts, list) else []
    sites = []
    for g in device_groups if isinstance(device_groups, list) else []:
        devices = g.get("devices") or []
        not_online = [compact_sm_device(d) for d in devices if d.get("status") != "online"]
        updates = [d.get("name") for d in devices if d.get("updateAvailable")]
        sites.append({
            "hostName": g.get("hostName"),
            "devicesTotal": len(devices),
            "devicesOnline": sum(1 for d in devices if d.get("status") == "online"),
            "devicesNotOnline": not_online or None,
            "updatesAvailable": updates or None,
        })
    summary = {
        "consoles": consoles,
        "sites": [{k: v for k, v in s.items() if v is not None} for s in sites],
        "attention": [
            c["name"] for c in consoles
            if c.get("state") != "connected" or c.get("internetIssues5min")
        ] or None,
    }
    return _dump({k: v for k, v in summary.items() if v is not None})


# ---------- Site Manager ----------


@mcp.tool(
    name="unifi_sm_hosts",
    description=(
        "Site Manager: list all UniFi OS consoles on the account, or get one "
        "console's details by id. Returns a compact summary by default (raw "
        "console objects are ~13 KB each); set full=true for the complete object."
    ),
)
async def unifi_sm_hosts(
    id: Annotated[str | None, Field(description="Host id for console details; omit to list all hosts")] = None,
    limit: Annotated[int | None, Field(description="Max results, applies when listing")] = None,
    next_token: Annotated[str | None, Field(description="Pagination token, applies when listing")] = None,
    full: Annotated[bool, Field(description="Return raw console objects instead of the compact summary")] = False,
) -> str:
    subcommand = "host" if id else "hosts"
    data = await asyncio.to_thread(cli.run, subcommand, id=id, limit=limit, next_token=next_token)
    if not full:
        data = [compact_host(h) for h in data] if isinstance(data, list) else compact_host(data)
    return _dump(data)


@mcp.tool(name="unifi_sm_sites", description="Site Manager: list all sites across all consoles on the account.")
async def unifi_sm_sites(
    limit: Annotated[int | None, Field(description="Max results")] = None,
) -> str:
    return await asyncio.to_thread(_run, "sites", limit=limit)


@mcp.tool(
    name="unifi_sm_devices",
    description=(
        "Site Manager: list all devices across all sites on the account "
        "(cloud-wide view). Compact by default; set full=true for raw objects."
    ),
)
async def unifi_sm_devices(
    limit: Annotated[int | None, Field(description="Max results")] = None,
    query: Annotated[str | None, Field(description="Case-insensitive substring filter on name/ip/mac/model")] = None,
    full: Annotated[bool, Field(description="Return raw device objects instead of the compact summary")] = False,
) -> str:
    data = await asyncio.to_thread(cli.run, "cloud-devices", limit=limit)
    if query and isinstance(data, list):
        data = [
            {**g, "devices": filter_by_query(g.get("devices") or [], query)}
            for g in data
        ]
        data = [g for g in data if g.get("devices")]
    if not full and isinstance(data, list):
        data = compact_sm_device_groups(data)
    return _dump(data)


@mcp.tool(
    name="unifi_sm_isp_metrics",
    description=(
        "Site Manager: ISP performance metrics (packet loss, latency) for the "
        "account's WAN connections. NOTE: the API only accepts duration '7d' or "
        "'30d' with metric_type '1h', and '24h' with metric_type '5m'. "
        "[Early access UniFi API]"
    ),
)
async def unifi_sm_isp_metrics(
    metric_type: Annotated[Literal["1h", "5m"], Field(description="Granularity: '1h' (use with duration 7d/30d) or '5m' (use with duration 24h)")] = "1h",
    begin: Annotated[str | None, Field(description="ISO8601 start timestamp")] = None,
    end: Annotated[str | None, Field(description="ISO8601 end timestamp")] = None,
    duration: Annotated[str | None, Field(description="Relative duration; valid combos: 1h+7d, 1h+30d, 5m+24h")] = None,
) -> str:
    if duration is None and begin is None and end is None:
        duration = "7d" if metric_type == "1h" else "24h"
    return await asyncio.to_thread(
        _run, "isp-metrics", metric_type=metric_type, begin=begin, end=end, duration=duration
    )


@mcp.tool(
    name="unifi_sm_sdwan",
    description=(
        "Site Manager: list SD-WAN configurations, or get one config's details or "
        "deployment status by id. [Early access UniFi API; returns empty on accounts "
        "without SD-WAN configured]"
    ),
)
async def unifi_sm_sdwan(
    id: Annotated[str | None, Field(description="SD-WAN config id; omit to list all configs")] = None,
    status: Annotated[bool, Field(description="When id is given, return deployment status instead of config details")] = False,
) -> str:
    if id and status:
        subcommand = "sdwan-status"
    elif id:
        subcommand = "sdwan-config"
    else:
        subcommand = "sdwan"
    return await asyncio.to_thread(_run, subcommand, id=id)


# ---------- Network Integration ----------


@mcp.tool(name="unifi_net_info", description="Network: controller version and capabilities for a site's console.")
async def unifi_net_info(
    site: Annotated[str | None, Field(description="Site label or partial hint (see unifi_library); defaults to the first site")] = None,
) -> str:
    return await asyncio.to_thread(_run, "info", site=site)


@mcp.tool(
    name="unifi_net_sites",
    description="Network: sites managed by a console (as opposed to unifi_sm_sites, which is account-wide).",
)
async def unifi_net_sites(
    site: Annotated[str | None, Field(description="Site label or partial hint")] = None,
    limit: Annotated[int | None, Field(description="Max results")] = None,
) -> str:
    return await asyncio.to_thread(_run, "net-sites", site=site, limit=limit)


@mcp.tool(
    name="unifi_net_devices",
    description=(
        "Network: adopted devices for a site. Get one device's details or latest "
        "statistics by id, or list devices still pending adoption."
    ),
)
async def unifi_net_devices(
    site: Annotated[str | None, Field(description="Site label or partial hint")] = None,
    id: Annotated[str | None, Field(description="Device id for details or statistics; omit to list devices")] = None,
    stats: Annotated[bool, Field(description="When id is given, return latest statistics instead of device details")] = False,
    pending: Annotated[bool, Field(description="List devices pending adoption instead (ignores id/stats)")] = False,
    query: Annotated[str | None, Field(description="Case-insensitive substring filter on name/ip/mac/model; searches up to 200 devices server-side")] = None,
    limit: Annotated[int | None, Field(description="Max results, applies when listing")] = None,
    next_token: Annotated[str | None, Field(description="Pagination token, applies when listing")] = None,
) -> str:
    if pending:
        subcommand = "devices-pending"
    elif id and stats:
        subcommand = "device-stats"
    elif id:
        subcommand = "device"
    else:
        subcommand = "devices"
    if subcommand == "devices" and query:
        data = await asyncio.to_thread(cli.run, "devices", site=site, limit=limit or 200)
        matches = filter_by_query(data if isinstance(data, list) else [], query)
        return _dump({"query": query, "matches": matches, "count": len(matches)})
    return await asyncio.to_thread(_run, subcommand, site=site, id=id, limit=limit, next_token=next_token)


@mcp.tool(
    name="unifi_net_clients",
    description=(
        "Network: connected clients for a site, or one client's details by id. "
        "Use query= to find a client by name/IP/MAC without listing everything."
    ),
)
async def unifi_net_clients(
    site: Annotated[str | None, Field(description="Site label or partial hint")] = None,
    id: Annotated[str | None, Field(description="Client id for details; omit to list clients")] = None,
    query: Annotated[str | None, Field(description="Case-insensitive substring filter on name/ip/mac; searches up to 200 clients server-side")] = None,
    limit: Annotated[int | None, Field(description="Max results, applies when listing")] = None,
    next_token: Annotated[str | None, Field(description="Pagination token, applies when listing")] = None,
) -> str:
    if id:
        return await asyncio.to_thread(_run, "client", site=site, id=id)
    if query:
        data = await asyncio.to_thread(cli.run, "clients", site=site, limit=limit or 200)
        matches = filter_by_query(data if isinstance(data, list) else [], query)
        return _dump({"query": query, "matches": matches, "count": len(matches)})
    return await asyncio.to_thread(_run, "clients", site=site, limit=limit, next_token=next_token)


@mcp.tool(
    name="unifi_net_networks",
    description=(
        "Network: networks for a site, one network's details by id, or what "
        "references a network (devices/clients/WiFi assigned to it). Networks with "
        "no active references return a handled error — list networks first and pick "
        "one that carries traffic."
    ),
)
async def unifi_net_networks(
    site: Annotated[str | None, Field(description="Site label or partial hint")] = None,
    id: Annotated[str | None, Field(description="Network id for details or references; omit to list networks")] = None,
    refs: Annotated[bool, Field(description="When id is given, return what references this network instead of its details")] = False,
    limit: Annotated[int | None, Field(description="Max results, applies when listing")] = None,
    next_token: Annotated[str | None, Field(description="Pagination token, applies when listing")] = None,
) -> str:
    if id and refs:
        subcommand = "network-refs"
    elif id:
        subcommand = "network"
    else:
        subcommand = "networks"
    return await asyncio.to_thread(_run, subcommand, site=site, id=id, limit=limit, next_token=next_token)


@mcp.tool(name="unifi_net_wifi", description="Network: WiFi broadcasts (SSIDs) for a site, or one broadcast's details by id.")
async def unifi_net_wifi(
    site: Annotated[str | None, Field(description="Site label or partial hint")] = None,
    id: Annotated[str | None, Field(description="WiFi broadcast id for details; omit to list broadcasts")] = None,
    limit: Annotated[int | None, Field(description="Max results, applies when listing")] = None,
    next_token: Annotated[str | None, Field(description="Pagination token, applies when listing")] = None,
) -> str:
    subcommand = "wifi-details" if id else "wifi"
    return await asyncio.to_thread(_run, subcommand, site=site, id=id, limit=limit, next_token=next_token)


@mcp.tool(name="unifi_net_hotspot", description="Network: hotspot vouchers for a site, or one voucher's details by id.")
async def unifi_net_hotspot(
    site: Annotated[str | None, Field(description="Site label or partial hint")] = None,
    id: Annotated[str | None, Field(description="Voucher id for details; omit to list vouchers")] = None,
    limit: Annotated[int | None, Field(description="Max results, applies when listing")] = None,
    next_token: Annotated[str | None, Field(description="Pagination token, applies when listing")] = None,
) -> str:
    subcommand = "voucher" if id else "vouchers"
    return await asyncio.to_thread(_run, subcommand, site=site, id=id, limit=limit, next_token=next_token)


@mcp.tool(
    name="unifi_net_firewall",
    description=(
        "Network: firewall zones or policies for a site, a specific zone/policy by "
        "id, or policy ordering between two zones (requires from_zone and to_zone — "
        "call with resource='zones' first to get real zone ids)."
    ),
)
async def unifi_net_firewall(
    resource: Annotated[Literal["zones", "policies", "ordering"], Field(description="Which firewall resource to query")],
    site: Annotated[str | None, Field(description="Site label or partial hint")] = None,
    id: Annotated[str | None, Field(description="Zone or policy id for details; omit to list")] = None,
    from_zone: Annotated[str | None, Field(description="Source firewall zone id, required when resource='ordering'")] = None,
    to_zone: Annotated[str | None, Field(description="Destination firewall zone id, required when resource='ordering'")] = None,
    limit: Annotated[int | None, Field(description="Max results, applies when listing")] = None,
    next_token: Annotated[str | None, Field(description="Pagination token, applies when listing")] = None,
) -> str:
    if resource == "ordering":
        subcommand = "firewall-ordering"
    elif resource == "zones":
        subcommand = "firewall-zone" if id else "firewall-zones"
    else:
        subcommand = "firewall-policy" if id else "firewall-policies"
    return await asyncio.to_thread(
        _run, subcommand, site=site, id=id, from_zone=from_zone, to_zone=to_zone, limit=limit, next_token=next_token
    )


@mcp.tool(
    name="unifi_net_acl",
    description="Network: ACL rules for a site, one rule's details by id, or the rule evaluation ordering.",
)
async def unifi_net_acl(
    site: Annotated[str | None, Field(description="Site label or partial hint")] = None,
    id: Annotated[str | None, Field(description="ACL rule id for details; omit to list rules")] = None,
    ordering: Annotated[bool, Field(description="Return ACL rule evaluation ordering instead (ignores id)")] = False,
    limit: Annotated[int | None, Field(description="Max results, applies when listing")] = None,
    next_token: Annotated[str | None, Field(description="Pagination token, applies when listing")] = None,
) -> str:
    if ordering:
        subcommand = "acl-ordering"
    elif id:
        subcommand = "acl-rule"
    else:
        subcommand = "acl-rules"
    return await asyncio.to_thread(_run, subcommand, site=site, id=id, limit=limit, next_token=next_token)


@mcp.tool(name="unifi_net_dns", description="Network: DNS policies for a site, or one policy's details by id.")
async def unifi_net_dns(
    site: Annotated[str | None, Field(description="Site label or partial hint")] = None,
    id: Annotated[str | None, Field(description="DNS policy id for details; omit to list policies")] = None,
    limit: Annotated[int | None, Field(description="Max results, applies when listing")] = None,
    next_token: Annotated[str | None, Field(description="Pagination token, applies when listing")] = None,
) -> str:
    subcommand = "dns-policy" if id else "dns-policies"
    return await asyncio.to_thread(_run, subcommand, site=site, id=id, limit=limit, next_token=next_token)


@mcp.tool(name="unifi_net_traffic", description="Network: traffic matching lists for a site, or one list's details by id.")
async def unifi_net_traffic(
    site: Annotated[str | None, Field(description="Site label or partial hint")] = None,
    id: Annotated[str | None, Field(description="Traffic matching list id for details; omit to list")] = None,
    limit: Annotated[int | None, Field(description="Max results, applies when listing")] = None,
    next_token: Annotated[str | None, Field(description="Pagination token, applies when listing")] = None,
) -> str:
    subcommand = "traffic-list" if id else "traffic-lists"
    return await asyncio.to_thread(_run, subcommand, site=site, id=id, limit=limit, next_token=next_token)


@mcp.tool(
    name="unifi_net_supporting",
    description=(
        "Network: supporting resources for a site — WAN interfaces, VPN "
        "tunnels/servers, RADIUS profiles, device tags, DPI categories/applications, "
        "or the countries list (used for geo-IP firewall rules)."
    ),
)
async def unifi_net_supporting(
    resource: Annotated[
        Literal[
            "wans", "vpn-tunnels", "vpn-servers", "radius-profiles",
            "device-tags", "dpi-categories", "dpi-applications", "countries",
        ],
        Field(description="Which supporting resource to query"),
    ],
    site: Annotated[str | None, Field(description="Site label or partial hint")] = None,
    limit: Annotated[int | None, Field(description="Max results, applies where the resource supports listing")] = None,
    next_token: Annotated[str | None, Field(description="Pagination token, applies where the resource supports listing")] = None,
) -> str:
    subcommand = SUPPORTING_RESOURCES[resource]
    return await asyncio.to_thread(_run, subcommand, site=site, limit=limit, next_token=next_token)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
