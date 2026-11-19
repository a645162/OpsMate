"""主机清单的离线测试。"""

from __future__ import annotations

from opsmate import config
from opsmate.inventory import HostEntry, Inventory


def make_inventory(tmp_path, monkeypatch):
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path / "home"))
    return Inventory()


def test_roundtrip(tmp_path, monkeypatch):
    inv = make_inventory(tmp_path, monkeypatch)
    host = HostEntry(name="demo", host="203.0.113.10", port=2222, user="deploy")
    inv.upsert(host)
    loaded = Inventory.load()
    assert len(loaded.hosts) == 1
    assert loaded.hosts[0].name == "demo"
    assert loaded.hosts[0].port == 2222


def test_upsert_and_remove(tmp_path, monkeypatch):
    inv = make_inventory(tmp_path, monkeypatch)
    host = HostEntry(name="a", host="h")
    inv.upsert(host)
    host.user = "ops"
    inv.upsert(host)
    assert len(inv.hosts) == 1 and inv.hosts[0].user == "ops"
    assert inv.remove(host.id) is True
    assert inv.remove(host.id) is False
    assert Inventory.load().hosts == []


def test_aliases_unique(tmp_path, monkeypatch):
    inv = make_inventory(tmp_path, monkeypatch)
    inv.hosts = [
        HostEntry(name="web 01", host="h1"),
        HostEntry(name="web 01", host="h2"),
        HostEntry(name="db", host="h3"),
    ]
    aliases = inv.aliases()
    assert list(aliases) == ["web-01", "web-01-2", "db"]


def test_import_landui_dedupes(tmp_path, monkeypatch):
    inv = make_inventory(tmp_path, monkeypatch)

    class Server:
        def __init__(self, sid, name, ip):
            self.id, self.name, self.ip = sid, name, ip
            self.os, self.region = "Linux", "西南1区"

    added, updated = inv.import_landui([Server(1, "srv-1", "203.0.113.1")])
    assert (added, updated) == (1, 0)
    added, updated = inv.import_landui([Server(1, "srv-1", "203.0.113.2")])
    assert (added, updated) == (0, 1)
    assert inv.hosts[0].host == "203.0.113.2"
    assert inv.hosts[0].source == "landui:1"
    assert inv.hosts[0].tags == ["西南1区"]


def test_target_respects_auth():
    host = HostEntry(name="a", host="h", auth="key", key_path="~/.ssh/id", password="p")
    target = host.target()
    assert target.key_path == "~/.ssh/id"
    assert target.password == ""

    host.auth = "password"
    assert host.target().password == "p"
