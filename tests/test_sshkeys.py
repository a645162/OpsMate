"""SSH 密钥管理的离线测试（需要本机 ssh-keygen）。"""

from __future__ import annotations

import pytest

from opsmate import sshkeys

pytestmark = pytest.mark.skipif(
    sshkeys.find_tool("ssh-keygen") is None, reason="本机没有 ssh-keygen"
)


def test_generate_list_delete(tmp_path):
    key = sshkeys.generate_key("id_test", comment="opsmate-test", directory=tmp_path)
    assert key.private_path.exists()
    assert key.public_path.exists()
    assert key.private_path.stat().st_mode & 0o777 == 0o600
    assert key.public_text().startswith("ssh-ed25519")

    keys = sshkeys.list_keys(tmp_path)
    assert [k.name for k in keys] == ["id_test"]
    assert keys[0].fingerprint.startswith("SHA256:")

    sshkeys.delete_key(key)
    assert not key.private_path.exists()
    assert not key.public_path.exists()


def test_generate_refuses_overwrite(tmp_path):
    sshkeys.generate_key("id_test", directory=tmp_path)
    with pytest.raises(FileExistsError):
        sshkeys.generate_key("id_test", directory=tmp_path)
    sshkeys.generate_key("id_test", directory=tmp_path, overwrite=True)


def test_read_public_key_from_private_path(tmp_path):
    key = sshkeys.generate_key("id_x", directory=tmp_path)
    assert sshkeys.read_public_key(key.private_path).startswith("ssh-ed25519")


def test_install_rejects_garbage():
    from opsmate.inventory import HostEntry

    host = HostEntry(name="h", host="127.0.0.1")
    with pytest.raises(ValueError):
        sshkeys.install_public_key(host, "not-a-key")
