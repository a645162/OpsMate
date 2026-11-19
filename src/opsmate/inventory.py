"""服务器清单：不依赖云厂商的本地主机列表。

存 ``~/.config/opsmate/hosts.json``（0600）。密码可选，建议优先用密钥/Agent。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from landui_sdk.ssh import SshTarget

from opsmate import config

AUTH_MODES = ("agent", "password", "key")


def new_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class HostEntry:
    name: str
    host: str
    port: int = 22
    user: str = "root"
    auth: str = "agent"
    key_path: str = ""
    key_passphrase: str = ""
    password: str = ""
    tags: list[str] = field(default_factory=list)
    note: str = ""
    group: str = ""
    source: str = "manual"        # manual | landui:<id>
    id: str = field(default_factory=new_id)

    @property
    def label(self) -> str:
        return f"{self.name} · {self.user}@{self.host}:{self.port}"

    def target(self) -> SshTarget:
        return SshTarget(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password if self.auth == "password" else "",
            key_path=self.key_path if self.auth == "key" else "",
            key_passphrase=self.key_passphrase if self.auth == "key" else "",
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HostEntry:
        fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        fields["port"] = int(fields.get("port") or 22)
        fields.setdefault("tags", [])
        entry = cls(**fields)
        if entry.auth not in AUTH_MODES:
            entry.auth = "agent"
        return entry


def _slug(text: str) -> str:
    """把名字变成 ssh config 里可用的 alias。"""
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-").lower()
    return text or "host"


def unique_alias(name: str, used: set[str]) -> str:
    base = _slug(name)
    alias = base
    index = 2
    while alias in used:
        alias = f"{base}-{index}"
        index += 1
    used.add(alias)
    return alias


@dataclass
class Inventory:
    hosts: list[HostEntry] = field(default_factory=list)

    # ------------------------------------------------------------------ io
    @classmethod
    def load(cls) -> Inventory:
        raw = config.read_json(config.hosts_path(), {"hosts": []})
        items = raw.get("hosts", []) if isinstance(raw, dict) else []
        hosts = [HostEntry.from_dict(item) for item in items if isinstance(item, dict)]
        return cls(hosts=hosts)

    def save(self) -> None:
        config.write_private(
            config.hosts_path(), {"version": 1, "hosts": [h.to_dict() for h in self.hosts]}
        )

    # -------------------------------------------------------------- 查询
    def by_id(self, host_id: str) -> HostEntry | None:
        return next((h for h in self.hosts if h.id == host_id), None)

    def by_name(self, name: str) -> HostEntry | None:
        return next((h for h in self.hosts if h.name == name), None)

    def find(self, text: str) -> list[HostEntry]:
        needle = text.lower()
        return [
            h for h in self.hosts
            if needle in h.name.lower()
            or needle in h.host.lower()
            or needle in h.user.lower()
            or any(needle in t.lower() for t in h.tags)
        ]

    # -------------------------------------------------------------- 修改
    def upsert(self, host: HostEntry) -> HostEntry:
        for index, existing in enumerate(self.hosts):
            if existing.id == host.id:
                self.hosts[index] = host
                break
        else:
            self.hosts.append(host)
        self.save()
        return host

    def remove(self, host_id: str) -> bool:
        before = len(self.hosts)
        self.hosts = [h for h in self.hosts if h.id != host_id]
        if len(self.hosts) != before:
            self.save()
            return True
        return False

    def aliases(self) -> dict[str, HostEntry]:
        used: set[str] = set()
        out: dict[str, HostEntry] = {}
        for host in self.hosts:
            out[unique_alias(host.name, used)] = host
        return out

    # -------------------------------------------------------------- 导入
    def import_landui(self, servers: Iterable[Any]) -> tuple[int, int]:
        """把蓝队云实例并进来：返回 (新增, 更新)。

        以 ``source=landui:<id>`` 去重，重复导入只更新 IP 等信息，不会覆盖
        你手工改过的端口/账号（端口仅在新建时采用云端值）。
        """
        added = updated = 0
        for server in servers:
            source = f"landui:{server.id}"
            existing = next((h for h in self.hosts if h.source == source), None)
            if existing:
                existing.host = server.ip or existing.host
                existing.note = existing.note or server.os
                updated += 1
            else:
                self.hosts.append(
                    HostEntry(
                        name=server.name,
                        host=server.ip,
                        port=22,
                        user="root",
                        auth="agent",
                        tags=[server.region] if getattr(server, "region", "") else [],
                        note=getattr(server, "os", ""),
                        source=source,
                    )
                )
                added += 1
        if added or updated:
            self.save()
        return added, updated
