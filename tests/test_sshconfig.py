"""~/.ssh/config 托管块与备份的离线测试。"""

from __future__ import annotations

from opsmate import config, sshconfig
from opsmate.inventory import HostEntry, Inventory


def _inventory() -> Inventory:
    return Inventory(hosts=[
        HostEntry(name="demo", host="203.0.113.10", port=2222, user="deploy",
                  auth="key", key_path="~/.ssh/id_ed25519"),
        HostEntry(name="db", host="203.0.113.11"),
    ])


def test_sync_creates_block_and_preserves_user_text(tmp_path, monkeypatch):
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path / "home"))
    path = tmp_path / "config"
    path.write_text("Host my-own\n  HostName example.com\n", "utf-8")

    backup, text = sshconfig.sync(path, inventory=_inventory())
    assert backup is not None and backup.exists()
    assert "Host my-own" in text
    assert "Host demo" in text and "Host db" in text
    assert "Port 2222" in text
    assert "IdentityFile ~/.ssh/id_ed25519" in text


def test_sync_is_idempotent_and_replaces(tmp_path, monkeypatch):
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path / "home"))
    path = tmp_path / "config"
    inv = _inventory()
    sshconfig.sync(path, inventory=inv)
    _, text1 = sshconfig.sync(path, inventory=inv)
    _, text2 = sshconfig.sync(path, inventory=inv)
    assert text1 == text2
    assert text2.count(sshconfig.MARK_BEGIN) == 1

    smaller = Inventory(hosts=[HostEntry(name="demo", host="203.0.113.99")])
    _, text3 = sshconfig.sync(path, inventory=smaller)
    assert "203.0.113.99" in text3
    assert "Host db" not in text3


def test_write_config_backs_up(tmp_path, monkeypatch):
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path / "home"))
    path = tmp_path / "config"
    path.write_text("old\n", "utf-8")
    sshconfig.write_config("new\n", path)
    assert path.read_text("utf-8") == "new\n"
    backups = list((tmp_path / "home" / "backups").glob("config.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text("utf-8") == "old\n"


def test_parse_hosts():
    text = """
Host a
  HostName 1.2.3.4
  User root
  Port 22
  IdentityFile ~/.ssh/id

Host b c
  HostName 5.6.7.8
"""
    hosts = sshconfig.parse_hosts(text)
    assert [h.alias for h in hosts] == ["a", "b"]
    assert hosts[0].hostname == "1.2.3.4"
    assert hosts[0].user == "root"
    assert hosts[1].alias == "b"
