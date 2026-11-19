"""MySQL / PostgreSQL 全库备份与恢复（覆盖式）。

两种客户端：
* **local**：用本机的 ``mysqldump`` / ``mysql`` / ``pg_dump`` / ``pg_restore`` / ``psql``
* **docker**：没有本地客户端时，用一次性容器跑同样命令（``docker run --rm -i``）

密码只通过环境变量传递（``MYSQL_PWD`` / ``PGPASSWORD``），不进命令行参数。
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from opsmate import config

ENGINES = {"mysql": 3306, "postgres": 5432}
DEFAULT_IMAGES = {"mysql": "mysql:8", "postgres": "postgres:16"}
LOCAL_TOOLS = {
    "mysql": ("mysqldump", "mysql"),
    "postgres": ("pg_dump", "pg_restore", "psql"),
}
ENV_VARS = {"mysql": "MYSQL_PWD", "postgres": "PGPASSWORD"}


class DbError(RuntimeError):
    pass


def new_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class DbTarget:
    name: str
    engine: str = "mysql"
    host: str = "127.0.0.1"
    port: int = 0
    user: str = "root"
    password: str = ""
    databases: list[str] = field(default_factory=list)   # 空 = 全部
    client_mode: str = "auto"                            # auto | local | docker
    image: str = ""
    note: str = ""
    id: str = field(default_factory=new_id)

    def __post_init__(self) -> None:
        self.engine = self.engine.lower()
        if self.engine not in ENGINES:
            raise ValueError(f"不支持的数据库类型：{self.engine}")
        if not self.port:
            self.port = ENGINES[self.engine]
        if not self.image:
            self.image = DEFAULT_IMAGES[self.engine]

    @property
    def label(self) -> str:
        scope = "全部数据库" if not self.databases else ",".join(self.databases)
        return f"{self.name} · {self.engine} · {self.user}@{self.host}:{self.port} · {scope}"


@dataclass
class DbCatalog:
    targets: list[DbTarget] = field(default_factory=list)

    @classmethod
    def load(cls) -> DbCatalog:
        raw = config.read_json(config.db_targets_path(), {"targets": []})
        items = raw.get("targets", []) if isinstance(raw, dict) else []
        out: list[DbTarget] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            fields = {k: v for k, v in item.items() if k in DbTarget.__dataclass_fields__}
            out.append(DbTarget(**fields))
        return cls(targets=out)

    def save(self) -> None:
        config.write_private(
            config.db_targets_path(),
            {"version": 1, "targets": [asdict(t) for t in self.targets]},
        )

    def by_id(self, target_id: str) -> DbTarget | None:
        return next((t for t in self.targets if t.id == target_id), None)

    def upsert(self, target: DbTarget) -> None:
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


# --------------------------------------------------------------------- 执行器
@dataclass
class Runner:
    mode: str
    image: str
    target: DbTarget

    def tool(self, name: str) -> list[str]:
        if self.mode == "docker":
            args = ["docker", "run", "--rm", "-i"]
            host = self.target.host
            if host in ("127.0.0.1", "localhost", "::1"):
                if sys.platform.startswith("linux"):
                    args += ["--network", "host"]
                else:
                    args += ["--add-host", "host.docker.internal:host-gateway"]
            args += ["-e", ENV_VARS[self.target.engine], self.image, name]
            return args
        return [name]

    def host_arg(self) -> str:
        host = self.target.host
        if (
            self.mode == "docker"
            and host in ("127.0.0.1", "localhost", "::1")
            and not sys.platform.startswith("linux")
        ):
            return "host.docker.internal"
        return host

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env[ENV_VARS[self.target.engine]] = self.target.password
        return env


def resolve_runner(target: DbTarget) -> Runner:
    mode = target.client_mode
    local_ok = all(shutil.which(t) for t in LOCAL_TOOLS[target.engine])
    docker_ok = shutil.which("docker") is not None

    if mode == "auto":
        if local_ok:
            mode = "local"
        elif docker_ok:
            mode = "docker"
        else:
            raise DbError(
                f"既没有本机 {target.engine} 客户端，也没有 Docker。"
                "请安装客户端或在设置里选 docker 模式。"
            )
    elif mode == "local" and not local_ok:
        missing = [t for t in LOCAL_TOOLS[target.engine] if not shutil.which(t)]
        raise DbError(f"缺少本机客户端：{', '.join(missing)}")
    elif mode == "docker" and not docker_ok:
        raise DbError("找不到 docker")
    return Runner(mode=mode, image=target.image, target=target)


def _run(cmd: list[str], runner: Runner, *, timeout: float | None = None,
         stdout: Any = None, stdin: Any = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, check=False, env=runner.env(), stdout=stdout, stdin=stdin,
            stderr=subprocess.PIPE, text=False, timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise DbError(f"命令不存在：{cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DbError(f"命令超时：{cmd[0]}") from exc


def _err(proc: subprocess.CompletedProcess) -> str:
    return (proc.stderr or b"").decode("utf-8", "replace").strip()


# --------------------------------------------------------------------- 查询
def test_connection(target: DbTarget, timeout: float = 20) -> str:
    runner = resolve_runner(target)
    if target.engine == "mysql":
        cmd = runner.tool("mysql") + [
            "--host", runner.host_arg(), "--port", str(target.port),
            "--user", target.user, "--connect-timeout", "10", "-N", "-B",
            "-e", "SELECT VERSION();",
        ]
    else:
        cmd = runner.tool("psql") + [
            "--host", runner.host_arg(), "--port", str(target.port),
            "--username", target.user, "--dbname", "postgres",
            "-tAc", "SELECT version();",
        ]
    proc = _run(cmd, runner, timeout=timeout)
    if proc.returncode != 0:
        raise DbError(_err(proc) or "连接失败")
    return proc.stdout.decode("utf-8", "replace").strip()


def list_databases(target: DbTarget, timeout: float = 20) -> list[str]:
    runner = resolve_runner(target)
    if target.engine == "mysql":
        cmd = runner.tool("mysql") + [
            "--host", runner.host_arg(), "--port", str(target.port),
            "--user", target.user, "-N", "-B", "-e", "SHOW DATABASES;",
        ]
    else:
        cmd = runner.tool("psql") + [
            "--host", runner.host_arg(), "--port", str(target.port),
            "--username", target.user, "--dbname", "postgres",
            "-tAc", "SELECT datname FROM pg_database WHERE datistemplate = false;",
        ]
    proc = _run(cmd, runner, timeout=timeout)
    if proc.returncode != 0:
        raise DbError(_err(proc) or "读取数据库列表失败")
    names = [line.strip() for line in proc.stdout.decode("utf-8", "replace").splitlines()]
    skip = {"information_schema", "performance_schema", "mysql", "sys"}
    return [n for n in names if n and n not in skip]


# --------------------------------------------------------------------- 备份
def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in text).strip("-") or "db"


def backup(target: DbTarget, out_dir: Path | None = None) -> Path:
    runner = resolve_runner(target)
    out_dir = out_dir or (config.backups_dir() / "db")
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{_slug(target.name)}-{target.engine}-{_stamp()}"

    if target.engine == "mysql":
        path = out_dir / f"{stem}.sql.gz"
        cmd = runner.tool("mysqldump") + [
            "--host", runner.host_arg(), "--port", str(target.port),
            "--user", target.user, "--single-transaction", "--quick",
            "--routines", "--triggers", "--events", "--add-drop-database",
            "--default-character-set=utf8mb4",
        ]
        cmd += ["--all-databases"] if not target.databases else ["--databases", *target.databases]
        _backup_gz(cmd, runner, path)
        return path

    if not target.databases:
        path = out_dir / f"{stem}.sql.gz"
        cmd = runner.tool("pg_dumpall") + [
            "--host", runner.host_arg(), "--port", str(target.port),
            "--username", target.user, "--clean", "--if-exists", "--no-owner",
        ]
        _backup_gz(cmd, runner, path)
        return path

    # PostgreSQL 指定库：每个库一份 custom dump，打包成 tar.gz
    path = out_dir / f"{stem}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for db in target.databases:
            dump = tmp_path / f"{db}.dump"
            base = runner.tool("pg_dump") + [
                "--host", runner.host_arg(), "--port", str(target.port),
                "--username", target.user, "--format", "custom", "--dbname", db,
            ]
            if runner.mode == "docker":
                # 容器里写文件拿不出来，直接 stdout 落到本地
                with dump.open("wb") as fh:
                    proc = _run(base, runner, stdout=fh)
            else:
                proc = _run([*base, "--file", str(dump)], runner)
            if proc.returncode != 0:
                raise DbError(_err(proc) or f"备份 {db} 失败")
        with tarfile.open(path, "w:gz") as tar:
            for dump in tmp_path.iterdir():
                tar.add(dump, arcname=dump.name)
    return path


def _backup_gz(cmd: list[str], runner: Runner, path: Path) -> None:
    # 注意：不能把 gzip 文件对象直接交给 stdout（subprocess 会绕过压缩缓冲），
    # 必须从管道读字节再写进 gzip。
    proc = subprocess.Popen(
        cmd, env=runner.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    with gzip.open(path, "wb") as fh:
        assert proc.stdout is not None
        shutil.copyfileobj(proc.stdout, fh)
        proc.stdout.close()
    err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", "replace").strip()
    code = proc.wait()
    if code != 0:
        path.unlink(missing_ok=True)
        raise DbError(err or "备份失败")


# --------------------------------------------------------------------- 恢复
def restore(target: DbTarget, archive: str | Path, *, confirm: bool = False) -> str:
    """覆盖式恢复：会 DROP 已有对象再导入，务必先确认。"""
    if not confirm:
        raise DbError("恢复会覆盖现有数据，需要显式 confirm=True")
    archive = Path(archive).expanduser()
    if not archive.exists():
        raise FileNotFoundError(archive)
    runner = resolve_runner(target)

    if target.engine == "mysql":
        cmd = runner.tool("mysql") + [
            "--host", runner.host_arg(), "--port", str(target.port),
            "--user", target.user, "--default-character-set=utf8mb4",
        ]
        _feed_gz(cmd, runner, archive)
        return "MySQL 恢复完成（已覆盖同名的库/表）"

    if archive.name.endswith(".tar.gz"):
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(archive, "r:gz") as tar:
                tar.extractall(tmp)  # 归档由本模块生成
            restored = []
            for dump in sorted(Path(tmp).glob("*.dump")):
                db = dump.stem
                cmd = runner.tool("pg_restore") + [
                    "--host", runner.host_arg(), "--port", str(target.port),
                    "--username", target.user, "--dbname", db,
                    "--clean", "--if-exists", "--no-owner", "--no-privileges",
                ]
                if runner.mode == "docker":
                    with dump.open("rb") as fh:
                        proc = _run(cmd, runner, stdin=fh)
                else:
                    proc = _run([*cmd, str(dump)], runner)
                if proc.returncode != 0:
                    raise DbError(_err(proc) or f"恢复 {db} 失败")
                restored.append(db)
            return "PostgreSQL 恢复完成：" + ", ".join(restored)

    cmd = runner.tool("psql") + [
        "--host", runner.host_arg(), "--port", str(target.port),
        "--username", target.user, "--dbname", "postgres", "-v", "ON_ERROR_STOP=1",
    ]
    _feed_gz(cmd, runner, archive)
    return "PostgreSQL 全库恢复完成（已覆盖同名对象）"


def _feed_gz(cmd: list[str], runner: Runner, archive: Path) -> None:
    proc = subprocess.Popen(
        cmd, env=runner.env(), stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        with gzip.open(archive, "rb") as src:
            shutil.copyfileobj(src, proc.stdin)  # type: ignore[arg-type]
    finally:
        if proc.stdin:
            proc.stdin.close()
    err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", "replace").strip()
    code = proc.wait()
    if code != 0:
        raise DbError(err or f"恢复失败（退出码 {code}）")
