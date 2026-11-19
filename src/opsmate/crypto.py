"""备份文件加密（AES-256-CTR + HMAC-SHA256，流式）。

文件格式::

    b"OPSMATE1" | salt(16) | iv(16) | ciphertext... | hmac(32)

密钥：PBKDF2-HMAC-SHA256(password, salt, 200000) 派生 64 字节（加密 32 / MAC 32）。
HMAC 覆盖 header + salt + iv + ciphertext，解密时会完整校验后再交给恢复流程。
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC = b"OPSMATE1"
SALT_LEN = 16
IV_LEN = 16
MAC_LEN = 32
ITERATIONS = 200_000
CHUNK = 1024 * 1024


class CryptoError(RuntimeError):
    pass


def _derive(password: str, salt: bytes) -> tuple[bytes, bytes]:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=64, salt=salt, iterations=ITERATIONS
    )
    material = kdf.derive(password.encode("utf-8"))
    return material[:32], material[32:]


class EncryptingWriter:
    """按块写入的加密文件对象，可传给 gzip / tarfile。"""

    def __init__(self, path: Path, password: str):
        if not password:
            raise CryptoError("加密口令不能为空")
        self.path = Path(path)
        self._salt = os.urandom(SALT_LEN)
        self._iv = os.urandom(IV_LEN)
        enc_key, mac_key = _derive(password, self._salt)
        self._encryptor = Cipher(
            algorithms.AES(enc_key), modes.CTR(self._iv)
        ).encryptor()
        self._mac = hmac.new(mac_key, digestmod=hashlib.sha256)
        self._fh = open(self.path, "wb")  # noqa: SIM115 - 需要显式 close 控制格式
        header = MAGIC + self._salt + self._iv
        self._fh.write(header)
        self._mac.update(header)
        self._closed = False

    def write(self, data: bytes) -> int:
        if self._closed:
            raise CryptoError("writer 已关闭")
        if not data:
            return 0
        ciphertext = self._encryptor.update(data)
        self._mac.update(ciphertext)
        self._fh.write(ciphertext)
        return len(data)

    def close(self) -> None:
        if self._closed:
            return
        try:
            tail = self._encryptor.finalize()
            self._mac.update(tail)
            self._fh.write(tail)
            self._fh.write(self._mac.digest())
        finally:
            self._fh.close()
            self._closed = True

    def abort(self) -> None:
        try:
            self._fh.close()
        finally:
            self.path.unlink(missing_ok=True)
            self._closed = True


def is_encrypted(path: str | Path) -> bool:
    p = Path(path)
    try:
        with p.open("rb") as fh:
            return fh.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def decrypt_file(path: str | Path, password: str, out_path: str | Path) -> Path:
    """校验 HMAC 后解密到 out_path；失败会删掉半成品。"""
    path = Path(path)
    out_path = Path(out_path)
    size = path.stat().st_size
    if size < len(MAGIC) + SALT_LEN + IV_LEN + MAC_LEN:
        raise CryptoError("文件太小，不像 OpsMate 加密备份")

    with path.open("rb") as fh:
        magic = fh.read(len(MAGIC))
        if magic != MAGIC:
            raise CryptoError("不是 OpsMate 加密备份文件")
        salt = fh.read(SALT_LEN)
        iv = fh.read(IV_LEN)
        enc_key, mac_key = _derive(password, salt)
        decryptor = Cipher(algorithms.AES(enc_key), modes.CTR(iv)).decryptor()
        mac = hmac.new(mac_key, digestmod=hashlib.sha256)
        mac.update(magic + salt + iv)

        cipher_len = size - len(MAGIC) - SALT_LEN - IV_LEN - MAC_LEN
        with out_path.open("wb") as out:
            remaining = cipher_len
            while remaining > 0:
                chunk = fh.read(min(CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                mac.update(chunk)
                out.write(decryptor.update(chunk))
            out.write(decryptor.finalize())
        stored = fh.read(MAC_LEN)

    try:
        if not hmac.compare_digest(stored, mac.digest()):
            raise CryptoError("口令不对，或文件被篡改（HMAC 校验失败）")
    except CryptoError:
        out_path.unlink(missing_ok=True)
        raise
    return out_path
