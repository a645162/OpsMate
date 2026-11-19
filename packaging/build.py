#!/usr/bin/env python3
"""用 Nuitka 打包 OpsMate（Windows / Linux）。

    uv run --extra gui --extra build python packaging/build.py --all
    uv run --extra gui --extra build python packaging/build.py --gui
    uv run --extra build python packaging/build.py --cli

产物在 dist/：
  Windows: OpsMate.dist/OpsMate.exe + ops.dist/ops.exe
  Linux:   OpsMate.dist/OpsMate.bin + ops.dist/ops.bin
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def build(target: str, *, console: bool) -> None:
    entry = ROOT / "packaging" / f"launcher_{target}.py"
    name = "OpsMate" if target == "gui" else "ops"
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        # 系统 Python 常常没有静态 libpython，用动态链接即可
        "--static-libpython=no",
        "--enable-plugin=pyside6" if target == "gui" else "--nofollow-import-to=opsmate.gui",
        "--include-package=opsmate",
        "--include-package=landui_sdk",
        f"--output-dir={DIST}",
        f"--output-filename={name}",
        "--remove-output",
    ]
    if sys.platform == "win32":
        cmd.append("--windows-console-mode=force" if console else "--windows-console-mode=disable")
        if target == "gui" and (ROOT / "packaging" / "opsmate.ico").exists():
            cmd.append(f"--windows-icon-from-ico={ROOT / 'packaging' / 'opsmate.ico'}")
    run([*cmd, str(entry)])

    produced = DIST / f"launcher_{target}.dist"
    final = DIST / f"{name}.dist"
    if produced.exists():
        if final.exists():
            shutil.rmtree(final)
        produced.rename(final)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--cli", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if not (args.gui or args.cli or args.all):
        parser.error("至少选一个：--gui / --cli / --all")

    DIST.mkdir(exist_ok=True)
    if args.all or args.gui:
        build("gui", console=False)
    if args.all or args.cli:
        build("cli", console=True)

    print("\n打包完成，产物在", DIST)
    print("Windows 生成安装包：makensis packaging\\installer.nsi")
    print("Linux 直接运行：dist/OpsMate.dist/OpsMate.bin 或 dist/ops.dist/ops.bin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
