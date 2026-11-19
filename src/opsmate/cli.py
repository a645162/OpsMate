"""命令行入口：``ops <命令>``。"""

from __future__ import annotations

import argparse
import getpass
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from opsmate import APP_NAME, APP_NAME_ZH, __version__


# --------------------------------------------------------------------- 输出
def print_table(headers: list[str], rows: list[list[str]]) -> None:
    if not rows:
        print("（空）")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("-" * (sum(widths) + 2 * (len(headers) - 1)))
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def pick(items, ref: str, label: str):
    by_id = [item for item in items if getattr(item, "id", "") == ref]
    if by_id:
        return by_id[0]
    by_name = [item for item in items if getattr(item, "name", "").lower() == ref.lower()]
    if by_name:
        return by_name[0]
    matches = [item for item in items if ref.lower() in getattr(item, "name", "").lower()]
    if len(matches) == 1:
        return matches[0]
    raise LookupError(f"找不到{label}：{ref}")


# --------------------------------------------------------------------- 基础
def cmd_version(_args: argparse.Namespace) -> int:
    print(f"{APP_NAME}（{APP_NAME_ZH}） {__version__}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from opsmate.doctor import run_checks, summarize

    checks = run_checks(online=args.online)
    width = max(len(c.name) for c in checks)
    for check in checks:
        print(f"{check.icon} {check.name.ljust(width)}  {check.detail}")
    ok, text = summarize(checks)
    print()
    print(("✅ " if ok else "❌ ") + text)
    return 0 if ok else 1


def cmd_servers(_args: argparse.Namespace) -> int:
    from opsmate import landui

    try:
        servers = landui.client().cloud.all_servers()
    except Exception as exc:  # noqa: BLE001
        return fail(landui.friendly_error(exc))
    print_table(
        ["ID", "名称", "状态", "公网IP", "规格", "到期"],
        [[str(s.id), s.name, s.status, s.ip, s.cpu_mem, s.expire_at] for s in servers],
    )
    print("\n提示：ops hosts import  可以导入到本地清单")
    return 0


def cmd_ssh(args: argparse.Namespace) -> int:
    from landui_sdk.ssh import launch_ssh

    from opsmate.inventory import Inventory

    inventory = Inventory.load()
    try:
        host = pick(inventory.hosts, args.ref, "主机")
    except LookupError as exc:
        return fail(f"{exc}（先用 ops hosts add 添加）")
    try:
        cmd = launch_ssh(host.target(), args.launcher or "auto")
    except Exception as exc:  # noqa: BLE001
        return fail(f"{type(exc).__name__}: {exc}")
    print(f"[{host.user}@{host.host}:{host.port}] {cmd}")
    return 0


# --------------------------------------------------------------------- 主机
def cmd_hosts(args: argparse.Namespace) -> int:
    from opsmate.inventory import HostEntry, Inventory

    inventory = Inventory.load()

    if args.hosts_cmd == "list":
        rows = [
            [h.id, h.name, h.host, str(h.port), h.user, h.auth, ",".join(h.tags), h.note]
            for h in inventory.hosts
        ]
        print_table(["ID", "名称", "地址", "端口", "用户", "认证", "标签", "备注"], rows)
        return 0

    if args.hosts_cmd == "add":
        if not args.host:
            return fail("需要 --host")
        existing = inventory.by_id(args.id) if args.id else None
        entry = existing or HostEntry(name=args.name or args.host, host=args.host)
        entry.name = args.name or entry.name
        entry.host = args.host
        entry.port = args.port or entry.port
        entry.user = args.user or entry.user
        entry.auth = args.auth or entry.auth
        entry.key_path = args.key or entry.key_path
        entry.note = args.note if args.note is not None else entry.note
        if args.tags:
            entry.tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        if args.ask_password:
            entry.password = getpass.getpass("SSH 密码（不回显）: ")
            entry.auth = "password"
        inventory.upsert(entry)
        print(f"已保存：{entry.id}  {entry.label}")
        return 0

    if args.hosts_cmd == "rm":
        try:
            host = pick(inventory.hosts, args.ref, "主机")
        except LookupError as exc:
            return fail(str(exc))
        inventory.remove(host.id)
        print(f"已删除 {host.name}")
        return 0

    if args.hosts_cmd == "import":
        from opsmate import landui

        try:
            servers = landui.client().cloud.all_servers()
        except Exception as exc:  # noqa: BLE001
            return fail(landui.friendly_error(exc))
        added, updated = inventory.import_landui(servers)
        print(f"导入完成：新增 {added} 台，更新 {updated} 台")
        return 0

    return fail("未知子命令")


# --------------------------------------------------------------------- 密钥
def cmd_keys(args: argparse.Namespace) -> int:
    from opsmate import sshkeys

    if args.keys_cmd == "list":
        keys = sshkeys.list_keys()
        print_table(
            ["名称", "类型", "位数", "指纹", "注释", "路径"],
            [
                [k.name, k.key_type, str(k.bits), k.fingerprint, k.comment, str(k.public_path)]
                for k in keys
            ],
        )
        return 0

    if args.keys_cmd == "new":
        passphrase = ""
        if args.ask_passphrase:
            passphrase = getpass.getpass("私钥口令（留空表示无）: ")
        try:
            key = sshkeys.generate_key(
                args.name,
                key_type=args.type,
                comment=args.comment,
                passphrase=passphrase,
                bits=args.bits,
                overwrite=args.force,
            )
        except Exception as exc:  # noqa: BLE001
            return fail(str(exc))
        print(f"已生成：{key.private_path}")
        print(f"公钥：{key.public_path}")
        print(key.fingerprint)
        return 0

    if args.keys_cmd == "show":
        try:
            keys = sshkeys.list_keys()
            key = pick(keys, args.ref, "密钥")
        except LookupError as exc:
            return fail(str(exc))
        print(key.public_text())
        return 0

    if args.keys_cmd == "install":
        from opsmate.inventory import Inventory

        inventory = Inventory.load()
        try:
            host = pick(inventory.hosts, args.host, "主机")
            key = pick(sshkeys.list_keys(), args.key, "密钥")
        except LookupError as exc:
            return fail(str(exc))
        try:
            print(sshkeys.install_public_key(host, key.public_text()))
        except Exception as exc:  # noqa: BLE001
            return fail(f"{type(exc).__name__}: {exc}")
        return 0

    if args.keys_cmd == "github":
        try:
            key = pick(sshkeys.list_keys(), args.ref, "密钥")
        except LookupError as exc:
            return fail(str(exc))
        title = args.title or f"{socket.gethostname()}-{key.name}"
        try:
            print(sshkeys.upload_to_github(key.public_path, title))
        except Exception as exc:  # noqa: BLE001
            return fail(f"{type(exc).__name__}: {exc}")
        return 0

    if args.keys_cmd == "delete":
        try:
            key = pick(sshkeys.list_keys(), args.ref, "密钥")
        except LookupError as exc:
            return fail(str(exc))
        if not args.yes:
            return fail("删除密钥会同时删掉私钥，确认请加 --yes")
        sshkeys.delete_key(key)
        print(f"已删除 {key.name}")
        return 0

    return fail("未知子命令")


# --------------------------------------------------------------------- config
def cmd_config(args: argparse.Namespace) -> int:
    from opsmate import config, sshconfig

    path = config.ssh_config_path()
    if args.config_cmd == "show":
        print(f"# {path}")
        print(sshconfig.read_config(path) or "（文件不存在或为空）")
        return 0

    if args.config_cmd == "backup":
        backup = sshconfig.backup_file(path)
        print(f"已备份到 {backup}" if backup else "没有可备份的文件")
        return 0

    if args.config_cmd == "sync":
        backup, text = sshconfig.sync(path)
        if backup:
            print(f"已备份原文件 -> {backup}")
        print(f"已同步到 {path}（{len(sshconfig.parse_hosts(text))} 个 Host 块）")
        return 0

    if args.config_cmd == "edit":
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
        if not editor:
            return fail(f"没有设置 $EDITOR，请直接编辑：{path}")
        sshconfig.backup_file(path)
        return subprocess.call([editor, str(path)], check=False)

    return fail("未知子命令")


# --------------------------------------------------------------------- 导出
def cmd_export(args: argparse.Namespace) -> int:
    from opsmate import sshconfig, terminals
    from opsmate.inventory import Inventory

    inventory = Inventory.load()
    if args.export_cmd == "ssh":
        backup, text = sshconfig.sync()
        print(f"已同步 {len(sshconfig.parse_hosts(text))} 个 Host 到 ~/.ssh/config")
        if backup:
            print(f"原文件备份：{backup}")
        return 0

    if args.export_cmd == "tabby":
        if args.apply:
            backup, added, path = terminals.apply_tabby(inventory)
            print(f"Tabby：新增 {added} 个 profile -> {path}")
        else:
            path = terminals.export_tabby(inventory)
            print(f"已导出片段：{path}（手动合并到 Tabby 配置，或加 --apply）")
        return 0

    if args.export_cmd == "wt":
        if args.apply:
            backup, added, path = terminals.apply_windows_terminal(inventory)
            print(f"Windows Terminal：新增 {added} 个 profile -> {path}")
        else:
            path = terminals.export_windows_terminal(inventory)
            print(f"已导出片段：{path}（Windows 上可加 --apply 直接合并）")
        return 0

    return fail("未知子命令")


def _backup_passphrase(label: str = "备份口令") -> str:
    env = os.environ.get("OPSMATE_BACKUP_PASSPHRASE")
    if env:
        return env
    return getpass.getpass(f"{label}（留空=不加密）: ")


# --------------------------------------------------------------------- 对象存储
def cmd_storage(args: argparse.Namespace) -> int:
    from opsmate import storage

    store = storage.StorageStore.load()
    if args.storage_cmd == "list":
        print_table(
            ["ID", "名称", "提供商", "bucket", "region", "endpoint", "前缀"],
            [[t.id, t.name, t.provider, t.bucket, t.region or "-",
              t.effective_endpoint() or "-", t.prefix] for t in store.targets],
        )
        return 0
    if args.storage_cmd == "add":
        target = storage.StorageTarget(
            name=args.name, provider=args.provider, bucket=args.bucket,
            region=args.region or "", endpoint=args.endpoint or "",
            access_key=args.access_key or "", prefix=args.prefix,
        )
        target.secret_key = getpass.getpass("SecretKey（不回显）: ")
        store.upsert(target)
        print(f"已保存存储：{target.id} {target.label}")
        return 0
    if args.storage_cmd == "prune":
        target = pick(store.targets, args.ref, "存储")
        removed = storage.prune_remote(
            target, prefix=args.prefix, match=args.match or "",
            keep=args.keep, dry_run=args.dry_run,
        )
        verb = "将删除" if args.dry_run else "已删除"
        for key in removed:
            print(f"{verb} {key}")
        print(f"共 {len(removed)} 个对象")
        return 0
    if args.storage_cmd == "verify":
        target = pick(store.targets, args.ref, "存储")
        key = args.key
        if not key:
            from pathlib import Path as _Path

            fake = _Path(args.file)
            key = storage.object_key(target, fake)
            print(f"未指定 --key，按当前前缀推导：{key}")
        digest = storage.verify_remote(target, args.file, key)
        print(f"校验通过：{digest}")
        return 0
    if args.storage_cmd == "rm":
        target = pick(store.targets, args.ref, "存储")
        store.remove(target.id)
        print(f"已删除 {target.name}")
        return 0
    return fail("未知子命令")


# --------------------------------------------------------------------- DNS
def cmd_dns(args: argparse.Namespace) -> int:
    from opsmate import dns

    store = dns.DnsStore.load()

    if args.dns_cmd == "creds":
        if args.creds_cmd == "list":
            print_table(
                ["ID", "名称", "提供商", "凭据"],
                [
                    [c.id, c.name, c.provider,
                     "token" if c.provider == "cloudflare" else c.access_key_id]
                    for c in store.credentials
                ],
            )
            return 0
        if args.creds_cmd == "add":
            cred = dns.DnsCredential(name=args.name, provider=args.provider)
            if args.provider == "cloudflare":
                cred.token = getpass.getpass("Cloudflare API Token: ")
            else:
                cred.access_key_id = input("AccessKeyId: ").strip()
                cred.access_key_secret = getpass.getpass("AccessKeySecret: ")
            store.upsert(cred)
            print(f"已保存凭据：{cred.id} {cred.label}")
            return 0
        if args.creds_cmd == "rm":
            cred = pick(store.credentials, args.ref, "凭据")
            store.remove(cred.id)
            print(f"已删除 {cred.name}")
            return 0

    try:
        cred = pick(store.credentials, args.cred, "凭据")
        provider = dns.provider_for(cred)
        zones = provider.zones()
    except Exception as exc:  # noqa: BLE001
        return fail(f"{type(exc).__name__}: {exc}")

    if args.dns_cmd == "zones":
        print_table(["ID", "域名"], [[z.id, z.name] for z in zones])
        return 0

    try:
        zone = pick(zones, args.zone, "域名")
    except LookupError as exc:
        return fail(str(exc))

    try:
        if args.dns_cmd == "records":
            records = provider.records(zone.name if cred.provider == "aliyun" else zone.id)
            rows = [
                [r.id, r.name, r.type, r.content, str(r.ttl),
                 "已代理" if r.proxied else (r.line or "-"), str(r.priority or "")]
                for r in records
            ]
            print_table(
                ["ID", "名称", "类型", "值", "TTL", "线路/代理", "优先级"], rows
            )
            return 0

        if args.dns_cmd == "add":
            if cred.provider == "cloudflare":
                provider.add(zone.id, name=args.name, type=args.type,
                             content=args.content, ttl=args.ttl,
                             proxied=args.proxied, priority=args.priority)
            else:
                provider.add(zone.name, name=args.name, type=args.type,
                             content=args.content, ttl=args.ttl,
                             line=args.line, priority=args.priority)
            print("已添加记录")
            return 0

        if args.dns_cmd == "update":
            fields: dict[str, Any] = {}
            if cred.provider == "cloudflare":
                if args.name: fields["name"] = args.name
                if args.type: fields["type"] = args.type
                if args.content: fields["content"] = args.content
                if args.ttl: fields["ttl"] = args.ttl
                if args.proxied is not None: fields["proxied"] = args.proxied
                provider.update(zone.id, args.record_id, **fields)
            else:
                if args.name: fields["RR"] = args.name
                if args.type: fields["Type"] = args.type
                if args.content: fields["Value"] = args.content
                if args.ttl: fields["TTL"] = args.ttl
                if args.line: fields["Line"] = args.line
                provider.update(args.record_id, **fields)
            print("已更新记录")
            return 0

        if args.dns_cmd == "rm":
            if not args.yes:
                return fail("删除解析记录确认请加 --yes")
            provider.delete(zone.id if cred.provider == "cloudflare" else args.record_id)
            print("已删除记录")
            return 0
    except Exception as exc:  # noqa: BLE001
        return fail(f"{type(exc).__name__}: {exc}")
    return fail("未知子命令")


# --------------------------------------------------------------------- 数据库
def cmd_db(args: argparse.Namespace) -> int:
    from opsmate import db

    catalog = db.DbCatalog.load()

    if args.db_cmd == "list":
        print_table(
            ["ID", "名称", "类型", "地址", "用户", "范围", "模式", "隧道", "自动"],
            [
                [t.id, t.name, t.engine, f"{t.host}:{t.port}", t.user,
                 "全部" if not t.databases else ",".join(t.databases), t.client_mode,
                 t.ssh_host_id or "—",
                 f"{t.schedule_hours}h/留{t.keep}" if t.schedule_hours else "—"]
                for t in catalog.targets
            ],
        )
        return 0

    if args.db_cmd == "add":
        target = db.DbTarget(
            name=args.name,
            engine=args.engine,
            host=args.host or "127.0.0.1",
            port=args.port or 0,
            user=args.user or ("root" if args.engine == "mysql" else "postgres"),
            databases=[d.strip() for d in (args.databases or "").split(",") if d.strip()],
            client_mode=args.mode,
            image=args.image or "",
            ssh_host_id=args.ssh_host or "",
            schedule_hours=args.every_hours or 0,
            keep=args.keep,
            upload_to=args.upload or "",
            remote_keep=args.remote_keep,
        )
        if args.ask_password:
            target.password = getpass.getpass("数据库密码（不回显）: ")
        catalog.upsert(target)
        print(f"已保存：{target.id}  {target.label}")
        return 0

    if args.db_cmd == "rm":
        try:
            target = pick(catalog.targets, args.ref, "数据库")
        except LookupError as exc:
            return fail(str(exc))
        catalog.remove(target.id)
        print(f"已删除 {target.name}")
        return 0

    if args.db_cmd == "test":
        try:
            target = pick(catalog.targets, args.ref, "数据库")
            print(db.test_connection(target))
        except Exception as exc:  # noqa: BLE001
            return fail(f"{type(exc).__name__}: {exc}")
        return 0

    if args.db_cmd == "databases":
        try:
            target = pick(catalog.targets, args.ref, "数据库")
            print("\n".join(db.list_databases(target)))
        except Exception as exc:  # noqa: BLE001
            return fail(f"{type(exc).__name__}: {exc}")
        return 0

    if args.db_cmd == "backup":
        try:
            target = pick(catalog.targets, args.ref, "数据库")
            password = _backup_passphrase() if args.encrypt else None
            path = db.backup(
                target, Path(args.out) if args.out else None, password=password or None
            )
            remote = ""
            if args.upload:
                from opsmate import storage

                store = storage.StorageStore.load()
                dest = store.by_name(args.upload) or store.by_id(args.upload)
                if dest is None:
                    return fail(f"找不到存储：{args.upload}")
                uri = storage.upload(dest, path)
                remote = "，已上传 " + uri
                if not args.no_verify:
                    _, key = storage.parse_uri(uri)
                    storage.verify_remote(dest, path, key)
                    remote += "，校验通过"
        except Exception as exc:  # noqa: BLE001
            return fail(f"{type(exc).__name__}: {exc}")
        print(f"备份完成：{path}{remote}")
        return 0

    if args.db_cmd == "drill":
        try:
            target = pick(catalog.targets, args.ref, "数据库")
            from opsmate import crypto

            password = None
            if crypto.is_encrypted(args.file):
                password = getpass.getpass("这是加密备份，输入口令: ")
            report = db.drill(target, args.file, password=password)
        except Exception as exc:  # noqa: BLE001
            return fail(f"{type(exc).__name__}: {exc}")
        print(f"演练完成，报告：{report}")
        return 0

    if args.db_cmd == "autobackup":
        results = db.autobackup(force=args.force)
        if not results:
            print("没有到期的备份任务")
            return 0
        for name, result in results:
            print(f"{name}: {result}")
        return 0

    if args.db_cmd == "schedule":
        try:
            target = pick(catalog.targets, args.ref, "数据库")
        except LookupError as exc:
            return fail(str(exc))
        target.schedule_hours = args.hours
        target.keep = args.keep
        catalog.upsert(target)
        print(f"{target.name}: 每 {args.hours} 小时备份一次，保留 {args.keep} 份")
        print(_schedule_hint(args.hours))
        if args.install:
            return _install_schedule(args.hours)
        return 0

    if args.db_cmd == "restore":
        if not args.yes:
            return fail("恢复会覆盖现有数据，确认请加 --yes")
        try:
            target = pick(catalog.targets, args.ref, "数据库")
            from opsmate import crypto

            password = None
            if crypto.is_encrypted(args.file):
                password = getpass.getpass("这是加密备份，输入口令: ")
            if args.no_safety:
                print(db.restore(target, args.file, confirm=True, password=password))
            else:
                print(
                    db.restore_with_safety(
                        target, args.file, confirm=True, password=password
                    )
                )
        except Exception as exc:  # noqa: BLE001
            return fail(f"{type(exc).__name__}: {exc}")
        return 0

    return fail("未知子命令")


def _schedule_hint(hours: int) -> str:
    return (
        "让它自动跑：\n"
        f"  Linux:  ops db schedule <名称> --hours {hours} --install\n"
        f"  或 cron: 0 */{max(hours, 1)} * * * {sys.executable} -m opsmate db autobackup\n"
        f"  Windows: schtasks /Create /SC HOURLY /MO {max(hours, 1)} /TN OpsMateDBBackup "
        f"/TR \"{sys.executable} -m opsmate db autobackup\""
    )


def _install_schedule(hours: int) -> int:
    if sys.platform != "linux":
        print("当前系统不支持自动安装，请用上面打印的计划任务命令。")
        return 1
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    service = unit_dir / "opsmate-db-backup.service"
    timer = unit_dir / "opsmate-db-backup.timer"
    service.write_text(
        "[Unit]\nDescription=OpsMate database backup\n\n"
        "[Service]\nType=oneshot\n"
        f"ExecStart={sys.executable} -m opsmate db autobackup\n",
        "utf-8",
    )
    timer.write_text(
        "[Unit]\nDescription=OpsMate database backup timer\n\n"
        "[Timer]\nOnBootSec=5min\n"
        f"OnUnitActiveSec={max(hours, 1)}h\nPersistent=true\n\n"
        "[Install]\nWantedBy=timers.target\n",
        "utf-8",
    )
    subprocess.call(["systemctl", "--user", "daemon-reload"], check=False)
    code = subprocess.call(
        ["systemctl", "--user", "enable", "--now", "opsmate-db-backup.timer"], check=False
    )
    if code == 0:
        print("已安装 systemd 用户定时器：opsmate-db-backup.timer")
    else:
        print("安装失败，请手动执行：systemctl --user enable --now opsmate-db-backup.timer")
    return code


# --------------------------------------------------------------------- 解析器
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ops", description=f"{APP_NAME}（{APP_NAME_ZH}）—— 本地服务器运维助手"
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="显示版本").set_defaults(func=cmd_version)

    doctor = sub.add_parser("doctor", help="环境自检")
    doctor.add_argument("--online", action="store_true", help="额外验证蓝队云登录态")
    doctor.set_defaults(func=cmd_doctor)

    sub.add_parser("servers", help="列出蓝队云服务器").set_defaults(func=cmd_servers)

    ssh = sub.add_parser("ssh", help="用本地清单里的主机打开 SSH")
    ssh.add_argument("ref", help="主机 ID 或名称")
    ssh.add_argument("--launcher", help="打开方式：auto/tabby/gnome-terminal…")
    ssh.set_defaults(func=cmd_ssh)

    hosts = sub.add_parser("hosts", help="本地主机清单")
    hosts_sub = hosts.add_subparsers(dest="hosts_cmd", required=True)
    hosts_sub.add_parser("list", help="列出主机").set_defaults(func=cmd_hosts)
    add = hosts_sub.add_parser("add", help="添加/更新主机")
    add.add_argument("--id", help="更新已有主机时传 ID")
    add.add_argument("--name")
    add.add_argument("--host")
    add.add_argument("--port", type=int)
    add.add_argument("--user")
    add.add_argument("--auth", choices=["agent", "password", "key"])
    add.add_argument("--key", help="私钥路径")
    add.add_argument("--tags", help="逗号分隔")
    add.add_argument("--note")
    add.add_argument("--ask-password", action="store_true", help="交互输入 SSH 密码")
    add.set_defaults(func=cmd_hosts)
    rm = hosts_sub.add_parser("rm", help="删除主机")
    rm.add_argument("ref")
    rm.set_defaults(func=cmd_hosts)
    hosts_sub.add_parser("import", help="从蓝队云导入").set_defaults(func=cmd_hosts)

    keys = sub.add_parser("keys", help="SSH 密钥")
    keys_sub = keys.add_subparsers(dest="keys_cmd", required=True)
    keys_sub.add_parser("list", help="列出密钥").set_defaults(func=cmd_keys)
    new = keys_sub.add_parser("new", help="生成密钥")
    new.add_argument("name")
    new.add_argument("--type", default="ed25519", choices=["ed25519", "rsa", "ecdsa"])
    new.add_argument("--bits", type=int)
    new.add_argument("--comment", default="")
    new.add_argument("--ask-passphrase", action="store_true")
    new.add_argument("--force", action="store_true", help="覆盖同名密钥")
    new.set_defaults(func=cmd_keys)
    show = keys_sub.add_parser("show", help="输出公钥（可直接复制）")
    show.add_argument("ref")
    show.set_defaults(func=cmd_keys)
    install = keys_sub.add_parser("install", help="装到服务器的 authorized_keys")
    install.add_argument("host")
    install.add_argument("key")
    install.set_defaults(func=cmd_keys)
    github = keys_sub.add_parser("github", help="上传到 GitHub（gh ssh-key add）")
    github.add_argument("ref")
    github.add_argument("--title")
    github.set_defaults(func=cmd_keys)
    delete = keys_sub.add_parser("delete", help="删除本地密钥")
    delete.add_argument("ref")
    delete.add_argument("--yes", action="store_true")
    delete.set_defaults(func=cmd_keys)

    conf = sub.add_parser("config", help="~/.ssh/config")
    conf_sub = conf.add_subparsers(dest="config_cmd", required=True)
    conf_sub.add_parser("show", help="查看").set_defaults(func=cmd_config)
    conf_sub.add_parser("backup", help="备份").set_defaults(func=cmd_config)
    conf_sub.add_parser("sync", help="把清单写进托管块").set_defaults(func=cmd_config)
    conf_sub.add_parser("edit", help="用 $EDITOR 编辑").set_defaults(func=cmd_config)

    export = sub.add_parser("export", help="导出到终端客户端")
    export_sub = export.add_subparsers(dest="export_cmd", required=True)
    for name, help_text in (
        ("ssh", "写入 ~/.ssh/config 托管块"),
        ("tabby", "Tabby profiles"),
        ("wt", "Windows Terminal profiles"),
    ):
        item = export_sub.add_parser(name, help=help_text)
        item.add_argument("--apply", action="store_true", help="直接合并到客户端配置")
        item.set_defaults(func=cmd_export)

    storage_parser = sub.add_parser("storage", help="对象存储（S3 / OSS）")
    storage_sub = storage_parser.add_subparsers(dest="storage_cmd", required=True)
    storage_sub.add_parser("list", help="列出存储").set_defaults(func=cmd_storage)
    add_storage = storage_sub.add_parser("add", help="添加存储")
    add_storage.add_argument("name")
    add_storage.add_argument("--provider", default="s3", choices=["s3", "oss"])
    add_storage.add_argument("--bucket", required=True)
    add_storage.add_argument("--region")
    add_storage.add_argument("--endpoint", help="MinIO / 自建 S3 地址")
    add_storage.add_argument("--access-key", required=True)
    add_storage.add_argument("--prefix", default="opsmate")
    add_storage.set_defaults(func=cmd_storage)
    prune_storage = storage_sub.add_parser("prune", help="按份数清理远端旧对象")
    prune_storage.add_argument("ref")
    prune_storage.add_argument("--prefix", required=True, help="对象前缀，如 opsmate/2026/11/")
    prune_storage.add_argument("--match", help="只清理文件名包含该字符串的对象")
    prune_storage.add_argument("--keep", type=int, required=True)
    prune_storage.add_argument("--dry-run", action="store_true")
    prune_storage.set_defaults(func=cmd_storage)
    verify_storage = storage_sub.add_parser("verify", help="下载远端对象并比对 SHA-256")
    verify_storage.add_argument("ref")
    verify_storage.add_argument("file")
    verify_storage.add_argument("--key", help="对象键；不填按当前前缀推导")
    verify_storage.set_defaults(func=cmd_storage)
    rm_storage = storage_sub.add_parser("rm", help="删除存储")
    rm_storage.add_argument("ref")
    rm_storage.set_defaults(func=cmd_storage)

    dns_parser = sub.add_parser("dns", help="Cloudflare / 阿里云 DNS 解析")
    dns_sub = dns_parser.add_subparsers(dest="dns_cmd", required=True)
    creds = dns_sub.add_parser("creds", help="管理 DNS 凭据")
    creds_sub = creds.add_subparsers(dest="creds_cmd", required=True)
    creds_sub.add_parser("list", help="列出凭据").set_defaults(func=cmd_dns)
    add_cred = creds_sub.add_parser("add", help="添加凭据")
    add_cred.add_argument("name")
    add_cred.add_argument("--provider", default="cloudflare", choices=["cloudflare", "aliyun"])
    add_cred.set_defaults(func=cmd_dns)
    rm_cred = creds_sub.add_parser("rm", help="删除凭据")
    rm_cred.add_argument("ref")
    rm_cred.set_defaults(func=cmd_dns)

    for name, help_text in (("zones", "列出域名"), ("records", "列出解析")):
        item = dns_sub.add_parser(name, help=help_text)
        item.add_argument("cred")
        if name == "records":
            item.add_argument("zone")
        item.set_defaults(func=cmd_dns)

    add_rec = dns_sub.add_parser("add", help="添加解析")
    add_rec.add_argument("cred")
    add_rec.add_argument("zone")
    add_rec.add_argument("--name", required=True, help="主机记录，如 www / @")
    add_rec.add_argument("--type", default="A")
    add_rec.add_argument("--content", required=True)
    add_rec.add_argument("--ttl", type=int, default=600)
    add_rec.add_argument("--proxied", action="store_true", help="Cloudflare 代理")
    add_rec.add_argument("--line", default="default", help="阿里云线路")
    add_rec.add_argument("--priority", type=int)
    add_rec.set_defaults(func=cmd_dns)

    upd_rec = dns_sub.add_parser("update", help="修改解析")
    upd_rec.add_argument("cred")
    upd_rec.add_argument("zone")
    upd_rec.add_argument("record_id")
    upd_rec.add_argument("--name")
    upd_rec.add_argument("--type")
    upd_rec.add_argument("--content")
    upd_rec.add_argument("--ttl", type=int)
    upd_rec.add_argument("--proxied", dest="proxied", action="store_true", default=None)
    upd_rec.add_argument("--no-proxied", dest="proxied", action="store_false")
    upd_rec.add_argument("--line")
    upd_rec.set_defaults(func=cmd_dns)

    rm_rec = dns_sub.add_parser("rm", help="删除解析")
    rm_rec.add_argument("cred")
    rm_rec.add_argument("zone")
    rm_rec.add_argument("record_id")
    rm_rec.add_argument("--yes", action="store_true")
    rm_rec.set_defaults(func=cmd_dns)

    database = sub.add_parser("db", help="MySQL / PostgreSQL 备份与恢复")
    db_sub = database.add_subparsers(dest="db_cmd", required=True)
    db_sub.add_parser("list", help="列出数据库目标").set_defaults(func=cmd_db)
    add = db_sub.add_parser("add", help="添加数据库目标")
    add.add_argument("--name", required=True)
    add.add_argument("--engine", default="mysql", choices=["mysql", "postgres"])
    add.add_argument("--host")
    add.add_argument("--port", type=int)
    add.add_argument("--user")
    add.add_argument("--databases", help="逗号分隔；留空=全部")
    add.add_argument("--mode", default="auto", choices=["auto", "local", "docker"])
    add.add_argument("--image", help="docker 客户端镜像，如 mysql:8 / postgres:16")
    add.add_argument("--ask-password", action="store_true")
    add.add_argument("--ssh-host", help="通过哪台 SSH 主机做隧道（主机名或 ID）")
    add.add_argument("--every-hours", type=int, default=0, help="自动备份间隔，0=关闭")
    add.add_argument("--upload", help="自动备份后上传到哪个存储（ops storage 名称）")
    add.add_argument("--remote-keep", type=int, default=0, help="远端保留份数，0=不清理")
    add.add_argument("--keep", type=int, default=7, help="保留最近几份备份")
    add.set_defaults(func=cmd_db)
    for name, help_text in (("rm", "删除目标"), ("test", "测试连接"),
                            ("databases", "列出库"), ("backup", "备份")):
        item = db_sub.add_parser(name, help=help_text)
        item.add_argument("ref")
        if name == "backup":
            item.add_argument("--out", help="输出目录")
            item.add_argument("--encrypt", action="store_true", help="用口令加密备份")
            item.add_argument("--upload", help="备份后上传到对象存储（名称）")
            item.add_argument("--no-verify", action="store_true", help="上传后不做下载校验")
        item.set_defaults(func=cmd_db)
    restore = db_sub.add_parser("restore", help="覆盖式恢复")
    restore.add_argument("ref")
    restore.add_argument("file")
    restore.add_argument("--yes", action="store_true", help="确认覆盖")
    restore.add_argument("--no-safety", action="store_true", help="恢复前不拍快照")
    restore.set_defaults(func=cmd_db)

    drill = db_sub.add_parser("drill", help="恢复演练（快照→恢复→校验→报告）")
    drill.add_argument("ref")
    drill.add_argument("file")
    drill.set_defaults(func=cmd_db)

    autobackup = db_sub.add_parser("autobackup", help="执行到期的自动备份（给定时器调用）")
    autobackup.add_argument("--force", action="store_true", help="忽略间隔，全部备份")
    autobackup.set_defaults(func=cmd_db)

    schedule = db_sub.add_parser("schedule", help="设置自动备份间隔/保留份数")
    schedule.add_argument("ref")
    schedule.add_argument("--hours", type=int, required=True)
    schedule.add_argument("--keep", type=int, default=7)
    schedule.add_argument("--install", action="store_true", help="安装 systemd 用户定时器")
    schedule.set_defaults(func=cmd_db)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
