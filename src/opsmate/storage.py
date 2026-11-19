"""把备份传到对象存储：AWS S3 / MinIO / 阿里云 OSS（S3 兼容）。

boto3 是可选依赖：``uv sync --extra cloud``。
凭据存 ``~/.config/opsmate/storage.json``（0600）。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from opsmate import config


class StorageError(RuntimeError):
    pass


def new_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class StorageTarget:
    name: str
    provider: str = "s3"          # s3 | oss
    bucket: str = ""
    region: str = ""
    endpoint: str = ""            # 自建 MinIO / OSS 时需要
    access_key: str = ""
    secret_key: str = ""
    prefix: str = "opsmate"
    id: str = field(default_factory=new_id)

    def effective_endpoint(self) -> str:
        if self.endpoint:
            return self.endpoint
        if self.provider == "oss" and self.region:
            return f"https://s3.oss-{self.region}.aliyuncs.com"
        return ""

    @property
    def label(self) -> str:
        return f"{self.name}（{self.provider}://{self.bucket}）"


@dataclass
class StorageStore:
    targets: list[StorageTarget] = field(default_factory=list)

    @classmethod
    def load(cls) -> StorageStore:
        raw = config.read_json(config.storage_path(), {"targets": []})
        items = raw.get("targets", []) if isinstance(raw, dict) else []
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            fields = {k: v for k, v in item.items() if k in StorageTarget.__dataclass_fields__}
            out.append(StorageTarget(**fields))
        return cls(targets=out)

    def save(self) -> None:
        config.write_private(
            config.storage_path(),
            {"version": 1, "targets": [asdict(t) for t in self.targets]},
        )

    def by_id(self, target_id: str) -> StorageTarget | None:
        return next((t for t in self.targets if t.id == target_id), None)

    def by_name(self, name: str) -> StorageTarget | None:
        return next((t for t in self.targets if t.name == name), None)

    def upsert(self, target: StorageTarget) -> None:
        for index, existing in enumerate(self.targets):
            if existing.id == target.id:
                self.targets[index] = target
                break
        else:
            self.targets.append(target)
        self.save()

    def remove(self, target_id: str) -> bool:
        before = len(self.targets)
        self.targets = [t for t in self.targets if t.id != target_id]
        if len(self.targets) != before:
            self.save()
            return True
        return False


def make_client(target: StorageTarget, client_factory: Any | None = None):
    if client_factory is not None:
        return client_factory(target)
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - 可选依赖
        raise StorageError(
            "需要 boto3：uv sync --extra cloud（或 pip install boto3）"
        ) from exc
    if not target.bucket:
        raise StorageError("没有配置 bucket")
    return boto3.client(
        "s3",
        endpoint_url=target.effective_endpoint() or None,
        region_name=target.region or None,
        aws_access_key_id=target.access_key or None,
        aws_secret_access_key=target.secret_key or None,
    )


def object_key(target: StorageTarget, path: Path) -> str:
    stamp = time.strftime("%Y/%m")
    prefix = target.prefix.strip("/")
    return f"{prefix}/{stamp}/{path.name}" if prefix else f"{stamp}/{path.name}"


def upload(
    target: StorageTarget,
    path: str | Path,
    *,
    key: str | None = None,
    client: Any | None = None,
    client_factory: Any | None = None,
) -> str:
    """上传单个文件，返回 ``s3://bucket/key``。"""
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(path)
    if not target.bucket:
        raise StorageError("没有配置 bucket")
    key = key or object_key(target, path)
    s3 = client or make_client(target, client_factory)
    try:
        s3.upload_file(str(path), target.bucket, key)
    except Exception as exc:
        raise StorageError(f"上传失败：{type(exc).__name__}: {exc}") from exc
    return f"s3://{target.bucket}/{key}"
