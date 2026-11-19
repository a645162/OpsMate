# 打包说明（Nuitka + NSIS）

## 依赖

```bash
uv sync --extra gui --extra build
```

* Windows：Python 3.10+、Visual Studio Build Tools（C 编译器）、NSIS（生成安装包）
* Linux：gcc、patchelf（Nuitka 会提示）

## 构建

```bash
# Windows
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1

# Linux
./packaging/build_linux.sh

# 只构建一个
uv run python packaging/build.py --gui
uv run python packaging/build.py --cli
```

产物：

```
dist/OpsMate.dist/   # 图形界面（Windows: OpsMate.exe，Linux: OpsMate.bin）
dist/ops.dist/       # 命令行（Windows: ops.exe，Linux: ops.bin）
```

## 生成 Windows 安装包

```powershell
winget install NSIS.NSIS
makensis packaging\installer.nsi
```

得到 `dist\OpsMate-Setup-0.1.0.exe`：

* 安装 GUI 到 `%PROGRAMFILES64%\OpsMate`
* 可选安装 CLI 到 `OpsMate\cli`，开始菜单提供 `OpsMate CLI` 入口
* 带卸载程序与“应用和功能”注册项

## 注意

* 两个入口分别打包，体积各自包含依赖；如需单文件可再加 `--onefile`，
  但首次启动会解压，PySide6 场景不推荐。
* 图标：把 `opsmate.ico` 放到 `packaging/` 下，构建脚本会自动带上（Windows）。
* 数据目录始终在用户目录（`%APPDATA%\opsmate`），安装/卸载不会动用户数据。
