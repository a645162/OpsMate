"""路径与本地状态。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

#: 本项目的配置目录（可用环境变量覆盖，方便测试）
HOME_ENV = "OPSASSISTANT_HOME"
DEFAULT_HOME = "~/.config/opsassistant"

#: 蓝队云 SDK 默认把登录态放在这里，直接复用，避免多存一份 Cookie
LANDUI_AUTH = "~/.config/landui-sdk/auth.json"


def home() -> Path:
    return Path(os.environ.get(HOME_ENV, DEFAULT_HOME)).expanduser()


def ensure_home() -> Path:
    path = home()
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return home() / "settings.json"


def landui_auth_path() -> Path:
    return Path(LANDUI_AUTH).expanduser()


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
