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


class RemoteClient(FakeClient):
    def __init__(self, contents, download_bytes=b""):
        super().__init__()
        self.contents = contents
        self.download_bytes = download_bytes
        self.deleted = []
        self.calls = []

    def list_objects_v2(self, **kwargs):
        return {"Contents": self.contents, "IsTruncated": False}

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        self.calls.append(("delete", Bucket, Key))

    def download_file(self, bucket, key, filename):
        self.calls.append(("download", bucket, key, filename))
        Path(filename).write_bytes(self.download_bytes)


def _obj(key, ts):
    import datetime as dt

    return {"Key": key, "LastModified": dt.datetime(2026, 1, ts)}


def test_prune_remote_keeps_newest():
    client = RemoteClient([
        _obj("p/a-1.sql.gz", 1),
        _obj("p/a-2.sql.gz", 2),
        _obj("p/a-3.sql.gz", 3),
        _obj("p/other.sql.gz", 4),
    ])
    target = storage.StorageTarget(name="s", bucket="b")
    removed = storage.prune_remote(
        target, prefix="p/", match="a-", keep=2, client=client
    )
    assert removed == ["p/a-1.sql.gz"]
    assert client.deleted == ["p/a-1.sql.gz"]


def test_prune_remote_dry_run():
    client = RemoteClient([_obj("p/a-1.sql.gz", 1), _obj("p/a-2.sql.gz", 2)])
    target = storage.StorageTarget(name="s", bucket="b")
    removed = storage.prune_remote(
        target, prefix="p/", keep=1, dry_run=True, client=client
    )
    assert removed == ["p/a-1.sql.gz"]
    assert client.deleted == []


def test_verify_remote_ok(tmp_path):
    local = tmp_path / "x.bin"
    local.write_bytes(b"hello")
    client = RemoteClient([], download_bytes=b"hello")
    target = storage.StorageTarget(name="s", bucket="b")
    assert storage.verify_remote(target, local, "k", client=client)


def test_verify_remote_mismatch(tmp_path):
    local = tmp_path / "x.bin"
    local.write_bytes(b"hello")
    client = RemoteClient([], download_bytes=b"tampered")
    target = storage.StorageTarget(name="s", bucket="b")
    try:
        storage.verify_remote(target, local, "k", client=client)
    except storage.StorageError as exc:
        assert "SHA-256" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当校验失败")


def test_parse_uri():
    assert storage.parse_uri("s3://b/k/x.bin") == ("b", "k/x.bin")
    try:
        storage.parse_uri("http://x")
    except storage.StorageError:
        pass
    else:  # pragma: no cover
        raise AssertionError("应当抛错")
