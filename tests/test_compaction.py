"""Offline tests for the server-side compaction and filtering helpers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unifi_mcp.server import (  # noqa: E402
    compact_host,
    compact_sm_device,
    compact_sm_device_groups,
    filter_by_query,
)


def make_host():
    return {
        "id": "H1",
        "type": "console",
        "ipAddress": "203.0.113.10",
        "isBlocked": False,
        "lastConnectionStateChange": "2026-01-01T00:00:00Z",
        "latestBackupTime": "2026-01-02T00:00:00Z",
        "userData": {"huge": "blob" * 1000},
        "reportedState": {
            "name": "Console-1",
            "hostname": "console-1",
            "state": "connected",
            "version": "5.1.117",
            "releaseChannel": "release",
            "internetIssues5min": {"periods": []},
            "firmwareUpdate": {"latestAvailableVersion": None},
            "wans": [{"name": "wan1", "type": "dhcp", "ipv4": "203.0.113.10", "status": "up", "noise": "x"}],
            "apps": ["giant", "irrelevant", "list"],
            "controllers": [{"big": "blob"}],
        },
    }


def test_compact_host_keeps_essentials_and_drops_blobs():
    c = compact_host(make_host())
    assert c["name"] == "Console-1"
    assert c["state"] == "connected"
    assert c["version"] == "5.1.117"
    assert c["internetIssues5min"] is False
    assert c["firmwareUpdateAvailable"] is False
    assert c["wans"] == [{"name": "wan1", "type": "dhcp", "ipv4": "203.0.113.10", "status": "up"}]
    assert "userData" not in c and "apps" not in str(c)


def test_compact_host_flags_issues():
    h = make_host()
    h["reportedState"]["internetIssues5min"] = {"periods": [{"index": 1}]}
    h["reportedState"]["firmwareUpdate"] = {"latestAvailableVersion": "5.2.0"}
    c = compact_host(h)
    assert c["internetIssues5min"] is True
    assert c["firmwareUpdateAvailable"] is True


def test_compact_host_much_smaller():
    import json
    h = make_host()
    assert len(json.dumps(compact_host(h))) < len(json.dumps(h)) / 5


def test_compact_sm_device_drops_uidb_and_empties():
    d = {
        "name": "AP-1", "model": "U7 Pro", "ip": "192.0.2.5", "mac": "AA:BB",
        "status": "online", "version": "8.6.11", "firmwareStatus": "upToDate",
        "updateAvailable": "", "isConsole": False, "productLine": "network",
        "uidb": {"guid": "xxx", "images": {"a": "b"}}, "note": "",
    }
    c = compact_sm_device(d)
    assert "uidb" not in c and "note" not in c and "updateAvailable" not in c
    assert c["name"] == "AP-1" and c["status"] == "online"
    assert c["isConsole"] is False  # falsy but meaningful — must be kept


def test_compact_sm_device_groups_shape():
    groups = [{"hostId": "H", "hostName": "Site", "devices": [{"name": "X", "uidb": {}}], "extra": 1}]
    out = compact_sm_device_groups(groups)
    assert out == [{"hostName": "Site", "hostId": "H", "devices": [{"name": "X"}]}]


def test_filter_by_query_matches_common_fields():
    items = [
        {"name": "Garage door", "ipAddress": "192.0.2.17", "macAddress": "8c:aa:b5:5d:94:ec"},
        {"name": "Printer", "ipAddress": "192.0.2.30", "macAddress": "de:ad:be:ef:00:01"},
    ]
    assert len(filter_by_query(items, "garage")) == 1
    assert len(filter_by_query(items, "192.0.2")) == 2
    assert len(filter_by_query(items, "DE:AD")) == 1
    assert filter_by_query(items, "nothing") == []
