"""蓝队云接入层的薄封装：把 SDK 的错误翻译成人话。"""

from __future__ import annotations

import os

from landui_sdk import LanduiClient, LanduiError, NotLoggedInError

from opsmate.config import landui_auth_path


def client(auth_file: str | os.PathLike | None = None) -> LanduiClient:
    """按登录态文件创建客户端；没有登录态时给出可操作提示。"""
    path = landui_auth_path() if auth_file is None else auth_file
    if not os.path.exists(path):
        raise RuntimeError(
            f"没有找到登录态：{path}\n"
            "先在 LanduiSDK 里登录一次（图形界面或 `landui login`），再回来。"
        )
    instance = LanduiClient()
    instance.load_auth(path)
    return instance


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, NotLoggedInError):
        return (
            "登录态已失效：先在 LanduiSDK 里重新登录"
            "（图形界面，或 `landui login --credentials`），再回来。"
        )
    if isinstance(exc, LanduiError):
        return f"蓝队云接口出错：{exc}"
    return f"{type(exc).__name__}: {exc}"
