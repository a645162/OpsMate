"""~/.ssh/config：读取、备份、托管块同步、整文件编辑。

只动 ``# >>> opsmate managed >>>`` 到 ``# <<< opsmate managed <<<`` 之间的内容，
块外你手写的配置原样保留；每次写入前都会备份。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from opsmate import config
from opsmate.inventory import HostEntry, Inventory

MARK_BEGIN = "# >>> opsmate managed (auto-generated) >>>"
MARK_END = "# <<< opsmate managed <<<"

_BLOCK_RE = re.compile(
    re.escape(MARK_BEGIN) + r".*?" + re.escape(MARK_END) + r"\n?", re.DOTALL
)


@dataclass
class SshHostBlock:
    alias: str
    hostname: str = ""
    port: int = 0
    user: str = ""
    identity_file: str = ""
    options: list[str] = field(default_factory=list)


def backup_file(path: Path) -> Path | None:
    """把文件复制到 opsmate 的备份目录，返回备份路径（原文件不存在返回 None）。"""
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = config.backups_dir() / f"{path.name}.{stamp}.bak"
    target.write_bytes(path.read_bytes())
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def read_config(path: Path | None = None) -> str:
    path = path or config.ssh_config_path()
    if not path.exists():
        return ""
    return path.read_text("utf-8")


def write_config(text: str, path: Path | None = None, *, backup: bool = True) -> Path:
    path = path or config.ssh_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup:
        backup_file(path)
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, "utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def parse_hosts(text: str) -> list[SshHostBlock]:
    """解析常见的简单 Host 块（够用于展示和查重）。"""
    hosts: list[SshHostBlock] = []
    current: SshHostBlock | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        keyword, _, value = line.partition(" ")
        if not value:
            keyword, _, value = line.partition("=")
        keyword = keyword.strip().lower()
        value = value.strip()
        if keyword == "host":
            if current:
                hosts.append(current)
            aliases = value.split()
            current = SshHostBlock(alias=aliases[0] if aliases else "")
        elif current is None:
            continue
        elif keyword == "hostname":
            current.hostname = value
        elif keyword == "port":
            try:
                current.port = int(value)
            except ValueError:
                pass
        elif keyword == "user":
            current.user = value
        elif keyword == "identityfile":
            current.identity_file = value
        else:
            current.options.append(f"{keyword} {value}")
    if current:
        hosts.append(current)
    return [h for h in hosts if h.alias and "*" not in h.alias and "?" not in h.alias]


def render_block(hosts: list[HostEntry], aliases: dict[str, HostEntry] | None = None) -> str:
    aliases = aliases or Inventory(hosts).aliases()
    lines = [MARK_BEGIN]
    for alias, host in aliases.items():
        lines.append(f"# {host.name} ({host.id})")
        lines.append(f"Host {alias}")
        lines.append(f"  HostName {host.host}")
        if host.port and host.port != 22:
            lines.append(f"  Port {host.port}")
        if host.user:
            lines.append(f"  User {host.user}")
        if host.auth == "key" and host.key_path:
            lines.append(f"  IdentityFile {host.key_path}")
        lines.append("")
    lines.append(MARK_END)
    return "\n".join(lines) + "\n"


def sync(path: Path | None = None, *, inventory: Inventory | None = None) -> tuple[Path | None, str]:
    """把清单写进托管块；返回 (备份路径, 新的文本)。"""
    path = path or config.ssh_config_path()
    inventory = inventory or Inventory.load()
    current = read_config(path)
    block = render_block(inventory.hosts, inventory.aliases())
    if block in current:
        return None, current
    backup = backup_file(path) if path.exists() else None
    if MARK_BEGIN in current and MARK_END in current:
        updated = _BLOCK_RE.sub(block, current)
    else:
        updated = current.rstrip() + ("\n\n" if current.strip() else "") + block
    write_config(updated, path, backup=False)
    return backup, updated


def host_aliases(text: str) -> set[str]:
    return {h.alias for h in parse_hosts(text)}
