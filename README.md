# UniFi Network Advisor — OpenClaw Skill & MCP Server

A read-only tool that lets your AI assistant query your UniFi network and give advice on devices, clients, firewall configuration, VPN tunnels, ISP performance, and more — GET-only, no writes of any kind.

Ships two ways, both backed by the same `scripts/unifi.py` logic:

- **[OpenClaw](https://openclaw.ai) skill** (`SKILL.md` + `scripts/unifi.py`) — OpenClaw runs the script directly as a subprocess.
- **MCP server** (`unifi-mcp`) — a standard stdio MCP server for Claude Code, OpenClaw, or any other MCP client. See [MCP Server](#mcp-server) below.

The two are independent — install either one, or both side by side.

## What it does

Covers two official UniFi API surfaces:

**Site Manager API** (cloud, cross-site):
- Lists all consoles, sites, and devices on your UI account
- ISP performance metrics and SD-WAN configurations

**Network Integration API** (proxied via api.ui.com):
- Devices, clients, networks, WiFi SSIDs
- Firewall zones, policies, ACL rules, DNS policies
- VPN tunnels and servers, WAN interfaces
- Hotspot vouchers, RADIUS profiles, device tags
- DPI categories and applications

**All queries are GET-only. No writes, no mutations.**

## Structure

```
open_claw-unifi-skill/
├── SKILL.md            # Skill definition and agent instructions (OpenClaw)
├── scripts/
│   └── unifi.py        # Query script — single source of truth for auth/HTTP/site logic
├── pyproject.toml       # unifi-mcp package metadata
└── src/unifi_mcp/
    ├── cli.py          # Subprocess adapter over scripts/unifi.py
    └── server.py       # MCP server: 19 tools wrapping the script's 44 subcommands
```

## Requirements

- A UI account at [unifi.ui.com](https://unifi.ui.com)
- Python 3 on the host running OpenClaw

## Setup

### 1. Generate an API key

Sign in at [unifi.ui.com](https://unifi.ui.com) → **Settings → API Keys** → create a new key.
The same key works for both the Site Manager and Network Integration APIs.

### 2. Set the API key on your OpenClaw host

```bash
export UNIFI_API_KEY=your-api-key-here
```

That's it. Host and site IDs are discovered automatically from your UI account on every invocation (cached for 15 minutes). No other env vars needed.

### 3. Install the skill

```bash
# From ClawHub (once published):
openclaw skills install unifi-advisor

# Or locally:
openclaw skills install /path/to/open_claw-unifi-skill
```

## Usage

Ask your assistant naturally:

- *"Are all my UniFi devices online?"*
- *"Which clients are connected right now?"*
- *"Show me my firewall policies and check for anything too broad"*
- *"Is my VPN tunnel up?"*
- *"How is my ISP performing?"*
- *"What networks and VLANs do I have configured?"*

If you have multiple sites, specify which one: *"Check the firewall on my HQ site"* or *"What clients are on the branch office network?"*

Or call directly for testing:

```bash
python3 scripts/unifi.py list                         # all available subcommands
python3 scripts/unifi.py library                      # show discovered sites
python3 scripts/unifi.py devices --site hq            # adopted devices on a specific site
python3 scripts/unifi.py clients --limit 20           # connected clients
python3 scripts/unifi.py firewall-policies            # firewall policies
python3 scripts/unifi.py isp-metrics --metric-type 1h # ISP performance
```

## MCP Server

The same `scripts/unifi.py` logic is also available as an MCP server (`unifi-mcp`), for Claude Code or any other MCP client — not just OpenClaw. It shells out to `scripts/unifi.py` under the hood, so it shares the same `UNIFI_API_KEY` and the same site-library cache (`~/.openclaw/unifi-skill.json`).

Rather than exposing all 44 subcommands as separate MCP tools (which would bloat every session's tool list), the script's subcommands are grouped into 19 tools that follow UniFi's own [developer.ui.com](https://developer.ui.com) API categories — e.g. all firewall endpoints (`firewall-zones`/`firewall-zone`/`firewall-policies`/`firewall-policy`/`firewall-ordering`) become a single `unifi_net_firewall` tool with a `resource` parameter, list-vs-details is controlled by an optional `id` parameter, and so on.

Since v0.2.0 the server also post-processes responses for LLM consumption:

- **Compact-by-default Site Manager output** — raw console objects are ~13 KB each; `unifi_sm_hosts`/`unifi_sm_devices` return curated summaries (~30x smaller) with a `full=true` escape hatch for the raw objects.
- **`query=` search** on `unifi_net_clients`, `unifi_net_devices`, and `unifi_sm_devices` — case-insensitive substring match on name/IP/MAC/model, filtered server-side instead of dumping full lists into context.
- **`unifi_health_summary`** — one call composing console states, WAN/internet-issue flags, per-site device totals, offline devices, and pending firmware updates.
- Compact JSON everywhere (no indentation) and a validated `unifi_sm_isp_metrics` parameter combo (the API only accepts `1h`+`7d/30d` or `5m`+`24h`).

| MCP tool | Covers |
|---|---|
| `unifi_library` | `library` |
| `unifi_sm_hosts` | `hosts`, `host` |
| `unifi_sm_sites` | `sites` |
| `unifi_sm_devices` | `cloud-devices` |
| `unifi_sm_isp_metrics` | `isp-metrics` |
| `unifi_sm_sdwan` | `sdwan`, `sdwan-config`, `sdwan-status` |
| `unifi_net_info` | `info` |
| `unifi_net_sites` | `net-sites` |
| `unifi_net_devices` | `devices`, `device`, `device-stats`, `devices-pending` |
| `unifi_net_clients` | `clients`, `client` |
| `unifi_net_networks` | `networks`, `network`, `network-refs` |
| `unifi_net_wifi` | `wifi`, `wifi-details` |
| `unifi_net_hotspot` | `vouchers`, `voucher` |
| `unifi_net_firewall` | `firewall-zones`, `firewall-zone`, `firewall-policies`, `firewall-policy`, `firewall-ordering` |
| `unifi_net_acl` | `acl-rules`, `acl-rule`, `acl-ordering` |
| `unifi_net_dns` | `dns-policies`, `dns-policy` |
| `unifi_net_traffic` | `traffic-lists`, `traffic-list` |
| `unifi_net_supporting` | `wans`, `vpn-tunnels`, `vpn-servers`, `radius`, `device-tags`, `dpi-categories`, `dpi-applications`, `countries` |

UniFi's docs also list a "Switching" API category — there's no corresponding subcommand in `scripts/unifi.py` yet, so there's no `unifi_net_switching` tool either. Would need new subcommands added to the script first.

### Sanity check

```bash
cd open_claw-unifi-skill
uv run unifi-mcp   # starts on stdio, Ctrl+C to stop — confirms install + UNIFI_API_KEY are good
```

### Claude Code

```bash
# Reads UNIFI_API_KEY from your shell env via ${} expansion — never stored in plaintext:
claude mcp add unifi --scope user -e UNIFI_API_KEY='${UNIFI_API_KEY}' -- uvx --from /path/to/open_claw-unifi-skill unifi-mcp
```

### OpenClaw

Register it as an MCP server the same way as any other (see your OpenClaw `mcp.servers` config):

```json
"unifi": {
  "command": "uvx",
  "args": ["--from", "/path/to/open_claw-unifi-skill", "unifi-mcp"],
  "env": { "UNIFI_API_KEY": "your-api-key-here" }
}
```

Then add `unifi` to whichever agent(s) should have UniFi access. This works alongside the `unifi-advisor` skill above — you don't need to remove one to use the other.
```

## All subcommands

### Site Manager (cloud)

| Subcommand | Description |
|---|---|
| `library` | Show discovered sites with labels, state, and API capabilities |
| `hosts` | All UniFi OS consoles on the UI account |
| `host` | Console details (`--id` required) |
| `sites` | All sites across all consoles |
| `cloud-devices` | All devices across all sites |
| `isp-metrics` | ISP performance metrics (`--metric-type 1h\|5m`) |
| `sdwan` | SD-WAN configurations |
| `sdwan-config` | SD-WAN config details (`--id` required) |
| `sdwan-status` | SD-WAN deployment status (`--id` required) |

### Network Integration (per-site, use `--site <label>`)

| Subcommand | Description |
|---|---|
| `info` | Controller version and capabilities |
| `net-sites` | Sites managed by this console |
| `devices` | Adopted devices overview |
| `device` | Device details (`--id` required) |
| `device-stats` | Latest device statistics (`--id` required) |
| `devices-pending` | Devices pending adoption |
| `clients` | Connected clients overview |
| `client` | Client details (`--id` required) |
| `networks` | Networks overview |
| `network` | Network details (`--id` required) |
| `network-refs` | References to a network — devices, clients, WiFi (`--id` required; use a network with active traffic) |
| `wifi` | WiFi broadcast (SSID) overview |
| `wifi-details` | WiFi broadcast details (`--id` required) |
| `firewall-zones` | Firewall zones |
| `firewall-zone` | Firewall zone details (`--id` required) |
| `firewall-policies` | Firewall policies |
| `firewall-policy` | Firewall policy details (`--id` required) |
| `firewall-ordering` | Firewall policy ordering between two zones (`--from-zone` and `--to-zone` required) |
| `acl-rules` | ACL rules |
| `acl-rule` | ACL rule details (`--id` required) |
| `acl-ordering` | ACL rule ordering |
| `dns-policies` | DNS policies |
| `dns-policy` | DNS policy details (`--id` required) |
| `traffic-lists` | Traffic matching lists |
| `traffic-list` | Traffic matching list details (`--id` required) |
| `vouchers` | Hotspot vouchers |
| `voucher` | Voucher details (`--id` required) |
| `wans` | WAN interfaces overview |
| `vpn-tunnels` | Site-to-site VPN tunnels |
| `vpn-servers` | VPN server configurations |
| `radius` | RADIUS profiles |
| `device-tags` | Device tags |
| `dpi-categories` | DPI application categories |
| `dpi-applications` | DPI applications |
| `countries` | Countries list (for geo-IP firewall rules) |

## License

MIT-0 — do whatever you want with it, no attribution required.
