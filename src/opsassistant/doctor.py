"""环境自检：装没装好、登录态在不在、能不能拉起 SSH。

设计成纯函数 + 数据返回，方便 CLI 打印，也方便以后塞进 GUI。
"""

from __future__ import annotations

import shutil
import stat
import sys
from dataclasses import dataclass

from opsassistant.config import landui_auth_path

OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass
class Check:
    name: str
    status: str          # ok / warn / fail
    detail: str = ""

    @property
    def icon(self) -> str:
        return {OK: "✅", WARN: "⚠", FAIL: "❌"}.get(self.status, "·")


def check_python() -> Check:
    version = sys.version_info
    text = f"{version.major}.{version.minor}.{version.micro}"
    if version >= (3, 10):
        return Check("Python", OK, text)
    return Check("Python", FAIL, f"{text}（需要 >= 3.10）")


def check_sdk() -> Check:
    try:
        import landui_sdk
    except ImportError as exc:  # pragma: no cover - 装好依赖时不会走到
        return Check("landui-sdk", FAIL, f"未安装：{exc}")
    return Check("landui-sdk", OK, getattr(landui_sdk, "__version__", "unknown"))


def check_auth() -> Check:
    path = landui_auth_path()
    if not path.exists():
        return Check("登录态", FAIL, f"缺失：{path}（先在 LanduiSDK 里登录一次）")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        return Check("登录态", WARN, f"{path} 权限 {oct(mode)}，建议 chmod 600")
    return Check("登录态", OK, str(path))


def check_ssh() -> Check:
    path = shutil.which("ssh")
    return Check("ssh 客户端", OK, path) if path else Check("ssh 客户端", FAIL, "PATH 里没有 ssh")


def check_terminal() -> Check:
    from landui_sdk.ssh import detect_terminal

    found = detect_terminal()
    if found is None:
        return Check("终端", WARN, "没探测到终端程序（CLI 可用，GUI 一键 SSH 需要）")
    return Check("终端", OK, found[0])


def check_login_online() -> Check:
    """真的打一次控制台接口，确认 Cookie 没过期。"""
    from opsassistant import landui

    try:
        ok = landui.client().is_logged_in()
    except Exception as exc:  # noqa: BLE001 - 自检不应崩
        return Check("在线登录态", FAIL, str(exc))
    return Check("在线登录态", OK, "有效") if ok else Check("在线登录态", FAIL, "已过期，需要重新登录")


def run_checks(online: bool = False) -> list[Check]:
    checks = [
        check_python(),
        check_sdk(),
        check_auth(),
        check_ssh(),
        check_terminal(),
    ]
    if online:
        checks.append(check_login_online())
    return checks


def summarize(checks: list[Check]) -> tuple[bool, str]:
    """返回 (是否可用, 一句话总结)。"""
    fails = [c for c in checks if c.status == FAIL]
    warns = [c for c in checks if c.status == WARN]
    if fails:
        return False, f"{len(fails)} 项不可用，{len(warns)} 项警告"
    if warns:
        return True, f"基本可用，{len(warns)} 项警告"
    return True, "一切正常"


__all__ = [
    "FAIL",
    "OK",
    "WARN",
    "Check",
    "check_auth",
    "check_login_online",
    "check_python",
    "check_sdk",
    "check_ssh",
    "check_terminal",
    "run_checks",
    "summarize",
]
