import asyncio

import httpx
import pytest

from jumpserver_mcp_server import executor as executor_module
from jumpserver_mcp_server.config import Settings
from jumpserver_mcp_server.executor import ApiToolExecutor


def client_factory(handler):
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("verify", None)
        kwargs.pop("follow_redirects", None)
        return original(transport=transport, *args, **kwargs)

    return factory


@pytest.fixture
def executor():
    ops = {
        "host_read": {
            "path": "/api/v1/assets/hosts/{id}/",
            "method": "get",
            "parameters": [
                {"name": "id", "in": "path"},
                {"name": "fields", "in": "query"},
                {"name": "X-JMS-ORG", "in": "header"},
            ],
        },
        "perm_read": {"path": "/api/v1/perms/asset-permissions/{id}/", "method": "get", "parameters": []},
        "block_ip": {"path": "/api/v1/settings/security/block-ip/", "method": "post", "parameters": []},
    }
    return ApiToolExecutor(
        "https://jms.example.com",
        ops,
        Settings(jumpserver_url="https://jms.example.com", api_token="server-token", max_response_chars=80),
    )


def test_path_query_body_and_auth(monkeypatch, executor):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={"id": "abc", "name": "host-1"})

    monkeypatch.setattr(executor_module.httpx, "AsyncClient", client_factory(handler))
    result = asyncio.run(
        executor.execute(
            "host_read",
            {"id": "abc", "fields": "id,name", "X-JMS-ORG": "ops"},
            {"authorization": "Bearer client-token"},
        )
    )
    assert seen["url"].startswith("https://jms.example.com/api/v1/assets/hosts/abc/")
    assert "fields=id%2Cname" in seen["url"]
    assert "/api/v1/api/v1" not in seen["url"]
    assert seen["headers"]["authorization"] == "Bearer client-token"
    assert seen["headers"]["x-jms-org"] == "ops"
    assert '"name": "host-1"' in result[0].text


def test_undeclared_placeholder_is_replaced(monkeypatch, executor):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={})

    monkeypatch.setattr(executor_module.httpx, "AsyncClient", client_factory(handler))
    asyncio.run(executor.execute("perm_read", {"id": "9c1f"}, None))
    assert seen["url"].endswith("/api/v1/perms/asset-permissions/9c1f/")
    assert "%7B" not in seen["url"]


def test_missing_placeholder_fails_loudly(executor):
    with pytest.raises(ValueError, match="¿¿¿¿¿¿"):
        asyncio.run(executor.execute("perm_read", {}, None))


def test_body_and_error(monkeypatch, executor):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        seen["headers"] = dict(request.headers)
        return httpx.Response(403, text="Forbidden")

    monkeypatch.setattr(executor_module.httpx, "AsyncClient", client_factory(handler))
    with pytest.raises(RuntimeError, match="403"):
        asyncio.run(executor.execute("block_ip", {"ip": "1.2.3.4"}, None))
    assert __import__("json").loads(seen["body"]) == {"ip": "1.2.3.4"}
    assert seen["headers"]["authorization"] == "Bearer server-token"


def test_access_key_signature(monkeypatch):
    ops = {"ping": {"path": "/api/v1/users/profile/", "method": "get", "parameters": []}}
    cfg = Settings(
        jumpserver_url="https://jms.example.com",
        access_key_id="AK",
        access_key_secret="SK",
    )
    ex = ApiToolExecutor("https://jms.example.com", ops, cfg)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={})

    monkeypatch.setattr(executor_module.httpx, "AsyncClient", client_factory(handler))
    asyncio.run(ex.execute("ping", {}, None))
    assert seen["headers"]["authorization"].startswith('Signature keyId="AK"')
    assert "hmac-sha256" in seen["headers"]["authorization"]
    assert "date" in seen["headers"]


def test_response_truncated(monkeypatch, executor):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": "x" * 500})

    monkeypatch.setattr(executor_module.httpx, "AsyncClient", client_factory(handler))
    result = asyncio.run(executor.execute("perm_read", {"id": "1"}, None))
    assert "¿¿¿¿¿" in result[0].text
