"""路径与本地状态（Windows / macOS / Linux 通用）。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

#: 本项目的配置目录（可用环境变量覆盖，方便测试）
HOME_ENV = "OPSMATE_HOME"
APP_DIR = "opsmate"

#: 蓝队云 SDK 默认把登录态放在这里，直接复用，避免多存一份 Cookie
LANDUI_AUTH = "~/.config/landui-sdk/auth.json"


def _default_home() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / APP_DIR
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_DIR


def home() -> Path:
    override = os.environ.get(HOME_ENV)
    return Path(override).expanduser() if override else _default_home()


def ensure_home() -> Path:
    path = home()
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return home() / "settings.json"


def hosts_path() -> Path:
    return home() / "hosts.json"


def db_targets_path() -> Path:
    return home() / "databases.json"


def backups_dir() -> Path:
    path = home() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def exports_dir() -> Path:
    path = home() / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def landui_auth_path() -> Path:
    return Path(LANDUI_AUTH).expanduser()


# ----------------------------------------------------------------- 外部配置
def ssh_dir() -> Path:
    return Path.home() / ".ssh"


def ssh_config_path() -> Path:
    return ssh_dir() / "config"


def tabby_config_candidates() -> list[Path]:
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return [appdata / "tabby" / "config.yaml"]
    if sys.platform == "darwin":
        return [Path.home() / "Library" / "Application Support" / "tabby" / "config.yaml"]
    return [Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "tabby" / "config.yaml"]


def windows_terminal_candidates() -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return [
        local / "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState" / "settings.json",
        local / "Packages" / "Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe" / "LocalState" / "settings.json",
        local / "Microsoft" / "Windows Terminal" / "settings.json",
    ]


def first_existing(paths: list[Path]) -> Path | None:
    return next((p for p in paths if p.exists()), None)


# ------------------------------------------------------------------- 配置 io
def load_settings() -> dict[str, Any]:
    path = settings_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(data: dict[str, Any]) -> Path:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def write_private(path: Path, data: Any) -> Path:
    """写 JSON 并把权限收到 0600。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default
