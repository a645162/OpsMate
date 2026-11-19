#!/usr/bin/env python3
"""用 Docker 起真实的 MySQL/PostgreSQL，验证「全库备份 → 破坏 → 覆盖恢复」。

    uv run python scripts/db_docker_test.py

需要本机有 docker，能拉 mysql:8 / postgres:16（首次会慢）。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from opsmate import db  # noqa: E402

MYSQL_PORT = 33306
PG_PORT = 35432
PASSWORD = "OpsMate_Test_123"


def run(cmd: list[str], *, check: bool = True, stdin: str | None = None) -> str:
    proc = subprocess.run(
        cmd, input=stdin, capture_output=True, text=True, check=False
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{proc.stderr.strip()}")
    return (proc.stdout or "").strip()


def wait_for(predicate, timeout: float, label: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(1)
    raise TimeoutError(f"{label} 超时（{timeout}s）")


def cleanup(*names: str) -> None:
    for name in names:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


def test_mysql(tmp: Path) -> None:
    name = "opsmate-mysql-test"
    cleanup(name)
    print("→ 启动 MySQL 容器…")
    run([
        "docker", "run", "-d", "--name", name,
        "-e", f"MYSQL_ROOT_PASSWORD={PASSWORD}",
        "-e", "MYSQL_DATABASE=demo",
        "-p", f"{MYSQL_PORT}:3306", "mysql:8",
    ])
    def mysql_ready() -> bool:
        return subprocess.run(
            ["docker", "exec", name, "mysql", "-uroot", f"-p{PASSWORD}",
             "-N", "-B", "-e", "SELECT 1;"],
            capture_output=True,
        ).returncode == 0

    wait_for(mysql_ready, 120, "MySQL 就绪")
    time.sleep(2)
    exec_sql = lambda sql: run([  # noqa: E731
        "docker", "exec", "-i", name, "mysql", "-uroot", f"-p{PASSWORD}", "-e", sql,
    ])
    exec_sql(
        "CREATE TABLE demo.t (id INT PRIMARY KEY, v VARCHAR(50));"
        "INSERT INTO demo.t VALUES (1,'alpha'),(2,'beta');"
    )
    before = exec_sql("SELECT COUNT(*) FROM demo.t;")

    target = db.DbTarget(
        name="mysql-docker", engine="mysql", host="127.0.0.1", port=MYSQL_PORT,
        user="root", password=PASSWORD, databases=["demo"], client_mode="auto",
    )
    print("→ 备份…")
    archive = db.backup(target, out_dir=tmp)
    print(f"   备份文件 {archive.name}（{archive.stat().st_size} 字节）")

    print("→ 删除整个库，模拟事故")
    exec_sql("DROP DATABASE demo;")

    print("→ 覆盖恢复…")
    print("  ", db.restore(target, archive, confirm=True))
    after = exec_sql("SELECT COUNT(*) FROM demo.t;")
    print(f"   恢复前行数输出 {before!r}，恢复后 {after!r}")
    assert "2" in after, after
    print("✅ MySQL 备份/恢复通过")


def test_postgres(tmp: Path) -> None:
    name = "opsmate-pg-test"
    cleanup(name)
    print("→ 启动 PostgreSQL 容器…")
    run([
        "docker", "run", "-d", "--name", name,
        "-e", f"POSTGRES_PASSWORD={PASSWORD}",
        "-e", "POSTGRES_DB=demo",
        "-p", f"{PG_PORT}:5432", "postgres:16",
    ])
    wait_for(
        lambda: subprocess.run(
            ["docker", "exec", name, "pg_isready", "-U", "postgres"],
            capture_output=True,
        ).returncode == 0,
        90, "PostgreSQL 就绪",
    )
    time.sleep(2)
    psql = lambda sql: run([  # noqa: E731
        "docker", "exec", "-i", name, "psql", "-U", "postgres", "-d", "demo",
        "-v", "ON_ERROR_STOP=1", "-c", sql,
    ])
    psql("CREATE TABLE t (id INT PRIMARY KEY, v TEXT);")
    psql("INSERT INTO t VALUES (1,'alpha'),(2,'beta');")

    target = db.DbTarget(
        name="pg-docker", engine="postgres", host="127.0.0.1", port=PG_PORT,
        user="postgres", password=PASSWORD, databases=["demo"], client_mode="auto",
    )
    print("→ 备份…")
    archive = db.backup(target, out_dir=tmp)
    print(f"   备份文件 {archive.name}（{archive.stat().st_size} 字节）")

    print("→ 删表模拟事故")
    psql("DROP TABLE t;")

    print("→ 覆盖恢复…")
    print("  ", db.restore(target, archive, confirm=True))
    count = psql("SELECT COUNT(*) FROM t;")
    print("   恢复后：", " ".join(count.split()))
    assert "2" in count, count
    print("✅ PostgreSQL 备份/恢复通过")


def main() -> int:
    tmp = Path("/tmp/opencode/db-test")
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        test_mysql(tmp)
        test_postgres(tmp)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 失败：{type(exc).__name__}: {exc}")
        return 1
    finally:
        cleanup("opsmate-mysql-test", "opsmate-pg-test")
        print("→ 已清理测试容器")
    print("\n🎉 MySQL 与 PostgreSQL 全流程（备份→破坏→覆盖恢复）均通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
