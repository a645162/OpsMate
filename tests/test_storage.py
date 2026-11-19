"""对象存储上传的离线测试（不依赖 boto3）。"""

from __future__ import annotations

from pathlib import Path

from opsmate import config, storage


def test_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path / "home"))
    store = storage.StorageStore()
    target = storage.StorageTarget(
        name="minio", provider="s3", bucket="backups",
        endpoint="http://127.0.0.1:9000", access_key="ak", secret_key="sk",
    )
    store.upsert(target)
    loaded = storage.StorageStore.load()
    assert loaded.by_name("minio").bucket == "backups"
    assert loaded.remove(target.id) is True
    assert storage.StorageStore.load().targets == []


def test_oss_endpoint_derived():
    target = storage.StorageTarget(name="oss", provider="oss", region="cn-hangzhou")
    assert target.effective_endpoint() == "https://s3.oss-cn-hangzhou.aliyuncs.com"
    target.endpoint = "https://custom"
    assert target.effective_endpoint() == "https://custom"


def test_object_key():
    target = storage.StorageTarget(name="s", bucket="b", prefix="opsmate")
    key = storage.object_key(target, Path("demo-mysql-20260101.sql.gz"))
    assert key.startswith("opsmate/") and key.endswith("demo-mysql-20260101.sql.gz")
    target.prefix = ""
    key = storage.object_key(target, Path("a.bin"))
    assert not key.startswith("opsmate/") and key.endswith("a.bin")


class FakeClient:
    def __init__(self):
        self.calls = []

    def upload_file(self, filename, bucket, key):
        self.calls.append((filename, bucket, key))


def test_upload_uses_client(tmp_path):
    path = tmp_path / "backup.sql.gz"
    path.write_bytes(b"data")
    client = FakeClient()
    target = storage.StorageTarget(name="s", bucket="b", prefix="p")
    uri = storage.upload(target, path, client=client)
    assert uri.startswith("s3://b/p/")
    assert client.calls[0][1] == "b"


def test_upload_missing_file(tmp_path):
    target = storage.StorageTarget(name="s", bucket="b")
    try:
        storage.upload(target, tmp_path / "nope")
    except FileNotFoundError:
        pass
    else:  # pragma: no cover
        raise AssertionError("应当抛 FileNotFoundError")
