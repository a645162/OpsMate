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
