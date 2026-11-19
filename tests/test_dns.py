"""DNS 提供商（Cloudflare / 阿里云）的离线测试。"""

from __future__ import annotations

import pytest

from opsmate import config, dns


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeResponse(self.responses.pop(0))


def test_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path / "home"))
    store = dns.DnsStore()
    cred = dns.DnsCredential(name="cf", provider="cloudflare", token="t")
    store.upsert(cred)
    loaded = dns.DnsStore.load()
    assert loaded.by_name("cf").token == "t"
    assert loaded.remove(cred.id) is True
    assert dns.DnsStore.load().credentials == []


def test_cloudflare_zones_and_records():
    session = FakeSession([
        {"success": True, "result": [{"id": "z1", "name": "example.com"}]},
        {"success": True, "result": [{
            "id": "r1", "name": "www.example.com", "type": "A",
            "content": "203.0.113.10", "ttl": 300, "proxied": True,
        }]},
    ])
    cf = dns.Cloudflare("token", session=session)
    zones = cf.zones()
    assert zones[0].name == "example.com"
    records = cf.records("z1")
    assert records[0].content == "203.0.113.10"
    assert records[0].proxied is True
    assert session.calls[0][2]["headers"]["Authorization"] == "Bearer token"


def test_cloudflare_add_update_delete():
    session = FakeSession([
        {"success": True, "result": {"id": "r2", "name": "a.example.com",
                                     "type": "A", "content": "203.0.113.11"}},
        {"success": True, "result": {"id": "r2", "name": "b.example.com",
                                     "type": "A", "content": "203.0.113.12"}},
        {"success": True, "result": {"id": "r2"}},
    ])
    cf = dns.Cloudflare("token", session=session)
    cf.add("z1", name="a.example.com", type="A", content="203.0.113.11", proxied=True)
    body = session.calls[0][2]["json"]
    assert body["proxied"] is True and body["type"] == "A"

    cf.update("z1", "r2", name="b.example.com")
    assert session.calls[1][0] == "PATCH"
    assert session.calls[1][1].endswith("/zones/z1/dns_records/r2")

    cf.delete("z1", "r2")
    assert session.calls[2][0] == "DELETE"


def test_cloudflare_error_raises():
    session = FakeSession([{"success": False, "errors": [{"message": "bad token"}]}])
    cf = dns.Cloudflare("t", session=session)
    with pytest.raises(dns.DnsError, match="bad token"):
        cf.zones()


def test_aliyun_signature_is_stable():
    client = dns.Aliyun("key-id", "secret", session=FakeSession([]))
    params = {"Action": "DescribeDomains", "PageSize": 100}
    sig1 = client.sign(params, "nonce", "2026-01-01T00:00:00Z")
    sig2 = client.sign(params, "nonce", "2026-01-01T00:00:00Z")
    assert sig1 == sig2
    assert len(sig1) > 20


def test_aliyun_zones_and_records():
    session = FakeSession([
        {"Domains": {"Domain": [{"DomainId": "d1", "DomainName": "example.cn"}]}},
        {"DomainRecords": {"Record": [{
            "RecordId": "r1", "RR": "www", "Type": "A", "Value": "203.0.113.10",
            "TTL": 600, "Line": "default", "Status": "ENABLE",
        }]}},
    ])
    aliyun = dns.Aliyun("kid", "secret", session=session)
    zones = aliyun.zones()
    assert zones[0].name == "example.cn"

    records = aliyun.records("example.cn")
    assert records[0].name == "www"
    assert records[0].content == "203.0.113.10"

    sent = session.calls[0][2]["params"]
    assert sent["Action"] == "DescribeDomains"
    assert "Signature" in sent
    assert sent["AccessKeyId"] == "kid"


def test_aliyun_add_and_error():
    session = FakeSession([
        {"RecordId": "r9"},
        {"Code": "InvalidAccessKeyId.NotFound", "Message": "invalid key"},
    ])
    aliyun = dns.Aliyun("kid", "secret", session=session)
    record = aliyun.add("example.cn", name="api", type="A", content="203.0.113.9")
    assert record.id == "r9"
    add_params = session.calls[0][2]["params"]
    assert add_params["Action"] == "AddDomainRecord"
    assert add_params["RR"] == "api"

    with pytest.raises(dns.DnsError, match="InvalidAccessKeyId"):
        aliyun.zones()
