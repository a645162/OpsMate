"""备份加密的往返与防篡改测试。"""

from __future__ import annotations

import os

import pytest

from opsmate import crypto


def test_roundtrip_binary(tmp_path):
    payload = os.urandom(3 * 1024 * 1024 + 17)
    plain = tmp_path / "data.bin"
    plain.write_bytes(payload)

    enc = tmp_path / "data.bin.enc"
    writer = crypto.EncryptingWriter(enc, "correct horse battery staple")
    with plain.open("rb") as src:
        while chunk := src.read(1024 * 1024):
            writer.write(chunk)
    writer.close()

    assert enc.read_bytes()[:8] == crypto.MAGIC
    assert payload not in enc.read_bytes()

    out = tmp_path / "restored.bin"
    crypto.decrypt_file(enc, "correct horse battery staple", out)
    assert out.read_bytes() == payload


def test_wrong_password_fails_and_removes_output(tmp_path):
    enc = tmp_path / "x.enc"
    writer = crypto.EncryptingWriter(enc, "right")
    writer.write(b"hello world")
    writer.close()

    out = tmp_path / "out.bin"
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_file(enc, "wrong", out)
    assert not out.exists()


def test_tamper_detected(tmp_path):
    enc = tmp_path / "x.enc"
    writer = crypto.EncryptingWriter(enc, "pw")
    writer.write(b"a" * 4096)
    writer.close()

    data = bytearray(enc.read_bytes())
    data[-40] ^= 0xFF  # 改密文
    enc.write_bytes(bytes(data))

    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_file(enc, "pw", tmp_path / "out.bin")


def test_is_encrypted(tmp_path):
    plain = tmp_path / "a.txt"
    plain.write_text("hi", "utf-8")
    assert crypto.is_encrypted(plain) is False

    enc = tmp_path / "a.enc"
    writer = crypto.EncryptingWriter(enc, "pw")
    writer.write(b"hi")
    writer.close()
    assert crypto.is_encrypted(enc) is True
