"""MySQL / PostgreSQL 全库备份与恢复（覆盖式）。

两种客户端：
* **local**：用本机的 ``mysqldump`` / ``mysql`` / ``pg_dump`` / ``pg_restore`` / ``psql``
* **docker**：没有本地客户端时，用一次性容器跑同样命令（``docker run --rm -i``）

密码只通过环境变量传递（``MYSQL_PWD`` / ``PGPASSWORD``），不进命令行参数。
"""

from __future__ import annotations

import atexit
import gzip
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from opsmate import config, crypto

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
    ssh_host_id: str = ""                                # 走哪台 SSH 主机做隧道（空=直连）
    schedule_hours: int = 0                              # 自动备份间隔，0=关闭
    keep: int = 7                                        # 保留最近几份
    last_backup: str = ""                                # ISO 时间
    upload_to: str = ""                                  # 对象存储目标名，空=不上传
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


# --------------------------------------------------------------------- SSH 隧道
def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_port(port: int, timeout: float = 10.0) -> None:
    import socket

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise DbError(f"SSH 隧道没起来（本地端口 {port} 连不上）")


class TunnelPool:
    """同一进程内复用 ssh -L 连接（同跳板+同目标端口只开一条）。"""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, int], dict[str, Any]] = {}

    def acquire(self, target: DbTarget, inventory: Any | None = None) -> dict[str, Any]:
        from dataclasses import replace

        from landui_sdk.ssh import build_ssh_command, ssh_env

        from opsmate.inventory import Inventory

        key = (target.ssh_host_id, target.host, target.port)
        entry = self._entries.get(key)
        if entry is not None and entry["proc"].poll() is None:
            entry["uses"] += 1
            return entry
        if entry is not None:
            entry["proc"].kill()

        inventory = inventory or Inventory.load()
        host = inventory.by_id(target.ssh_host_id)
        if host is None:
            raise DbError(f"隧道主机不存在：{target.ssh_host_id}")

        local_port = _free_port()
        ssh_args = build_ssh_command(host.target())
        cmd = [
            "ssh", "-N",
            "-L", f"127.0.0.1:{local_port}:{target.host}:{target.port}",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            *ssh_args[1:],
        ]
        proc = subprocess.Popen(
            cmd, env=ssh_env(host.target()),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        _wait_port(local_port)
        entry = {
            "proc": proc, "port": local_port, "uses": 1,
            "scoped": replace(target, host="127.0.0.1", port=local_port),
        }
        self._entries[key] = entry
        return entry

    def close_all(self) -> None:
        for entry in self._entries.values():
            proc = entry["proc"]
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._entries.clear()


_POOL = TunnelPool()


def close_tunnels() -> None:
    _POOL.close_all()


atexit.register(close_tunnels)


@contextmanager
def tunnel(target: DbTarget, inventory: Any | None = None):
    """需要时开一个 ssh -L 本地转发；同进程内复用，进程退出时统一关闭。"""
    if not target.ssh_host_id:
        yield target
        return
    entry = _POOL.acquire(target, inventory)
    try:
        yield entry["scoped"]
    finally:
        entry["uses"] -= 1


def _via_tunnel(target: DbTarget) -> Any:
    """上下文管理器的别名，便于测试替换。"""
    return tunnel(target)


def test_connection(target: DbTarget, timeout: float = 20) -> str:
    with _via_tunnel(target) as scoped:
        return _test_connection(scoped, timeout)


def list_databases(target: DbTarget, timeout: float = 20) -> list[str]:
    with _via_tunnel(target) as scoped:
        return _list_databases(scoped, timeout)


def backup(
    target: DbTarget, out_dir: Path | None = None, password: str | None = None
) -> Path:
    with _via_tunnel(target) as scoped:
        path = _backup(scoped, out_dir, password)
    target.last_backup = time.strftime("%Y-%m-%dT%H:%M:%S")
    return path


def restore(
    target: DbTarget,
    archive: str | Path,
    *,
    confirm: bool = False,
    password: str | None = None,
) -> str:
    with _via_tunnel(target) as scoped:
        return _restore(scoped, archive, confirm=confirm, password=password)


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
            cmd, check=False, env=runner.env(),
            stdout=subprocess.PIPE if stdout is None else stdout,
            stdin=stdin, stderr=subprocess.PIPE, text=False, timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise DbError(f"命令不存在：{cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DbError(f"命令超时：{cmd[0]}") from exc


def _err(proc: subprocess.CompletedProcess) -> str:
    return (proc.stderr or b"").decode("utf-8", "replace").strip()


# --------------------------------------------------------------------- 查询
def _test_connection(target: DbTarget, timeout: float = 20) -> str:
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


def _list_databases(target: DbTarget, timeout: float = 20) -> list[str]:
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


def _backup(
    target: DbTarget, out_dir: Path | None = None, password: str | None = None
) -> Path:
    runner = resolve_runner(target)
    out_dir = out_dir or (config.backups_dir() / "db")
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{_slug(target.name)}-{target.engine}-{_stamp()}"
    suffix = ".enc" if password else ""

    if target.engine == "mysql":
        path = out_dir / f"{stem}.sql.gz{suffix}"
        cmd = runner.tool("mysqldump") + [
            "--host", runner.host_arg(), "--port", str(target.port),
            "--user", target.user, "--single-transaction", "--quick",
            "--routines", "--triggers", "--events", "--add-drop-database",
            "--default-character-set=utf8mb4",
        ]
        cmd += ["--all-databases"] if not target.databases else ["--databases", *target.databases]
        _backup_gz(cmd, runner, path, password)
        return path

    if not target.databases:
        path = out_dir / f"{stem}.sql.gz{suffix}"
        cmd = runner.tool("pg_dumpall") + [
            "--host", runner.host_arg(), "--port", str(target.port),
            "--username", target.user, "--clean", "--if-exists", "--no-owner",
        ]
        _backup_gz(cmd, runner, path, password)
        return path

    # PostgreSQL 指定库：每个库一份 custom dump，打包成 tar.gz
    path = out_dir / f"{stem}.tar.gz{suffix}"
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
        if password:
            writer = crypto.EncryptingWriter(path, password)
            try:
                with tarfile.open(fileobj=writer, mode="w:gz") as tar:
                    for dump in tmp_path.iterdir():
                        tar.add(dump, arcname=dump.name)
            except Exception:
                writer.abort()
                raise
            writer.close()
        else:
            with tarfile.open(path, "w:gz") as tar:
                for dump in tmp_path.iterdir():
                    tar.add(dump, arcname=dump.name)
    return path


def _backup_gz(
    cmd: list[str], runner: Runner, path: Path, password: str | None = None
) -> None:
    # 不能把 gzip 文件对象直接交给 stdout（subprocess 会绕过压缩缓冲），
    # 必须从管道读字节再写进 gzip。加密时再套一层 EncryptingWriter。
    proc = subprocess.Popen(
        cmd, env=runner.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    writer = crypto.EncryptingWriter(path, password) if password else None
    try:
        with gzip.GzipFile(fileobj=writer, filename="", mode="wb") if writer else gzip.open(path, "wb") as fh:
            assert proc.stdout is not None
            shutil.copyfileobj(proc.stdout, fh)
            proc.stdout.close()
        if writer:
            writer.close()
    except Exception:
        if writer:
            writer.abort()
        else:
            path.unlink(missing_ok=True)
        raise
    err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", "replace").strip()
    code = proc.wait()
    if code != 0:
        path.unlink(missing_ok=True)
        raise DbError(err or "备份失败")


# --------------------------------------------------------------------- 恢复
def _restore(
    target: DbTarget,
    archive: str | Path,
    *,
    confirm: bool = False,
    password: str | None = None,
) -> str:
    """覆盖式恢复：会 DROP 已有对象再导入，务必先确认。"""
    if not confirm:
        raise DbError("恢复会覆盖现有数据，需要显式 confirm=True")
    archive = Path(archive).expanduser()
    if not archive.exists():
        raise FileNotFoundError(archive)

    if crypto.is_encrypted(archive):
        if not password:
            raise DbError("这是加密备份，需要提供口令")
        with tempfile.TemporaryDirectory() as tmp:
            plain = crypto.decrypt_file(archive, password, Path(tmp) / archive.name[:-4])
            return _restore(target, plain, confirm=True)
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


# --------------------------------------------------------------------- 保留/定时
def _backup_dir() -> Path:
    path = config.backups_dir() / "db"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _prefix(target: DbTarget) -> str:
    return f"{_slug(target.name)}-{target.engine}-"


def prune(target: DbTarget, keep: int | None = None, out_dir: Path | None = None) -> int:
    """只保留最近 keep 份该目标的备份，返回删除数量。"""
    keep = target.keep if keep is None else keep
    if keep <= 0:
        return 0
    directory = out_dir or _backup_dir()
    files = sorted(
        (p for p in directory.glob(f"{_prefix(target)}*") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for old in files[keep:]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def is_due(target: DbTarget, now: float | None = None) -> bool:
    if target.schedule_hours <= 0:
        return False
    if not target.last_backup:
        return True
    try:
        last = time.mktime(time.strptime(target.last_backup, "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return True
    return (now or time.time()) - last >= target.schedule_hours * 3600


def autobackup(*, force: bool = False, catalog: DbCatalog | None = None) -> list[tuple[str, str]]:
    """给所有到期的目标做备份并清理旧文件；返回 [(目标名, 结果)]。"""
    catalog = catalog or DbCatalog.load()
    results: list[tuple[str, str]] = []
    changed = False
    for target in catalog.targets:
        if not force and not is_due(target):
            continue
        try:
            path = backup(target)
            remote = ""
            if target.upload_to:
                from opsmate import storage

                store = storage.StorageStore.load()
                dest = store.by_name(target.upload_to) or store.by_id(target.upload_to)
                if dest is None:
                    remote = f"（上传失败：找不到存储 {target.upload_to}）"
                else:
                    try:
                        remote = "，已上传 " + storage.upload(dest, path)
                    except Exception as exc:  # noqa: BLE001
                        remote = f"（上传失败：{type(exc).__name__}: {exc}）"
            removed = prune(target)
            target.last_backup = time.strftime("%Y-%m-%dT%H:%M:%S")
            changed = True
            extra = f"（清理 {removed} 份旧备份）" if removed else ""
            results.append((target.name, f"{path}{extra}{remote}"))
        except Exception as exc:  # noqa: BLE001 - 单个失败不影响其它
            results.append((target.name, f"失败：{type(exc).__name__}: {exc}"))
    if changed:
        catalog.save()
    return results


def restore_with_safety(
    target: DbTarget,
    archive: str | Path,
    *,
    confirm: bool = False,
    password: str | None = None,
) -> str:
    """恢复前先给当前状态拍一份快照，失败了也能回头。"""
    if not confirm:
        raise DbError("恢复会覆盖现有数据，需要显式 confirm=True")
    safety: Path | None = None
    warning = ""
    try:
        safety = backup(target, out_dir=_backup_dir() / "pre-restore")
    except Exception as exc:  # noqa: BLE001 - 库已经坏了也要允许恢复
        warning = f"（当前状态快照失败：{type(exc).__name__}: {exc}）"
    message = restore(target, archive, confirm=True, password=password)
    if safety is not None:
        return f"{message}；恢复前快照：{safety.name}"
    return message + warning


# --------------------------------------------------------------------- 恢复演练
def _table_counts(target: DbTarget) -> dict[str, int]:
    runner = resolve_runner(target)
    counts: dict[str, int] = {}
    if target.engine == "mysql":
        cmd = runner.tool("mysql") + [
            "--host", runner.host_arg(), "--port", str(target.port),
            "--user", target.user, "-N", "-B", "-e",
            (
                "SELECT table_schema, COUNT(*) FROM information_schema.tables "
                "WHERE table_schema NOT IN "
                "('information_schema','performance_schema','mysql','sys') "
                "GROUP BY table_schema;"
            ),
        ]
        proc = _run(cmd, runner, timeout=30)
        if proc.returncode == 0:
            for line in proc.stdout.decode("utf-8", "replace").splitlines():
                schema, _, value = line.partition("\t")
                if schema and value.strip().isdigit():
                    counts[schema] = int(value)
        return counts

    dbs = target.databases or list_databases(target)[:5]
    for db in dbs:
        cmd = runner.tool("psql") + [
            "--host", runner.host_arg(), "--port", str(target.port),
            "--username", target.user, "--dbname", db, "-tAc",
            (
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog','information_schema');"
            ),
        ]
        proc = _run(cmd, runner, timeout=30)
        if proc.returncode == 0:
            value = proc.stdout.decode("utf-8", "replace").strip()
            if value.isdigit():
                counts[db] = int(value)
    return counts


def drill(
    target: DbTarget,
    archive: str | Path,
    *,
    password: str | None = None,
    out_dir: Path | None = None,
) -> Path:
    """恢复演练：快照 → 覆盖恢复 → 校验 → 生成 Markdown 报告。"""
    started = time.time()
    safety: Path | None = None
    safety_err = ""
    restore_err = ""
    version = ""
    dbs: list[str] = []
    counts: dict[str, int] = {}

    try:
        safety = backup(target)
    except Exception as exc:  # noqa: BLE001 - 快照失败不阻断演练
        safety_err = f"{type(exc).__name__}: {exc}"

    try:
        restore(target, archive, confirm=True, password=password)
    except Exception as exc:  # noqa: BLE001 - 写进报告再抛
        restore_err = f"{type(exc).__name__}: {exc}"
    else:
        try:
            version = test_connection(target)
            dbs = list_databases(target)
            counts = _table_counts(target)
        except Exception as exc:  # noqa: BLE001
            restore_err = f"恢复后校验失败：{type(exc).__name__}: {exc}"

    elapsed = time.time() - started
    reports = (out_dir or (config.home() / "reports"))
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"restore-{_slug(target.name)}-{_stamp()}.md"
    lines = [
        f"# 恢复演练报告：{target.name}",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        (
            f"- 目标：{target.engine} {target.user}@{target.host}:{target.port}"
            f"（{'全部库' if not target.databases else ','.join(target.databases)}）"
        ),
        f"- 备份文件：`{archive}`",
        f"- 耗时：{elapsed:.1f}s",
        f"- 恢复前快照：`{safety}`" if safety else f"- 恢复前快照：失败（{safety_err}）",
        "",
        "## 结果",
        "",
        ("❌ 失败：" + restore_err) if restore_err else "✅ 恢复成功",
        "",
    ]
    if version:
        lines += ["## 校验", "", f"- 版本：{version.splitlines()[0][:120]}",
                  f"- 数据库：{', '.join(dbs) or '（无）'}", ""]
        if counts:
            lines += ["| 库 | 表数量 |", "| --- | --- |"]
            lines += [f"| {name} | {value} |" for name, value in counts.items()]
            lines.append("")
    path.write_text("\n".join(lines), "utf-8")
    if restore_err:
        raise DbError(f"{restore_err}（报告：{path}）")
    return path
