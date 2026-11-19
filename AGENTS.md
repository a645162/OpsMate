# AGENTS.md

`OpsMate`（运维助手）—— 本地优先的服务器运维台。
覆盖：主机清单、SSH 密钥、`~/.ssh/config`、Tabby / Windows Terminal 配置、
MySQL/PostgreSQL 备份恢复，以及通过子模块 `libs/LanduiSDK` 接入蓝队云。

**第三方声明**：本项目与蓝队云官方无关；不要在任何产物里删掉这层说明。

## 红线

1. **不提交任何凭据**：`~/.config/opsmate/`（hosts/databases 里可能有密码）、
   SSH 私钥、蓝队云登录态。提交前：

   ```bash
   git diff --cached --name-only | xargs -r grep -nEi \
     'password|secret|token|PRIVATE KEY|PHPSESSID|/home/' || true
   ```

2. **破坏性操作必须显式确认**：数据库恢复要求 `confirm=True`/`--yes`，
   GUI 有二次弹窗；删除密钥/主机同样要求确认。
3. **改别人的配置前先备份**：`~/.ssh/config`、Tabby、Windows Terminal 一律
   走 `config.backup_file` / 备份目录；不要静默覆盖。
4. **不在命令行参数里放密码**：数据库用 `MYSQL_PWD`/`PGPASSWORD`（docker 用
   `-e VAR` 继承环境），SSH 用 askpass。
5. 测试不联网；数据库真实测试放 `scripts/db_docker_test.py`，不进默认 pytest。

## 跨平台

路径统一走 `opsmate/config.py`（Windows 用 `%APPDATA%`/`%LOCALAPPDATA%`，
macOS 用 `Application Support`，Linux 用 XDG）。新增路径不要写死 `~/.config`。
`ssh-keygen` 在 Windows 上可能不在 PATH，用 `sshkeys.find_tool()`。

## 结构

```
src/opsmate/
  cli.py         ops 子命令（argparse，每个 cmd_* 返回 int）
  inventory.py   主机清单（JSON, 0600），aliases() 生成唯一 ssh alias
  sshkeys.py     密钥列出/生成/装公钥/传 GitHub
  sshconfig.py   ~/.ssh/config 解析、托管块、备份、原子写入
  terminals.py   Tabby / Windows Terminal 片段与合并
  db.py          MySQL/PostgreSQL 备份恢复（local/docker runner、SSH 隧道、定时/保留、恢复前快照）
  config.py      跨平台路径与私有 JSON
  doctor.py      自检（纯函数返回 Check，不 print）
  gui/app.py     PySide6；线程用 run_async，禁止主线程发请求
tests/           离线测试
scripts/db_docker_test.py  真实容器验证（备份→破坏→恢复）
libs/LanduiSDK   git submodule，蓝队云 SDK
```

## 开发命令

```bash
uv sync --extra gui
uv run pytest -q
uv run ruff check src tests
uv run ops doctor
uv run ops-gui
uv run python scripts/db_docker_test.py
```

## 约定

- 新增外部配置写入：先备份，再写，权限 0600（`config.write_private`）。
- 新增命令：`build_parser()` 注册 + `cmd_xxx(args) -> int` + 离线测试。
- 数据库新增引擎：实现 backup/restore/test/list 四件事，密码走环境变量。
- 恢复类操作默认走 `restore_with_safety()`；新增入口不要绕过它（除非显式 `--no-safety`）。
- 定时任务只调用 `db.autobackup()`，它会按 `schedule_hours` 判断到期并 `prune(keep)`；
  不要在别处重复实现清理逻辑。
- GUI 操作耗时内容（备份/恢复/导入）一律 `run_async`，结果回 UI 线程。
- 文案中文；命令、路径、报错原文保持。
