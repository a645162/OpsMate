# OpsAssistant · 运维助手

> 一个本地优先的服务器运维台：把「看一眼 → 连上去 → 改一下」压缩到最短路径。
>
> 目前接入的第一个云厂商是**蓝队云（landui.com）**，通过第三方 SDK
> [`landui-sdk`](../LanduiSDK) 调用其控制台接口。
> **本项目与蓝队云官方无任何隶属、合作或授权关系**，接口来自公开网页前端整理，
> 可能随时变更。

## 为什么叫 OpsAssistant

「运维助手」直译是 Operations Assistant，取 **OpsAssistant**：
拼写直白、搜索友好，缩写成 `ops` 又好敲（命令就是 `ops`）。

## 现在能做什么

```bash
uv sync                 # 安装（会以 path 依赖方式装上隔壁的 LanduiSDK）
uv run ops doctor       # 自检：Python / SDK / 登录态 / ssh / 终端
uv run ops servers      # 列出云服务器
uv run ops ssh 10001    # 用已保存的账号打开 SSH
```

`doctor` 输出示例：

```
✅ Python        3.14.10
✅ landui-sdk    0.1.0
✅ 登录态        /home/you/.config/landui-sdk/auth.json
✅ ssh 客户端    /usr/bin/ssh
✅ 终端          /usr/bin/x-terminal-emulator
```

## 目录

```
src/opsassistant/
  cli.py        ops 命令（version / doctor / servers / ssh）
  doctor.py     环境自检（纯函数，便于复用到 GUI）
  landui.py     蓝队云接入的薄封装（错误翻译成人话）
  config.py     ~/.config/opsassistant/ 与 settings.json
tests/          离线测试
```

## 与 LanduiSDK 的关系

* 登录态、SSH 账号、密码都复用 `~/.config/landui-sdk/`，**不复制一份**，
  避免同一份凭据散落多处。
* 依赖通过 `pyproject.toml` 的 `[tool.uv.sources]` 指向 `../../LanduiSDK`；
  等 SDK 发布到 PyPI 后删掉那段即可。

## 路线图

- [ ] 图形界面：服务器列表 + 一键 SSH（复用 LanduiSDK 的组件）
- [ ] 多厂商抽象：`Provider` 接口，蓝队云只是第一个实现
- [ ] 批量操作：多选开关机、批量执行命令
- [ ] 到期/欠费提醒
- [ ] 本地凭据迁移到系统钥匙串

## 开发

```bash
uv run pytest -q
uv run ruff check src tests
```

## 许可

MIT。第三方接口的使用风险与合规责任由使用者承担。
