"""Execute a curated MCP tool by calling the matching JumpServer REST API."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Mapping
from urllib.parse import quote

import httpx
import mcp.types as types

from .auth import client_authorization, forwarded_headers, signature_headers
from .config import Settings

logger = logging.getLogger(__name__)

PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")
PARAM_ALIASES = {
    "id": ("pk", "uuid", "object_id"),
    "pk": ("id", "uuid"),
    "user": ("user_id", "username", "id"),
    "user_id": ("user", "id"),
    "node_id": ("node", "node_pk", "id"),
    "asset_id": ("asset", "asset_pk", "id"),
    "account_id": ("account", "id"),
    "perm_user_id": ("user_id", "id"),
    "label": ("label_id",),
    "res_type": ("resource_type",),
    "backend": ("sms_backend",),
    "name": ("id",),
}


class ApiToolExecutor:
    def __init__(
        self,
        base_url: str,
        operation_map: dict[str, dict[str, Any]],
        config: Settings,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.operation_map = operation_map
        self.config = config

    @staticmethod
    def _take_argument(arguments: dict[str, Any], name: str) -> Any:
        for candidate in (name,) + PARAM_ALIASES.get(name, ()):
            if candidate in arguments:
                return arguments.pop(candidate)
        return None

    def _fill_path(self, path: str, parameters: list[dict[str, Any]], arguments: dict[str, Any]) -> str:
        declared = {p.get("name") for p in parameters if p.get("in") == "path"}
        for name in sorted(declared, key=lambda n: (n != "id", len(str(n))), reverse=False):
            value = self._take_argument(arguments, str(name))
            if value is not None:
                path = path.replace("{" + str(name) + "}", quote(str(value), safe=""))

        # JumpServer's schema sometimes omits path parameters (e.g.
        # perms_asset-permissions_read). Never send a literal %7Bid%7D.
        for name in PLACEHOLDER_RE.findall(path):
            value = self._take_argument(arguments, name)
            if value is None:
                raise ValueError(
                    f"缺少路径参数 {name!r}，请提供 {name} 或 id/pk 之一"
                )
            path = path.replace("{" + name + "}", quote(str(value), safe=""), 1)
        return path

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        client_headers: Mapping[str, str] | None = None,
    ) -> list[types.TextContent]:
        operation = self.operation_map.get(name)
        if operation is None:
            raise ValueError(f"未知工具: {name}")

        path = str(operation.get("path", ""))
        method = str(operation.get("method", "get")).lower()
        if method not in {"get", "post", "put", "patch", "delete"}:
            raise ValueError(f"不支持的 HTTP 方法: {method}")

        # 不同 JumpServer 版本的 swagger 路径前缀不一致：有的带 /api/v1，
        # 有的（如 v5）不带。真实 REST API 一律在 /api/v1 下，发请求前补齐，
        # 签名也必须基于补齐后的 path，否则会落到前端 HTML 路由或 401。
        if not path.startswith("/api/v1/") and not path.startswith("/api/swagger"):
            path = "/api/v1" + path

        args = dict(arguments or {})
        parameters = operation.get("parameters") or []
        path = self._fill_path(path, parameters, args)

        query: dict[str, Any] = {}
        headers: dict[str, str] = {}
        for param in parameters:
            pname = param.get("name")
            if not pname or pname not in args:
                continue
            where = param.get("in")
            if where == "query":
                query[pname] = args.pop(pname)
            elif where == "header" and pname.lower() != "authorization":
                headers[pname] = str(args.pop(pname))

        # For reads, tolerate undeclared filter arguments by passing them as
        # query params instead of silently dropping them.
        if method in {"get", "delete"}:
            for key, value in list(args.items()):
                query.setdefault(key, value)
            args = {}

        headers.update(forwarded_headers(self.config, client_headers))

        authorization = client_authorization(self.config, client_headers)
        if authorization:
            headers["Authorization"] = authorization
        elif self.config.access_key_id and self.config.access_key_secret:
            headers.update(
                signature_headers(
                    method,
                    path,
                    query,
                    self.config.access_key_id,
                    self.config.access_key_secret,
                )
            )
        elif self.config.api_token:
            headers["Authorization"] = f"Bearer {self.config.api_token}"

        body = args if method in {"post", "put", "patch"} and args else None
        logger.debug("%s %s params=%s", method.upper(), path, list(query))

        timeout = httpx.Timeout(self.config.http_timeout)
        async with httpx.AsyncClient(
            base_url=self.base_url,
            verify=self.config.verify_ssl,
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            response = await client.request(method, path, params=query, headers=headers, json=body)

        if response.status_code >= 400:
            detail = response.text[:500]
            raise RuntimeError(f"JumpServer API {method.upper()} {path} 返回 {response.status_code}: {detail}")

        if not response.content:
            return [types.TextContent(type="text", text="ok")]

        try:
            payload = response.json()
            text = json.dumps(payload, ensure_ascii=False, indent=2)
        except (ValueError, json.JSONDecodeError):
            text = response.text

        limit = self.config.max_response_chars
        if limit > 0 and len(text) > limit:
            text = text[:limit] + f"\n... [结果已截断，共 {len(text)} 字符]"
        return [types.TextContent(type="text", text=text)]
