"""SSH 密钥：列出、生成、读公钥、装到服务器、上传 GitHub。"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from landui_sdk.ssh import build_ssh_command, ssh_env

from opsmate import config
from opsmate.inventory import HostEntry

WINDOWS_OPENSSH = Path(r"C:\Windows\System32\OpenSSH")


def find_tool(name: str) -> str | None:
    """ssh-keygen 在 Windows 上不一定进 PATH。"""
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        candidate = WINDOWS_OPENSSH / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    return None


@dataclass
class KeyInfo:
    name: str
    private_path: Path
    public_path: Path
    key_type: str = ""
    bits: int = 0
    fingerprint: str = ""
    comment: str = ""

    def public_text(self) -> str:
        try:
            return self.public_path.read_text("utf-8").strip()
        except OSError:
            return ""


def _fingerprint(pub_path: Path) -> tuple[str, int, str]:
    exe = find_tool("ssh-keygen")
    if not exe:
        return "", 0, ""
    try:
        proc = subprocess.run(
            [exe, "-lf", str(pub_path)],
            check=False, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "", 0, ""
    text = (proc.stdout or "").strip()
    if not text:
        return "", 0, ""
    # 例：256 SHA256:xxxx comment (ED25519)
    parts = text.split()
    bits = int(parts[0]) if parts and parts[0].isdigit() else 0
    fingerprint = next((p for p in parts if p.startswith("SHA256:")), "")
    key_type = ""
    if "(" in text and ")" in text:
        key_type = text.rsplit("(", 1)[-1].rstrip(")").upper()
    comment = ""
    if len(parts) >= 3:
        tail = " ".join(parts[2:])
        comment = tail.split("(")[0].strip()
    return fingerprint, bits, f"{key_type}|{comment}"


def list_keys(directory: Path | None = None) -> list[KeyInfo]:
    directory = directory or config.ssh_dir()
    if not directory.exists():
        return []
    keys: list[KeyInfo] = []
    for pub in sorted(directory.glob("*.pub")):
        private = pub.with_suffix("")
        fingerprint, bits, extra = _fingerprint(pub)
        key_type, _, comment = extra.partition("|")
        keys.append(
            KeyInfo(
                name=pub.stem,
                private_path=private,
                public_path=pub,
                key_type=key_type,
                bits=bits,
                fingerprint=fingerprint,
                comment=comment,
            )
        )
    return keys


def read_public_key(path: str | Path) -> str:
    p = Path(path).expanduser()
    if p.suffix != ".pub":
        pub = p.with_name(p.name + ".pub")
        if pub.exists():
            p = pub
    return p.read_text("utf-8").strip()


def generate_key(
    name: str,
    *,
    key_type: str = "ed25519",
    comment: str = "",
    passphrase: str = "",
    bits: int | None = None,
    directory: Path | None = None,
    overwrite: bool = False,
) -> KeyInfo:
    exe = find_tool("ssh-keygen")
    if not exe:
        raise RuntimeError("找不到 ssh-keygen，请先安装 OpenSSH 客户端")
    directory = directory or config.ssh_dir()
    directory.mkdir(parents=True, exist_ok=True)
    private = directory / name
    public = directory / f"{name}.pub"
    if (private.exists() or public.exists()) and not overwrite:
        raise FileExistsError(f"密钥已存在：{private}")

    if overwrite:
        private.unlink(missing_ok=True)
        public.unlink(missing_ok=True)

    cmd = [exe, "-q", "-t", key_type, "-f", str(private), "-N", passphrase]
    if comment:
        cmd += ["-C", comment]
    if bits:
        cmd += ["-b", str(bits)]

    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"ssh-keygen 失败：{(proc.stderr or proc.stdout).strip()}")
    try:
        private.chmod(0o600)
        public.chmod(0o644)
    except OSError:
        pass

    fingerprint, key_bits, extra = _fingerprint(public)
    detected_type, _, parsed_comment = extra.partition("|")
    return KeyInfo(
        name=name,
        private_path=private,
        public_path=public,
        key_type=detected_type or key_type.upper(),
        bits=key_bits or (bits or 0),
        fingerprint=fingerprint,
        comment=parsed_comment or comment,
    )


def delete_key(key: KeyInfo) -> None:
    key.public_path.unlink(missing_ok=True)
    key.private_path.unlink(missing_ok=True)


def install_public_key(
    host: HostEntry, public_key: str, *, timeout: float = 30.0
) -> str:
    """把公钥追加到远端 ~/.ssh/authorized_keys（幂等，已存在就不重复加）。"""
    public_key = public_key.strip()
    if not public_key.startswith(("ssh-", "ecdsa-", "sk-")):
        raise ValueError("这不像一个公钥")
    quoted = shlex.quote(public_key)
    remote = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        "touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && "
        f"grep -qxF {quoted} ~/.ssh/authorized_keys || "
        f"printf '%s\\n' {quoted} >> ~/.ssh/authorized_keys && echo OK"
    )
    cmd = [*build_ssh_command(host.target()), remote]
    proc = subprocess.run(
        cmd, check=False, env=ssh_env(host.target()),
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0 or "OK" not in proc.stdout:
        raise RuntimeError((proc.stderr or proc.stdout).strip()[-400:] or "安装失败")
    return "已写入 authorized_keys"


def upload_to_github(
    public_key_path: str | Path, title: str, *, timeout: float = 30.0
) -> str:
    """用 GitHub CLI 上传公钥：gh ssh-key add <file> --title <title>。"""
    gh = shutil.which("gh")
    if not gh:
        raise RuntimeError("找不到 gh（GitHub CLI），请先安装并登录")
    path = Path(public_key_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(path)

    auth = subprocess.run(
        [gh, "auth", "status"], check=False, capture_output=True, text=True, timeout=15
    )
    if auth.returncode != 0:
        raise RuntimeError("gh 未登录：先运行 `gh auth login`")

    proc = subprocess.run(
        [gh, "ssh-key", "add", str(path), "--title", title],
        check=False, capture_output=True, text=True, timeout=timeout,
    )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        raise RuntimeError(err or out or "上传失败")
    return out or "已上传到 GitHub"
