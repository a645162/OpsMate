"""命令行入口：``ops <命令>``。"""

from __future__ import annotations

import argparse
import sys

from opsassistant import APP_NAME, APP_NAME_ZH, __version__


def _print_checks(checks) -> None:
    width = max(len(c.name) for c in checks)
    for check in checks:
        print(f"{check.icon} {check.name.ljust(width)}  {check.detail}")


def cmd_version(_args: argparse.Namespace) -> int:
    print(f"{APP_NAME}（{APP_NAME_ZH}） {__version__}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from opsassistant.doctor import run_checks, summarize

    checks = run_checks(online=args.online)
    _print_checks(checks)
    ok, text = summarize(checks)
    print()
    print(("✅ " if ok else "❌ ") + text)
    return 0 if ok else 1


def cmd_servers(_args: argparse.Namespace) -> int:
    from opsassistant import landui

    try:
        client = landui.client()
        servers = client.cloud.all_servers()
    except Exception as exc:  # noqa: BLE001 - CLI 只打印人话
        print(landui.friendly_error(exc), file=sys.stderr)
        return 1

    if not servers:
        print("账号下没有云服务器")
        return 0

    rows = [
        (str(s.id), s.name, s.status, s.ip, s.cpu_mem, s.expire_at) for s in servers
    ]
    headers = ("ID", "名称", "状态", "公网IP", "规格", "到期")
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("-" * (sum(widths) + 2 * (len(headers) - 1)))
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return 0


def cmd_ssh(args: argparse.Namespace) -> int:
    from landui_sdk.config import AppConfig
    from landui_sdk.ssh import SshTarget, launch_ssh

    from opsassistant import landui

    try:
        client = landui.client()
        detail = client.cloud.get_server(args.id)
        cfg = AppConfig.load()
        profile = cfg.get(args.id)
        account = profile.account(args.user)
        if account is None:
            print("还没配置 SSH 账号，先用 LanduiSDK 的图形界面加一个", file=sys.stderr)
            return 1
        target = SshTarget(
            host=detail.ip,
            port=account.port,
            user=account.user,
            password=account.password,
            key_path=account.key_path,
            key_passphrase=account.key_passphrase,
        )
        cmd = launch_ssh(target, args.launcher or cfg.ssh_launcher,
                         custom=cfg.ssh_launcher_custom)
    except Exception as exc:  # noqa: BLE001
        print(landui.friendly_error(exc), file=sys.stderr)
        return 1
    print(f"[{account.user}@{detail.ip}:{account.port}] {cmd}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ops",
        description=f"{APP_NAME}（{APP_NAME_ZH}）—— 本地服务器运维助手",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="显示版本").set_defaults(func=cmd_version)

    doctor = sub.add_parser("doctor", help="环境与依赖自检")
    doctor.add_argument("--online", action="store_true", help="额外验证登录态是否有效（会联网）")
    doctor.set_defaults(func=cmd_doctor)

    sub.add_parser("servers", help="列出云服务器").set_defaults(func=cmd_servers)

    ssh = sub.add_parser("ssh", help="用已保存的账号打开 SSH")
    ssh.add_argument("id", type=int, help="服务器 ID")
    ssh.add_argument("--user", help="指定 SSH 账号（多账号时）")
    ssh.add_argument("--launcher", help="覆盖打开方式：auto/tabby/gnome-terminal…")
    ssh.set_defaults(func=cmd_ssh)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
