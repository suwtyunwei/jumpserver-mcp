"""FastAPI app exposing a curated JumpServer MCP server over SSE."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi_mcp import FastApiMCP
from fastapi_mcp.openapi.convert import convert_openapi_to_mcp_tools
from fastapi_mcp.server import LowlevelMCPServer
from fastapi_mcp.transport.sse import FastApiSseTransport
import mcp.types as types

from .auth import normalize_js_host, server_auth
from .config import Settings, settings
from .executor import ApiToolExecutor
from .toolset import select_tools

logger = logging.getLogger(__name__)


def setup_logging(level: str, debug: bool = False) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not debug:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)


def fetch_swagger_json(config: Settings) -> dict[str, Any]:
    url = config.swagger_url or f"{normalize_js_host(config)}/api/swagger.json"
    kwargs: dict[str, Any] = {
        "verify": config.verify_ssl,
        "timeout": config.schema_timeout,
        "follow_redirects": True,
    }
    auth = server_auth(config)
    if auth:
        kwargs["auth"] = auth
    logger.info("Fetching JumpServer OpenAPI schema from %s", url)
    response = httpx.get(url, **kwargs)
    if response.status_code != 200:
        raise RuntimeError(
            f"拉取 JumpServer OpenAPI 失败: HTTP {response.status_code} - {response.text[:300]}"
        )
    schema = response.json()
    if not isinstance(schema, dict) or "paths" not in schema:
        raise RuntimeError("JumpServer OpenAPI 响应中没有 paths 字段，请检查地址和认证凭据")
    return schema


class JumpServerMCP(FastApiMCP):
    def __init__(self, app: FastAPI, *, config: Settings, swagger_json: dict[str, Any]) -> None:
        self.config = config
        self.swagger_json = swagger_json
        self.sse_transport: FastApiSseTransport | None = None
        self.executor: ApiToolExecutor | None = None
        super().__init__(
            app,
            name="JumpServer API MCP",
            description="常用资产/账号/授权操作与 JumpServer 安全设置",
            describe_all_responses=config.describe_all_responses,
            describe_full_response_schema=config.describe_full_response_schema,
            # Only used by the base class internally; real calls use our executor.
            http_client=httpx.AsyncClient(base_url="http://apiserver"),
        )

    def setup_server(self) -> None:
        all_tools, all_operations = convert_openapi_to_mcp_tools(
            self.swagger_json,
            describe_all_responses=self._describe_all_responses,
            describe_full_response_schema=self._describe_full_response_schema,
        )
        self.tools, self.operation_map, stats = select_tools(all_tools, all_operations, self.config)
        self.executor = ApiToolExecutor(normalize_js_host(self.config), self.operation_map, self.config)
        logger.info(
            "工具裁剪: 总计 %s, 保留 %s, 敏感端点 %s, 未命中白名单 %s, 写操作默认禁用 %s, 删除/设置写默认禁用 %s; 分组=%s",
            stats["total"],
            stats["kept"],
            stats["denied"],
            stats["unmatched"],
            stats["writes"],
            stats["destructive"],
            stats.get("groups", {}),
        )

        mcp_server: LowlevelMCPServer = LowlevelMCPServer(self.name, self.description)

        @mcp_server.list_tools()
        async def handle_list_tools() -> list[types.Tool]:
            return self.tools

        @mcp_server.call_tool()
        async def handle_call_tool(
            name: str,
            arguments: dict[str, Any],
            http_request_info: Any = None,
        ) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
            headers = getattr(http_request_info, "headers", None)
            assert self.executor is not None
            return await self.executor.execute(name, arguments, headers)

        self.server = mcp_server

    def is_auth_session(self, session_id: str) -> bool:
        if not session_id or self.sse_transport is None:
            return False
        try:
            parsed = UUID(session_id)
            return parsed in self.sse_transport._read_stream_writers
        except (ValueError, AttributeError, TypeError):
            return False

    def mount(self, router: Any = None, mount_path: str = "/sse") -> None:
        mount_path = "/" + mount_path.strip("/")
        router = router or self.fastapi
        if isinstance(router, FastAPI):
            base_path = router.root_path
        else:
            base_path = self.fastapi.root_path + router.prefix

        sse_transport = FastApiSseTransport(f"{base_path}{mount_path}/messages/")
        self.sse_transport = sse_transport

        @router.get(mount_path, include_in_schema=False)
        async def handle_mcp_connection(request: Request):
            async with sse_transport.connect_sse(
                request.scope, request.receive, request._send
            ) as (reader, writer):
                await self.server.run(reader, writer, self.server.create_initialization_options())

        @router.post(f"{mount_path}/messages/", include_in_schema=False)
        async def handle_post_message(request: Request):
            return await sse_transport.handle_fastapi_post_message(request)

        logger.info("JumpServer MCP 已挂载: %s", mount_path)


def create_app(
    config: Settings | None = None,
    swagger_json: dict[str, Any] | None = None,
) -> FastAPI:
    cfg = config or settings
    setup_logging(cfg.log_level, cfg.debug)
    if swagger_json is None:
        try:
            swagger_json = fetch_swagger_json(cfg)
        except Exception as exc:  # 占位配置/凭据未就绪时避免容器崩溃重启
            logger.warning(
                "无法拉取 JumpServer OpenAPI schema，以空工具集启动；"
                "填好 .env 后重启即可自动恢复: %s",
                exc,
            )
            swagger_json = {
                "openapi": "3.0.0",
                "info": {"title": "placeholder", "version": "0"},
                "paths": {},
            }

    app = FastAPI(title="JumpServer MCP", version="0.2.0")
    mcp = JumpServerMCP(app, config=cfg, swagger_json=swagger_json)
    mcp.mount(mount_path=cfg.base_path)
    app.state.mcp = mcp

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "tools": len(mcp.tools)}

    @app.middleware("http")
    async def check_gateway_key(request: Request, call_next) -> Response:
        if request.url.path == "/healthz":
            return await call_next(request)

        session_id = request.query_params.get("session_id")
        if session_id:
            if mcp.is_auth_session(session_id):
                return await call_next(request)
            return JSONResponse(status_code=401, content={"detail": "Invalid session_id"})

        if cfg.gateway_key:
            if request.headers.get("x-mcp-gateway-key") != cfg.gateway_key:
                return JSONResponse(status_code=401, content={"detail": "Invalid X-MCP-Gateway-Key"})
        elif cfg.api_key:
            if request.headers.get("authorization") != f"Bearer {cfg.api_key}":
                return JSONResponse(status_code=401, content={"detail": "Invalid API key"})
        return await call_next(request)

    return app
