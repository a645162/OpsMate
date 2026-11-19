"""DNS 解析管理：Cloudflare 与阿里云（AliDNS）。

凭据存 ``~/.config/opsmate/dns.json``（0600）。
Cloudflare 用 API Token；阿里云用 AccessKey（RAM 子账号建议只给 AliyunDNSFullAccess）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import quote

import requests

from opsmate import config


class DnsError(RuntimeError):
    pass


def new_id() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class DnsCredential:
    name: str
    provider: str = "cloudflare"          # cloudflare | aliyun
    token: str = ""                       # Cloudflare API Token
    access_key_id: str = ""               # 阿里云 AccessKeyId
    access_key_secret: str = ""           # 阿里云 AccessKeySecret
    id: str = field(default_factory=new_id)

    @property
    def label(self) -> str:
        return f"{self.name}（{self.provider}）"


@dataclass
class Zone:
    id: str
    name: str
    provider: str = ""


@dataclass
class Record:
    id: str
    name: str
    type: str
    content: str
    ttl: int = 1
    proxied: bool = False
    line: str = ""
    priority: int = 0
    status: str = ""


@dataclass
class DnsStore:
    credentials: list[DnsCredential] = field(default_factory=list)

    @classmethod
    def load(cls) -> DnsStore:
        raw = config.read_json(config.dns_credentials_path(), {"credentials": []})
        items = raw.get("credentials", []) if isinstance(raw, dict) else []
        creds = []
        for item in items:
            if not isinstance(item, dict):
                continue
            fields = {k: v for k, v in item.items() if k in DnsCredential.__dataclass_fields__}
            creds.append(DnsCredential(**fields))
        return cls(credentials=creds)

    def save(self) -> None:
        config.write_private(
            config.dns_credentials_path(),
            {"version": 1, "credentials": [asdict(c) for c in self.credentials]},
        )

    def by_id(self, cred_id: str) -> DnsCredential | None:
        return next((c for c in self.credentials if c.id == cred_id), None)

    def by_name(self, name: str) -> DnsCredential | None:
        return next((c for c in self.credentials if c.name == name), None)

    def upsert(self, cred: DnsCredential) -> None:
        for index, existing in enumerate(self.credentials):
            if existing.id == cred.id:
                self.credentials[index] = cred
                break
        else:
            self.credentials.append(cred)
        self.save()

    def remove(self, cred_id: str) -> bool:
        before = len(self.credentials)
        self.credentials = [c for c in self.credentials if c.id != cred_id]
        if len(self.credentials) != before:
            self.save()
            return True
        return False


# ------------------------------------------------------------------ Cloudflare
class Cloudflare:
    BASE = "https://api.cloudflare.com/client/v4"

    def __init__(self, token: str, session: requests.Session | None = None):
        if not token:
            raise DnsError("缺少 Cloudflare API Token")
        self.session = session or requests.Session()
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.BASE}{path}"
        try:
            resp = self.session.request(
                method, url, headers=self.headers, timeout=30, **kwargs
            )
        except requests.RequestException as exc:
            raise DnsError(f"请求失败：{exc}") from exc
        try:
            data = resp.json()
        except ValueError as exc:
            raise DnsError(f"返回不是 JSON（HTTP {resp.status_code}）") from exc
        if not data.get("success"):
            errors = data.get("errors") or [{"message": f"HTTP {resp.status_code}"}]
            raise DnsError("; ".join(str(e.get("message")) for e in errors))
        return data.get("result")

    def zones(self) -> list[Zone]:
        result = self._call("GET", "/zones", params={"per_page": 50}) or []
        return [Zone(id=z["id"], name=z["name"], provider="cloudflare") for z in result]

    def records(self, zone_id: str) -> list[Record]:
        result = self._call(
            "GET", f"/zones/{zone_id}/dns_records", params={"per_page": 100}
        ) or []
        return [
            Record(
                id=r["id"], name=r["name"], type=r["type"], content=r.get("content", ""),
                ttl=int(r.get("ttl") or 1), proxied=bool(r.get("proxied")),
                priority=int(r.get("priority") or 0),
            )
            for r in result
        ]

    def add(self, zone_id: str, *, name: str, type: str, content: str,
            ttl: int = 1, proxied: bool = False, priority: int | None = None) -> Record:
        body: dict[str, Any] = {"name": name, "type": type, "content": content, "ttl": ttl}
        if type in ("A", "AAAA", "CNAME"):
            body["proxied"] = proxied
        if priority is not None and type == "MX":
            body["priority"] = priority
        result = self._call("POST", f"/zones/{zone_id}/dns_records", json=body)
        return Record(
            id=result["id"], name=result["name"], type=result["type"],
            content=result.get("content", ""), ttl=int(result.get("ttl") or ttl),
            proxied=bool(result.get("proxied")),
        )

    def update(self, zone_id: str, record_id: str, **fields: Any) -> Record:
        body = {k: v for k, v in fields.items() if v is not None}
        result = self._call(
            "PATCH", f"/zones/{zone_id}/dns_records/{record_id}", json=body
        )
        return Record(
            id=result["id"], name=result["name"], type=result["type"],
            content=result.get("content", ""), ttl=int(result.get("ttl") or 1),
            proxied=bool(result.get("proxied")),
        )

    def delete(self, zone_id: str, record_id: str) -> None:
        self._call("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")


# -------------------------------------------------------------------- 阿里云
def _percent_encode(value: Any) -> str:
    return quote(str(value), safe="~")


class Aliyun:
    BASE = "https://alidns.aliyuncs.com/"

    def __init__(self, access_key_id: str, access_key_secret: str,
                 session: requests.Session | None = None):
        if not access_key_id or not access_key_secret:
            raise DnsError("缺少阿里云 AccessKey")
        self.key_id = access_key_id
        self.secret = access_key_secret
        self.session = session or requests.Session()

    def sign(self, params: dict[str, Any], nonce: str, timestamp: str) -> str:
        payload = {
            "AccessKeyId": self.key_id,
            "Format": "JSON",
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": nonce,
            "SignatureVersion": "1.0",
            "Timestamp": timestamp,
            "Version": "2015-01-09",
            **params,
        }
        canonical = "&".join(
            f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in sorted(payload.items())
        )
        string_to_sign = "GET&%2F&" + _percent_encode(canonical)
        digest = hmac.new(
            (self.secret + "&").encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        payload["Signature"] = base64.b64encode(digest).decode("ascii")
        return payload["Signature"]

    def _rpc(self, action: str, **params: Any) -> dict[str, Any]:
        nonce = uuid.uuid4().hex
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        payload: dict[str, Any] = {
            "Action": action,
            "AccessKeyId": self.key_id,
            "Format": "JSON",
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": nonce,
            "SignatureVersion": "1.0",
            "Timestamp": timestamp,
            "Version": "2015-01-09",
            **{k: v for k, v in params.items() if v not in (None, "")},
        }
        canonical = "&".join(
            f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in sorted(payload.items())
        )
        string_to_sign = "GET&%2F&" + _percent_encode(canonical)
        payload["Signature"] = base64.b64encode(
            hmac.new(
                (self.secret + "&").encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha1,
            ).digest()
        ).decode("ascii")

        try:
            resp = self.session.request("GET", self.BASE, params=payload, timeout=30)
        except requests.RequestException as exc:
            raise DnsError(f"请求失败：{exc}") from exc
        try:
            data = resp.json()
        except ValueError as exc:
            raise DnsError(f"返回不是 JSON（HTTP {resp.status_code}）") from exc
        if "Code" in data and data.get("Code") not in (None, "200"):
            raise DnsError(f"{data.get('Code')}: {data.get('Message')}")
        return data

    def zones(self) -> list[Zone]:
        data = self._rpc("DescribeDomains", PageSize=100)
        domains = (data.get("Domains") or {}).get("Domain") or []
        return [
            Zone(id=d.get("DomainId") or d.get("DomainName", ""),
                 name=d.get("DomainName", ""), provider="aliyun")
            for d in domains
        ]

    def records(self, domain: str) -> list[Record]:
        data = self._rpc("DescribeDomainRecords", DomainName=domain, PageSize=500)
        items = (data.get("DomainRecords") or {}).get("Record") or []
        return [
            Record(
                id=r.get("RecordId", ""), name=r.get("RR", ""), type=r.get("Type", ""),
                content=r.get("Value", ""), ttl=int(r.get("TTL") or 600),
                line=r.get("Line", ""), priority=int(r.get("Priority") or 0),
                status=r.get("Status", ""),
            )
            for r in items
        ]

    def add(self, domain: str, *, name: str, type: str, content: str,
            ttl: int = 600, line: str = "default", priority: int | None = None) -> Record:
        params: dict[str, Any] = {
            "DomainName": domain, "RR": name, "Type": type, "Value": content,
            "TTL": ttl, "Line": line,
        }
        if priority:
            params["Priority"] = priority
        data = self._rpc("AddDomainRecord", **params)
        return Record(
            id=data.get("RecordId", ""), name=name, type=type, content=content,
            ttl=ttl, line=line, priority=priority or 0,
        )

    def update(self, record_id: str, **fields: Any) -> Record:
        data = self._rpc("UpdateDomainRecord", RecordId=record_id,
                         **{k: v for k, v in fields.items() if v not in (None, "")})
        return Record(
            id=data.get("RecordId", record_id),
            name=fields.get("RR", ""), type=fields.get("Type", ""),
            content=fields.get("Value", ""), ttl=int(fields.get("TTL") or 600),
        )

    def delete(self, record_id: str) -> None:
        self._rpc("DeleteDomainRecord", RecordId=record_id)


def provider_for(cred: DnsCredential, session: requests.Session | None = None):
    if cred.provider == "cloudflare":
        return Cloudflare(cred.token, session=session)
    if cred.provider == "aliyun":
        return Aliyun(cred.access_key_id, cred.access_key_secret, session=session)
    raise DnsError(f"未知的 DNS 提供商：{cred.provider}")
