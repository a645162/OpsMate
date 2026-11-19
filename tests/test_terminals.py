"""终端配置导出的离线测试。"""

from __future__ import annotations

import json

import yaml

from opsmate import config, terminals
from opsmate.inventory import HostEntry, Inventory


def _inventory() -> Inventory:
    return Inventory(hosts=[
        HostEntry(name="demo", host="203.0.113.10", port=2222, user="deploy",
                  auth="key", key_path="~/.ssh/id_ed25519"),
        HostEntry(name="db", host="203.0.113.11"),
    ])


def test_tabby_snippet():
    snippet = terminals.tabby_snippet(_inventory())
    profiles = snippet["profiles"]
    assert len(profiles) == 2
    assert profiles[0]["type"] == "ssh"
    assert profiles[0]["options"]["auth"] == "privateKey"
    assert profiles[1]["options"]["auth"] == "auto"
    # 绝不写密码
    assert "password" not in json.dumps(snippet)


def test_apply_tabby_merges_and_backs_up(tmp_path, monkeypatch):
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path / "home"))
    target = tmp_path / "config.yaml"
    target.write_text(
        yaml.safe_dump({"profiles": [{"name": "Existing"}], "version": 7}),
        "utf-8",
    )
    monkeypatch.setattr(terminals.config, "first_existing", lambda paths: target)

    backup, added, path = terminals.apply_tabby(_inventory())
    assert added == 2 and path == target
    assert backup is not None and backup.exists()
    data = yaml.safe_load(target.read_text("utf-8"))
    names = [p["name"] for p in data["profiles"]]
    assert names[0] == "Existing"
    assert "OpsMate / demo" in names
    assert data["version"] == 7

    backup2, added2, _ = terminals.apply_tabby(_inventory())
    assert added2 == 0 and backup2 is None


def test_windows_terminal_snippet_and_apply(tmp_path, monkeypatch):
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path / "home"))
    snippet = terminals.wt_snippet(_inventory())
    assert snippet["profiles"]["list"][0]["commandline"] == "ssh demo"

    target = tmp_path / "settings.json"
    target.write_text(json.dumps({"profiles": {"list": [{"name": "Existing"}]}}), "utf-8")
    backup, added, path = terminals.apply_windows_terminal(_inventory(), target)
    assert added == 2 and path == target and backup.exists()
    data = json.loads(target.read_text("utf-8"))
    names = [p["name"] for p in data["profiles"]["list"]]
    assert "Existing" in names and "OpsMate / demo" in names
    assert all(p.get("guid", "").startswith("{") for p in data["profiles"]["list"][1:])
