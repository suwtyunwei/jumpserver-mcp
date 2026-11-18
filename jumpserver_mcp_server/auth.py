"""JumpServer authentication helpers.

Three credential sources are supported, in priority order:

1. Per-call credentials forwarded from the MCP client (its ``Authorization``
   header), so every action is attributed to the developer using the client.
2. Server-side Access Key (AccessKeyID + AccessKeySecret) signed with the
   JumpServer HTTP Signature scheme.
3. Server-side short-lived Bearer token (``api_token``).
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
from typing import Any, Mapping
from urllib.parse import urlencode

import httpx

from .config import Settings

SIGNATURE_HEADERS = "(request-target) accept date"


def normalize_js_host(config: Settings) -> str:
    """Return the JumpServer host without a trailing ``/api/v1``.

    JumpServer's OpenAPI schema already prefixes every operation path with
    ``/api/v1``. Keeping the prefix in only one place avoids the classic
    ``/api/v1/api/v1/...`` 404.
    """
    host = (config.api_base_url or config.jumpserver_url).rstrip("/")
    if host.endswith("/api/v1"):
        host = host[: -len("/api/v1")]
    return host


def signature_headers(
    method: str,
    path: str,
    query: Mapping[str, Any] | None,
    access_key_id: str,
    access_key_secret: str,
) -> dict[str, str]:
    """Build headers for JumpServer's HTTP Signature (hmac-sha256) scheme."""
    date = dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    accept = "application/json"
    target = method.lower() + " " + path
    if query:
        target += "?" + urlencode(query, doseq=True)
    signing_string = "\n".join(
        [
            f"(request-target): {target}",
            f"accept: {accept}",
            f"date: {date}",
        ]
    )
    signature = base64.b64encode(
        hmac.new(access_key_secret.encode(), signing_string.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "Accept": accept,
        "Date": date,
        "Authorization": (
            f'Signature keyId="{access_key_id}",algorithm="hmac-sha256",'
            f'headers="{SIGNATURE_HEADERS}",signature="{signature}"'
        ),
    }


class AccessKeyAuth(httpx.Auth):
    """httpx auth adapter used for the one-time OpenAPI schema fetch."""

    def __init__(self, access_key_id: str, access_key_secret: str) -> None:
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret

    def auth_flow(self, request: httpx.Request):  # type: ignore[override]
        signed = signature_headers(
            request.method,
            request.url.path,
            dict(request.url.params),
            self.access_key_id,
            self.access_key_secret,
        )
        request.headers.update(signed)
        yield request


def server_auth(config: Settings) -> httpx.Auth | None:
    """Auth used for the startup schema fetch (no client request available)."""
    if config.access_key_id and config.access_key_secret:
        return AccessKeyAuth(config.access_key_id, config.access_key_secret)
    if config.api_token:
        return BearerAuth(config.api_token)
    return None


class BearerAuth(httpx.Auth):
    def __init__(self, token: str) -> None:
        self.token = token

    def auth_flow(self, request: httpx.Request):  # type: ignore[override]
        request.headers["Authorization"] = f"Bearer {self.token}"
        yield request


def forwarded_headers(
    config: Settings, client_headers: Mapping[str, str] | None
) -> dict[str, str]:
    """Forward a small, explicit allowlist of client headers to JumpServer."""
    if not client_headers:
        return {}
    lower = {k.lower(): v for k, v in client_headers.items()}
    result: dict[str, str] = {}
    for name in config.forward_headers.split(","):
        name = name.strip()
        if name and name.lower() in lower:
            result[name] = lower[name.lower()]
    return result


def client_authorization(
    config: Settings, client_headers: Mapping[str, str] | None
) -> str:
    """Return a usable per-call Authorization value, or '' if none."""
    if not client_headers:
        return ""
    lower = {k.lower(): v for k, v in client_headers.items()}
    value = lower.get("authorization", "").strip()
    if not value:
        return ""

    # A gateway credential authenticates the caller to this MCP server, not
    # to JumpServer. Never forward it as a JumpServer token.
    if config.api_key and value == f"Bearer {config.api_key}":
        return ""
    return value
