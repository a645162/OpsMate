"""CLI 与环境自检的离线测试（不联网）。"""

from __future__ import annotations

import stat

from opsmate import __version__, config, doctor
from opsmate.cli import main


def test_version(capsys):
    assert main(["version"]) == 0
    out = capsys.readouterr().out
    assert __version__ in out
    assert "运维助手" in out


def test_doctor_runs_without_network(capsys, monkeypatch):
    monkeypatch.setattr(doctor, "check_login_online", lambda: doctor.Check("x", doctor.OK))
    code = main(["doctor"])
    out = capsys.readouterr().out
    assert code in (0, 1)
    assert "Python" in out


def test_check_python_ok():
    assert doctor.check_python().status == doctor.OK


def test_check_auth_missing(tmp_path, monkeypatch):
    missing = tmp_path / "nope.json"
    monkeypatch.setattr(doctor, "landui_auth_path", lambda: missing)
    check = doctor.check_auth()
    assert check.status == doctor.FAIL
    assert "缺失" in check.detail


def test_check_auth_permissions(tmp_path, monkeypatch):
    path = tmp_path / "auth.json"
    path.write_text("{}", "utf-8")
    path.chmod(0o644)
    monkeypatch.setattr(doctor, "landui_auth_path", lambda: path)
    assert doctor.check_auth().status == doctor.WARN

    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert doctor.check_auth().status == doctor.OK


def test_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path / "home"))
    config.save_settings({"theme": "dark"})
    assert config.load_settings() == {"theme": "dark"}
    assert config.settings_path().exists()


def test_summarize():
    ok = [doctor.Check("a", doctor.OK), doctor.Check("b", doctor.WARN)]
    assert doctor.summarize(ok)[0] is True
    bad = [*ok, doctor.Check("c", doctor.FAIL)]
    assert doctor.summarize(bad)[0] is False
