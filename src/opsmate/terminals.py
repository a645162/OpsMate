"""把主机清单导出到终端客户端：Tabby、Windows Terminal。

约定：只写「连接信息」，不把密码写进别人的配置文件。
Tabby 用 agent/私钥，Windows Terminal 走 ``ssh <alias>``（依赖 ~/.ssh/config）。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import yaml

from opsmate import config
from opsmate.inventory import HostEntry, Inventory

PREFIX = "OpsMate / "


def _tabby_profile(alias: str, host: HostEntry) -> dict[str, Any]:
    options: dict[str, Any] = {
        "host": host.host,
        "port": host.port,
        "user": host.user,
        "auth": "auto",
    }
    if host.auth == "key" and host.key_path:
        options["auth"] = "privateKey"
        options["privateKeys"] = [host.key_path]
    return {
        "id": uuid.uuid4().hex,
        "type": "ssh",
        "name": f"{PREFIX}{host.name}",
        "options": options,
        "color": None,
        "icon": None,
    }


def tabby_snippet(inventory: Inventory) -> dict[str, Any]:
    aliases = inventory.aliases()
    return {"profiles": [_tabby_profile(alias, host) for alias, host in aliases.items()]}


def export_tabby(inventory: Inventory | None = None) -> Path:
    inventory = inventory or Inventory.load()
    path = config.exports_dir() / "tabby-profiles.yaml"
    path.write_text(
        yaml.safe_dump(tabby_snippet(inventory), sort_keys=False, allow_unicode=True),
        "utf-8",
    )
    return path


def apply_tabby(inventory: Inventory | None = None) -> tuple[Path | None, int, Path]:
    """把配置合并进 Tabby 的 config.yaml。

    没有现成配置就不动它（避免生成一个残缺配置），只导出片段。
    返回 (备份路径, 新增条数, 实际操作的文件)。
    """
    inventory = inventory or Inventory.load()
    target = config.first_existing(config.tabby_config_candidates())
    if target is None:
        snippet = export_tabby(inventory)
        return None, 0, snippet

    raw = yaml.safe_load(target.read_text("utf-8")) or {}
    profiles = raw.setdefault("profiles", [])
    existing = {p.get("name") for p in profiles if isinstance(p, dict)}
    added = 0
    for alias, host in inventory.aliases().items():
        name = f"{PREFIX}{host.name}"
        if name in existing:
            continue
        profiles.append(_tabby_profile(alias, host))
        added += 1

    if added:
        backup = config.backups_dir() / f"tabby-config.{time_stamp()}.yaml.bak"
        backup.write_bytes(target.read_bytes())
        target.write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), "utf-8"
        )
        return backup, added, target
    return None, 0, target


def _wt_profile(alias: str, host: HostEntry) -> dict[str, Any]:
    return {
        "guid": "{" + str(uuid.uuid4()) + "}",
        "name": f"{PREFIX}{host.name}",
        "commandline": f"ssh {alias}",
        "hidden": False,
        "icon": None,
        "startingDirectory": None,
    }


def wt_snippet(inventory: Inventory) -> dict[str, Any]:
    aliases = inventory.aliases()
    return {
        "profiles": {
            "list": [_wt_profile(alias, host) for alias, host in aliases.items()]
        }
    }


def export_windows_terminal(inventory: Inventory | None = None) -> Path:
    inventory = inventory or Inventory.load()
    path = config.exports_dir() / "windows-terminal-profiles.json"
    path.write_text(
        json.dumps(wt_snippet(inventory), ensure_ascii=False, indent=2), "utf-8"
    )
    return path


def apply_windows_terminal(
    inventory: Inventory | None = None, path: Path | None = None
) -> tuple[Path | None, int, Path]:
    inventory = inventory or Inventory.load()
    target = path or config.first_existing(config.windows_terminal_candidates())
    if target is None:
        snippet = export_windows_terminal(inventory)
        return None, 0, snippet

    raw = json.loads(target.read_text("utf-8-sig"))
    profiles = raw.setdefault("profiles", {})
    entries = profiles.setdefault("list", [])
    existing = {entry.get("name") for entry in entries if isinstance(entry, dict)}
    added = 0
    for alias, host in inventory.aliases().items():
        name = f"{PREFIX}{host.name}"
        if name in existing:
            continue
        entries.append(_wt_profile(alias, host))
        added += 1

    if added:
        backup = config.backups_dir() / f"windows-terminal-settings.{time_stamp()}.json.bak"
        backup.write_bytes(target.read_bytes())
        target.write_text(json.dumps(raw, ensure_ascii=False, indent=4), "utf-8")
        return backup, added, target
    return None, 0, target


def time_stamp() -> str:
    import time

    return time.strftime("%Y%m%d-%H%M%S")
