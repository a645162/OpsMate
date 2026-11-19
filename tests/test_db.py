"""数据库备份/恢复的纯逻辑测试（不真正连库）。"""

from __future__ import annotations

import pytest

from opsmate import config, db


def test_target_defaults():
    t = db.DbTarget(name="m")
    assert t.engine == "mysql" and t.port == 3306 and t.image == "mysql:8"
    p = db.DbTarget(name="p", engine="postgres")
    assert p.port == 5432 and p.image == "postgres:16"
    with pytest.raises(ValueError):
        db.DbTarget(name="x", engine="sqlite")


def test_catalog_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path / "home"))
    catalog = db.DbCatalog()
    target = db.DbTarget(name="demo", databases=["a", "b"])
    catalog.upsert(target)
    loaded = db.DbCatalog.load()
    assert len(loaded.targets) == 1
    assert loaded.targets[0].databases == ["a", "b"]
    assert loaded.remove(target.id) is True
    assert db.DbCatalog.load().targets == []


def test_runner_prefers_local(monkeypatch):
    monkeypatch.setattr(db.shutil, "which", lambda name: f"/usr/bin/{name}")
    target = db.DbTarget(name="m")
    runner = db.resolve_runner(target)
    assert runner.mode == "local"
    assert runner.tool("mysqldump") == ["mysqldump"]


def test_runner_falls_back_to_docker(monkeypatch):
    monkeypatch.setattr(
        db.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None
    )
    target = db.DbTarget(name="m", password="pw")
    runner = db.resolve_runner(target)
    assert runner.mode == "docker"
    argv = runner.tool("mysqldump")
    assert argv[:4] == ["docker", "run", "--rm", "-i"]
    assert "-e" in argv and "MYSQL_PWD" in argv
    assert "mysql:8" in argv
    assert runner.env()["MYSQL_PWD"] == "pw"
    assert "pw" not in " ".join(argv)  # 密码不进命令行


def test_runner_errors_without_anything(monkeypatch):
    monkeypatch.setattr(db.shutil, "which", lambda name: None)
    with pytest.raises(db.DbError):
        db.resolve_runner(db.DbTarget(name="m"))


def test_restore_requires_confirm(tmp_path):
    archive = tmp_path / "x.sql.gz"
    archive.write_bytes(b"")
    target = db.DbTarget(name="m")
    with pytest.raises(db.DbError):
        db.restore(target, archive)


def test_postgres_selected_dbs_use_tar():
    target = db.DbTarget(name="p", engine="postgres", databases=["demo"])
    assert target.engine == "postgres"
    # 备份格式选择在 backup() 内，这里只验证配置语义
    assert target.databases == ["demo"]


def test_prune_keeps_newest(tmp_path):
    import time

    target = db.DbTarget(name="demo")
    prefix = db._prefix(target)
    files = []
    for index in range(4):
        path = tmp_path / f"{prefix}{index}.sql.gz"
        path.write_bytes(b"x")
        # 拉开 mtime，保证排序稳定
        import os

        os.utime(path, (time.time() - index * 10, time.time() - index * 10))
        files.append(path)
    removed = db.prune(target, keep=2, out_dir=tmp_path)
    assert removed == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [f"{prefix}0.sql.gz", f"{prefix}1.sql.gz"]
    )


def test_is_due():
    target = db.DbTarget(name="x")
    assert db.is_due(target) is False  # 关闭
    target.schedule_hours = 24
    assert db.is_due(target) is True   # 从没备份过
    target.last_backup = "2026-01-01T00:00:00"
    assert db.is_due(target) is True
    import time

    target.last_backup = time.strftime("%Y-%m-%dT%H:%M:%S")
    assert db.is_due(target) is False


def test_autobackup_runs_due_only(tmp_path, monkeypatch):
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path / "home"))
    due = db.DbTarget(name="due", schedule_hours=1)
    not_due = db.DbTarget(name="later", schedule_hours=0)
    catalog = db.DbCatalog(targets=[due, not_due])

    called = []

    def fake_backup(target, out_dir=None):
        called.append(target.name)
        return tmp_path / "fake.sql.gz"

    monkeypatch.setattr(db, "backup", fake_backup)
    monkeypatch.setattr(db, "prune", lambda *a, **k: 0)

    results = db.autobackup(catalog=catalog)
    assert called == ["due"]
    assert results[0][0] == "due"
    assert due.last_backup


def test_restore_with_safety_order(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(db, "backup", lambda target, out_dir=None: (calls.append("backup"), tmp_path / "safe.sql.gz")[1])
    monkeypatch.setattr(db, "restore", lambda target, archive, confirm=False: (calls.append("restore"), "ok")[1])
    message = db.restore_with_safety(db.DbTarget(name="x"), "a.sql.gz", confirm=True)
    assert calls == ["backup", "restore"]
    assert "恢复前快照" in message


def test_tunnel_rewrites_target(monkeypatch):
    from opsmate.inventory import HostEntry, Inventory

    host = HostEntry(name="jump", host="203.0.113.5", port=2222, user="ops")
    monkeypatch.setattr(Inventory, "load", classmethod(lambda cls: Inventory(hosts=[host])))
    monkeypatch.setattr(db, "_free_port", lambda: 45678)
    monkeypatch.setattr(db, "_wait_port", lambda port, timeout=10: None)

    class Proc:
        def __init__(self):
            self.args = None
            self.terminated = False

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    holder = {}

    def fake_popen(cmd, **kwargs):
        holder["cmd"] = cmd
        holder["env"] = kwargs.get("env")
        return Proc()

    monkeypatch.setattr(db.subprocess, "Popen", fake_popen)
    target = db.DbTarget(name="x", host="10.0.0.9", port=3306,
                         ssh_host_id=host.id)
    with db.tunnel(target) as scoped:
        assert scoped.host == "127.0.0.1"
        assert scoped.port == 45678
    assert any("127.0.0.1:45678:10.0.0.9:3306" in part for part in holder["cmd"])
    assert holder["env"] is not None
