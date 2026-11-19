"""PySide6 界面：服务器 / 密钥 / SSH 配置 / 导出 / 数据库。"""

from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from opsmate import __version__, config, db, sshconfig, sshkeys, terminals
from opsmate.inventory import HostEntry, Inventory


# --------------------------------------------------------------------- 线程
class _Signals(QObject):
    done = Signal(object)
    error = Signal(str)


class Task(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as exc:  # noqa: BLE001
            self.signals.error.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.done.emit(result)


def run_async(parent: QObject, fn, ok, err) -> None:
    task = Task(fn)
    task.signals.done.connect(ok)
    task.signals.error.connect(err)
    keep = getattr(parent, "_bg_tasks", None)
    if keep is None:
        keep = []
        parent._bg_tasks = keep
    keep.append(task)

    def cleanup(*_):
        try:
            keep.remove(task)
        except ValueError:
            pass

    task.signals.done.connect(cleanup)
    task.signals.error.connect(cleanup)
    QThreadPool.globalInstance().start(task)


def err_box(parent: QWidget, exc: str) -> None:
    QMessageBox.critical(parent, "出错了", exc)


# --------------------------------------------------------------------- 对话框
class HostDialog(QDialog):
    def __init__(self, host: HostEntry | None = None, parent=None):
        super().__init__(parent)
        self.host = host or HostEntry(name="", host="")
        self.setWindowTitle("编辑主机" if host else "添加主机")
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        self.ed_name = QLineEdit(self.host.name)
        self.ed_host = QLineEdit(self.host.host)
        self.sp_port = QSpinBox()
        self.sp_port.setRange(1, 65535)
        self.sp_port.setValue(self.host.port or 22)
        self.ed_user = QLineEdit(self.host.user or "root")
        self.cb_auth = QComboBox()
        for key, label in (("agent", "SSH Agent / 默认私钥"), ("key", "指定私钥"), ("password", "密码")):
            self.cb_auth.addItem(label, key)
        self.cb_auth.setCurrentIndex(max(0, self.cb_auth.findData(self.host.auth)))
        self.ed_key = QLineEdit(self.host.key_path)
        self.ed_key.setPlaceholderText("~/.ssh/id_ed25519")
        btn_pick = QPushButton("浏览…")
        key_row = QHBoxLayout()
        key_row.addWidget(self.ed_key, 1)
        key_row.addWidget(btn_pick)
        self.ed_password = QLineEdit(self.host.password)
        self.ed_password.setEchoMode(QLineEdit.Password)
        self.ed_tags = QLineEdit(",".join(self.host.tags))
        self.ed_note = QLineEdit(self.host.note)

        form.addRow("名称", self.ed_name)
        form.addRow("地址", self.ed_host)
        form.addRow("端口", self.sp_port)
        form.addRow("用户", self.ed_user)
        form.addRow("认证", self.cb_auth)
        form.addRow("私钥", key_row)
        form.addRow("密码", self.ed_password)
        form.addRow("标签", self.ed_tags)
        form.addRow("备注", self.ed_note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        form.addRow(buttons)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        btn_pick.clicked.connect(self._pick_key)

    def _pick_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择私钥", str(config.ssh_dir()))
        if path:
            self.ed_key.setText(path)

    def _on_ok(self) -> None:
        if not self.ed_name.text().strip():
            self.ed_name.setText(self.ed_host.text().strip())
        if not self.ed_host.text().strip():
            QMessageBox.warning(self, "检查一下", "地址不能为空")
            return
        self.accept()

    def value(self) -> HostEntry:
        self.host.name = self.ed_name.text().strip() or self.ed_host.text().strip()
        self.host.host = self.ed_host.text().strip()
        self.host.port = self.sp_port.value()
        self.host.user = self.ed_user.text().strip() or "root"
        self.host.auth = self.cb_auth.currentData()
        self.host.key_path = self.ed_key.text().strip()
        self.host.password = self.ed_password.text()
        self.host.tags = [t.strip() for t in self.ed_tags.text().split(",") if t.strip()]
        self.host.note = self.ed_note.text().strip()
        return self.host


class KeyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("生成 SSH 密钥")
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        self.ed_name = QLineEdit("id_ed25519")
        self.cb_type = QComboBox()
        self.cb_type.addItems(["ed25519", "rsa", "ecdsa"])
        self.sp_bits = QSpinBox()
        self.sp_bits.setRange(0, 8192)
        self.sp_bits.setValue(0)
        self.sp_bits.setSpecialValueText("默认")
        self.ed_comment = QLineEdit(f"opsmate@{os_hostname()}")
        self.ed_pass = QLineEdit()
        self.ed_pass.setEchoMode(QLineEdit.Password)
        self.ed_pass.setPlaceholderText("留空表示无口令")
        self.lbl_path = QLabel(str(config.ssh_dir() / "id_ed25519"))
        self.lbl_path.setStyleSheet("color:#888")

        form.addRow("文件名", self.ed_name)
        form.addRow("类型", self.cb_type)
        form.addRow("位数", self.sp_bits)
        form.addRow("注释", self.ed_comment)
        form.addRow("私钥口令", self.ed_pass)
        form.addRow("路径", self.lbl_path)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("生成")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        form.addRow(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.ed_name.textChanged.connect(self._update_path)
        self.cb_type.currentTextChanged.connect(self._update_path)

    def _update_path(self) -> None:
        name = self.ed_name.text().strip() or "id_ed25519"
        self.lbl_path.setText(str(config.ssh_dir() / name))

    def values(self) -> dict:
        return {
            "name": self.ed_name.text().strip(),
            "key_type": self.cb_type.currentText(),
            "bits": self.sp_bits.value() or None,
            "comment": self.ed_comment.text().strip(),
            "passphrase": self.ed_pass.text(),
        }


def os_hostname() -> str:
    import socket

    return socket.gethostname()


class DbDialog(QDialog):
    def __init__(self, target: db.DbTarget | None = None, parent=None):
        super().__init__(parent)
        self.target = target
        self.setWindowTitle("编辑数据库" if target else "添加数据库")
        self.setMinimumWidth(500)
        form = QFormLayout(self)
        self.ed_name = QLineEdit(target.name if target else "")
        self.cb_engine = QComboBox()
        self.cb_engine.addItems(["mysql", "postgres"])
        if target:
            self.cb_engine.setCurrentText(target.engine)
        self.ed_host = QLineEdit(target.host if target else "127.0.0.1")
        self.sp_port = QSpinBox()
        self.sp_port.setRange(0, 65535)
        self.sp_port.setValue(target.port if target else 0)
        self.sp_port.setSpecialValueText("默认")
        self.ed_user = QLineEdit(target.user if target else "root")
        self.ed_password = QLineEdit(target.password if target else "")
        self.ed_password.setEchoMode(QLineEdit.Password)
        self.ed_databases = QLineEdit(",".join(target.databases) if target else "")
        self.ed_databases.setPlaceholderText("留空=全部数据库")
        self.cb_mode = QComboBox()
        for key, label in (("auto", "自动（有本地客户端就用，否则 Docker）"),
                           ("local", "只用本机客户端"), ("docker", "只用 Docker")):
            self.cb_mode.addItem(label, key)
        if target:
            self.cb_mode.setCurrentIndex(max(0, self.cb_mode.findData(target.client_mode)))
        self.ed_image = QLineEdit(target.image if target else "")
        self.ed_image.setPlaceholderText("mysql:8 / postgres:16")

        form.addRow("名称", self.ed_name)
        form.addRow("类型", self.cb_engine)
        form.addRow("地址", self.ed_host)
        form.addRow("端口", self.sp_port)
        form.addRow("用户", self.ed_user)
        form.addRow("密码", self.ed_password)
        form.addRow("数据库", self.ed_databases)
        form.addRow("客户端", self.cb_mode)
        form.addRow("Docker 镜像", self.ed_image)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        form.addRow(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

    def value(self) -> db.DbTarget:
        return db.DbTarget(
            id=self.target.id if self.target else db.new_id(),
            name=self.ed_name.text().strip() or "database",
            engine=self.cb_engine.currentText(),
            host=self.ed_host.text().strip() or "127.0.0.1",
            port=self.sp_port.value(),
            user=self.ed_user.text().strip() or "root",
            password=self.ed_password.text(),
            databases=[d.strip() for d in self.ed_databases.text().split(",") if d.strip()],
            client_mode=self.cb_mode.currentData(),
            image=self.ed_image.text().strip(),
        )


# --------------------------------------------------------------------- 主窗口
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"OpsMate · 运维助手 {__version__}（非官方第三方）")
        self.resize(1100, 720)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        self.tabs = tabs
        tabs.addTab(self._build_hosts_tab(), "服务器")
        tabs.addTab(self._build_keys_tab(), "SSH 密钥")
        tabs.addTab(self._build_config_tab(), "SSH 配置")
        tabs.addTab(self._build_export_tab(), "导出")
        tabs.addTab(self._build_db_tab(), "数据库")

        self.statusBar().showMessage("就绪")
        self.reload_hosts()
        self.reload_keys()
        self.reload_db()

    # ------------------------------------------------------------------ 主机
    def _build_hosts_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        bar = QHBoxLayout()
        for text, slot in (
            ("添加", self.add_host),
            ("编辑", self.edit_host),
            ("删除", self.delete_host),
            ("从蓝队云导入", self.import_landui),
            ("打开 SSH", self.ssh_host),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            bar.addWidget(btn)
        bar.addStretch(1)
        self.lbl_hosts = QLabel("")
        bar.addWidget(self.lbl_hosts)
        v.addLayout(bar)

        self.tbl_hosts = QTableWidget(0, 7)
        self.tbl_hosts.setHorizontalHeaderLabels(
            ["名称", "地址", "端口", "用户", "认证", "标签", "备注"]
        )
        self.tbl_hosts.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_hosts.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_hosts.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_hosts.setAlternatingRowColors(True)
        self.tbl_hosts.doubleClicked.connect(lambda _i: self.ssh_host())
        v.addWidget(self.tbl_hosts, 1)
        return page

    def reload_hosts(self) -> None:
        self.inventory = Inventory.load()
        hosts = self.inventory.hosts
        self.tbl_hosts.setRowCount(len(hosts))
        for row, host in enumerate(hosts):
            values = [
                host.name, host.host, str(host.port), host.user, host.auth,
                ",".join(host.tags), host.note,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, host.id)
                self.tbl_hosts.setItem(row, col, item)
        self.lbl_hosts.setText(f"{len(hosts)} 台")

    def _selected_host(self) -> HostEntry | None:
        rows = self.tbl_hosts.selectionModel().selectedRows()
        if not rows:
            return None
        host_id = self.tbl_hosts.item(rows[0].row(), 0).data(Qt.UserRole)
        return self.inventory.by_id(host_id)

    def add_host(self) -> None:
        dlg = HostDialog(parent=self)
        if dlg.exec() == QDialog.Accepted:
            self.inventory.upsert(dlg.value())
            self.reload_hosts()

    def edit_host(self) -> None:
        host = self._selected_host()
        if host is None:
            return
        dlg = HostDialog(host, self)
        if dlg.exec() == QDialog.Accepted:
            self.inventory.upsert(dlg.value())
            self.reload_hosts()

    def delete_host(self) -> None:
        host = self._selected_host()
        if host is None:
            return
        if QMessageBox.question(self, "删除", f"删除 {host.name}？") != QMessageBox.Yes:
            return
        self.inventory.remove(host.id)
        self.reload_hosts()

    def import_landui(self) -> None:
        from opsmate import landui

        def job():
            servers = landui.client().cloud.all_servers()
            inventory = Inventory.load()
            return inventory.import_landui(servers)

        def done(result):
            added, updated = result
            self.reload_hosts()
            self.statusBar().showMessage(f"导入完成：新增 {added}，更新 {updated}")

        run_async(self, job, done, lambda m: err_box(self, m))

    def ssh_host(self) -> None:
        host = self._selected_host()
        if host is None:
            return
        from landui_sdk.ssh import launch_ssh

        try:
            cmd = launch_ssh(host.target(), "auto")
        except Exception as exc:  # noqa: BLE001
            err_box(self, f"{type(exc).__name__}: {exc}")
            return
        self.statusBar().showMessage(f"已打开：{cmd[:80]}")

    # ------------------------------------------------------------------ 密钥
    def _build_keys_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        bar = QHBoxLayout()
        for text, slot in (
            ("新建密钥", self.new_key),
            ("复制公钥", self.copy_public_key),
            ("安装到服务器", self.install_key),
            ("上传到 GitHub", self.upload_key),
            ("删除", self.delete_key),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            bar.addWidget(btn)
        bar.addStretch(1)
        v.addLayout(bar)

        self.tbl_keys = QTableWidget(0, 4)
        self.tbl_keys.setHorizontalHeaderLabels(["名称", "类型", "指纹", "注释"])
        self.tbl_keys.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_keys.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_keys.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_keys.setAlternatingRowColors(True)
        v.addWidget(self.tbl_keys, 1)

        v.addWidget(QLabel("公钥（可直接全选复制）："))
        self.txt_pub = QPlainTextEdit()
        self.txt_pub.setReadOnly(True)
        self.txt_pub.setMaximumHeight(110)
        v.addWidget(self.txt_pub)
        self.tbl_keys.itemSelectionChanged.connect(self._show_pubkey)
        return page

    def reload_keys(self) -> None:
        self.keys = sshkeys.list_keys()
        self.tbl_keys.setRowCount(len(self.keys))
        for row, key in enumerate(self.keys):
            for col, value in enumerate(
                [key.name, key.key_type, key.fingerprint, key.comment]
            ):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, key.name)
                self.tbl_keys.setItem(row, col, item)

    def _selected_key(self):
        rows = self.tbl_keys.selectionModel().selectedRows()
        if not rows:
            return None
        name = self.tbl_keys.item(rows[0].row(), 0).data(Qt.UserRole)
        return next((k for k in self.keys if k.name == name), None)

    def _show_pubkey(self) -> None:
        key = self._selected_key()
        self.txt_pub.setPlainText(key.public_text() if key else "")

    def new_key(self) -> None:
        dlg = KeyDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        values = dlg.values()
        try:
            key = sshkeys.generate_key(**values)
        except Exception as exc:  # noqa: BLE001
            err_box(self, str(exc))
            return
        self.reload_keys()
        self.statusBar().showMessage(f"已生成 {key.private_path}")

    def copy_public_key(self) -> None:
        key = self._selected_key()
        if key is None:
            return
        QApplication.clipboard().setText(key.public_text())
        self.statusBar().showMessage(f"已复制 {key.name}.pub")

    def install_key(self) -> None:
        key = self._selected_key()
        if key is None:
            return
        hosts = Inventory.load().hosts
        if not hosts:
            err_box(self, "还没有主机，先在「服务器」里添加")
            return
        names = [h.name for h in hosts]
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getItem(self, "安装公钥", "选择服务器：", names, 0, False)
        if not ok:
            return
        host = next(h for h in hosts if h.name == name)
        run_async(
            self,
            lambda: sshkeys.install_public_key(host, key.public_text()),
            lambda msg: self.statusBar().showMessage(f"{host.name}：{msg}"),
            lambda m: err_box(self, m),
        )

    def upload_key(self) -> None:
        key = self._selected_key()
        if key is None:
            return
        from PySide6.QtWidgets import QInputDialog

        default = f"{os_hostname()}-{key.name}"
        title, ok = QInputDialog.getText(self, "上传到 GitHub", "Key 标题：", text=default)
        if not ok or not title.strip():
            return
        run_async(
            self,
            lambda: sshkeys.upload_to_github(key.public_path, title.strip()),
            lambda msg: QMessageBox.information(self, "GitHub", msg or "已上传"),
            lambda m: err_box(self, m),
        )

    def delete_key(self) -> None:
        key = self._selected_key()
        if key is None:
            return
        if QMessageBox.question(
            self, "删除密钥", f"删除 {key.name}（含私钥）？不可恢复"
        ) != QMessageBox.Yes:
            return
        sshkeys.delete_key(key)
        self.reload_keys()

    # ------------------------------------------------------------- ssh config
    def _build_config_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        bar = QHBoxLayout()
        self.lbl_config = QLabel(str(config.ssh_config_path()))
        bar.addWidget(self.lbl_config, 1)
        for text, slot in (
            ("重新加载", self.load_config),
            ("保存（自动备份）", self.save_config),
            ("立即备份", self.backup_config),
            ("同步主机清单", self.sync_config),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            bar.addWidget(btn)
        v.addLayout(bar)

        self.txt_config = QPlainTextEdit()
        self.txt_config.setPlaceholderText("~/.ssh/config 内容")
        v.addWidget(self.txt_config, 1)
        self.load_config()
        return page

    def load_config(self) -> None:
        self.txt_config.setPlainText(sshconfig.read_config())

    def save_config(self) -> None:
        try:
            sshconfig.write_config(self.txt_config.toPlainText())
        except Exception as exc:  # noqa: BLE001
            err_box(self, str(exc))
            return
        self.statusBar().showMessage("已保存（原文件已备份）")

    def backup_config(self) -> None:
        backup = sshconfig.backup_file(config.ssh_config_path())
        self.statusBar().showMessage(f"已备份：{backup}" if backup else "文件不存在")

    def sync_config(self) -> None:
        backup, _ = sshconfig.sync()
        self.load_config()
        self.statusBar().showMessage(
            f"已同步（备份：{backup.name}）" if backup else "已同步（内容无变化）"
        )

    # ---------------------------------------------------------------- 导出
    def _build_export_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.addWidget(QLabel("把「服务器」清单推送到常用工具："))

        for title, export_slot, apply_slot in (
            ("~/.ssh/config（所有工具通用）", self.sync_config, None),
            ("Tabby", self.export_tabby, self.apply_tabby),
            ("Windows Terminal", self.export_wt, self.apply_wt),
        ):
            box = QGroupBox(title)
            row = QHBoxLayout(box)
            b1 = QPushButton("导出片段")
            b1.clicked.connect(export_slot)
            row.addWidget(b1)
            if apply_slot:
                b2 = QPushButton("直接写入配置（先备份）")
                b2.clicked.connect(apply_slot)
                row.addWidget(b2)
            row.addStretch(1)
            v.addWidget(box)

        self.lbl_export = QLabel("")
        self.lbl_export.setWordWrap(True)
        self.lbl_export.setStyleSheet("color:#666")
        v.addWidget(self.lbl_export)
        v.addStretch(1)
        return page

    def export_tabby(self) -> None:
        path = terminals.export_tabby()
        self.lbl_export.setText(f"Tabby 片段：{path}")

    def apply_tabby(self) -> None:
        backup, added, path = terminals.apply_tabby()
        self.lbl_export.setText(
            f"Tabby：新增 {added} 个 profile -> {path}"
            + (f"（备份 {backup.name}）" if backup else "")
        )

    def export_wt(self) -> None:
        path = terminals.export_windows_terminal()
        self.lbl_export.setText(f"Windows Terminal 片段：{path}")

    def apply_wt(self) -> None:
        backup, added, path = terminals.apply_windows_terminal()
        self.lbl_export.setText(
            f"Windows Terminal：新增 {added} 个 profile -> {path}"
            + (f"（备份 {backup.name}）" if backup else "")
        )

    # ---------------------------------------------------------------- 数据库
    def _build_db_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        bar = QHBoxLayout()
        for text, slot in (
            ("添加", self.add_db),
            ("删除", self.delete_db),
            ("测试连接", self.test_db),
            ("备份", self.backup_db),
            ("恢复（覆盖）", self.restore_db),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            bar.addWidget(btn)
        bar.addStretch(1)
        v.addLayout(bar)

        self.tbl_db = QTableWidget(0, 6)
        self.tbl_db.setHorizontalHeaderLabels(
            ["名称", "类型", "地址", "用户", "范围", "客户端"]
        )
        self.tbl_db.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_db.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_db.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_db.setAlternatingRowColors(True)
        v.addWidget(self.tbl_db, 1)

        self.lbl_db = QLabel("")
        self.lbl_db.setWordWrap(True)
        self.lbl_db.setStyleSheet("color:#666")
        v.addWidget(self.lbl_db)
        return page

    def reload_db(self) -> None:
        self.catalog = db.DbCatalog.load()
        targets = self.catalog.targets
        self.tbl_db.setRowCount(len(targets))
        for row, target in enumerate(targets):
            values = [
                target.name, target.engine, f"{target.host}:{target.port}", target.user,
                "全部" if not target.databases else ",".join(target.databases),
                target.client_mode,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, target.id)
                self.tbl_db.setItem(row, col, item)
        self.lbl_db.setText(f"{len(targets)} 个数据库目标")

    def _selected_db(self) -> db.DbTarget | None:
        rows = self.tbl_db.selectionModel().selectedRows()
        if not rows:
            return None
        target_id = self.tbl_db.item(rows[0].row(), 0).data(Qt.UserRole)
        return self.catalog.by_id(target_id)

    def add_db(self) -> None:
        dlg = DbDialog(parent=self)
        if dlg.exec() == QDialog.Accepted:
            self.catalog.upsert(dlg.value())
            self.reload_db()

    def delete_db(self) -> None:
        target = self._selected_db()
        if target is None:
            return
        if QMessageBox.question(self, "删除", f"删除 {target.name}？") != QMessageBox.Yes:
            return
        self.catalog.remove(target.id)
        self.reload_db()

    def test_db(self) -> None:
        target = self._selected_db()
        if target is None:
            return
        self.lbl_db.setText("连接中…")
        run_async(
            self, lambda: db.test_connection(target),
            lambda msg: self.lbl_db.setText("✅ " + " ".join(str(msg).split())[:160]),
            lambda m: self.lbl_db.setText("❌ " + m),
        )

    def backup_db(self) -> None:
        target = self._selected_db()
        if target is None:
            return
        self.lbl_db.setText("备份中…（大库可能较久）")
        run_async(
            self, lambda: db.backup(target),
            lambda path: self.lbl_db.setText(f"✅ 备份完成：{path}"),
            lambda m: self.lbl_db.setText("❌ " + m),
        )

    def restore_db(self) -> None:
        target = self._selected_db()
        if target is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择备份文件", str(config.backups_dir() / "db"),
            "备份 (*.sql.gz *.tar.gz)",
        )
        if not path:
            return
        if QMessageBox.warning(
            self, "覆盖恢复",
            f"将用这个备份覆盖 {target.name} 的现有数据：\n{path}\n\n继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self.lbl_db.setText("恢复中…")
        run_async(
            self, lambda: db.restore(target, path, confirm=True),
            lambda msg: self.lbl_db.setText("✅ " + str(msg)),
            lambda m: self.lbl_db.setText("❌ " + m),
        )


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
