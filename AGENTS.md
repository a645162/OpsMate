# AGENTS.md

`OpsAssistant`（运维助手）—— 本地服务器运维台。当前只有 CLI 与自检，
后续会加 GUI 和多厂商支持。

## 红线

1. **不提交凭据**。登录态/SSH 密码都在 `~/.config/landui-sdk/`，
   本仓库只允许出现 `~/.config/opsassistant/settings.json` 这类非敏感配置。
   提交前：

   ```bash
   git diff --cached --name-only | xargs -r grep -nEi \
     'password|secret|token|PHPSESSID|/home/' || true
   ```

2. **不要复制 SDK 的登录态**到本仓库或本项目配置目录。用
   `opsassistant.landui.client()` 读 `~/.config/landui-sdk/auth.json`。
3. 测试**不联网**；所有外部调用都要能注入假对象。
4. 文案中文，命令/路径/错误原文保持。

## 结构约定

```
src/opsassistant/
  cli.py        argparse 子命令；每个命令一个 cmd_* 函数，返回 int
  doctor.py     Check 数据类 + 纯函数检查；不要在里面 print
  landui.py     SDK 适配层；UI/CLI 不直接 import landui_sdk 的业务类
  config.py     XDG 风格路径，支持 OPSASSISTANT_HOME 覆盖（测试用）
```

* 新增命令：在 `build_parser()` 注册，写 `cmd_xxx(args) -> int`，加离线测试。
* 新增检查项：写 `check_xxx() -> Check`，加进 `run_checks()`。
* 以后加多厂商：定义 `Provider` 协议（list_servers/get_server/ssh_target），
  蓝队云实现放 `providers/landui.py`，不要把 SDK 类型泄漏到 CLI/GUI。

## 命令

```bash
uv sync
uv run pytest -q
uv run ruff check src tests
uv run ops doctor
```

## 依赖

`landui-sdk` 目前通过 `[tool.uv.sources]` 指向 `../../LanduiSDK`（可编辑安装）。
若移动了仓库位置，同步改这一行；发布到 PyPI 后删除该配置并改成版本号。
