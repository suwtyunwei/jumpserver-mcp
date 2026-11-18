"""End-to-end check: real MCP handshake over SSE against a fake JumpServer."""

import asyncio
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import uvicorn

from jumpserver_mcp_server.config import Settings
from jumpserver_mcp_server.server import create_app


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

SCHEMA = {
    "openapi": "3.0.3",
    "info": {"title": "JumpServer", "version": "v5.1.1"},
    "paths": {
        "/api/v1/assets/hosts/": {
            "get": {
                "operationId": "assets_host-list",
                "tags": ["assets"],
                "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "ok"}},
            }
        },
        "/api/v1/assets/hosts/{id}/": {
            "get": {
                "operationId": "assets_host-read",
                "tags": ["assets"],
                "parameters": [{"name": "id", "in": "path", "schema": {"type": "string"}}],
                "responses": {"200": {"description": "ok"}},
            }
        },
        "/api/v1/authentication/auth/": {
            "post": {
                "operationId": "authentication_auth-create",
                "tags": ["authentication"],
                "responses": {"200": {"description": "ok"}},
            }
        },
    },
}


class FakeJumpServer(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        FakeJumpServer.seen.append(self.path)
        if self.path.startswith("/api/swagger.json"):
            body = json.dumps(SCHEMA).encode()
        elif self.path.startswith("/api/v1/assets/hosts/"):
            body = json.dumps([{"id": "abc", "hostname": "web-01"}]).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_mcp_handshake_and_tool_call():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeJumpServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    async def scenario():
        config = Settings(
            jumpserver_url=f"http://127.0.0.1:{port}",
            api_token="fake-token",
            log_level="ERROR",
        )
        app = create_app(config=config)
        mcp_port = free_port()
        uv_config = uvicorn.Config(
            app, host="127.0.0.1", port=mcp_port, log_level="error", access_log=False
        )
        uv_server = uvicorn.Server(uv_config)
        uv_thread = threading.Thread(target=uv_server.run, daemon=True)
        uv_thread.start()
        for _ in range(100):
            try:
                probe = httpx.get(f"http://127.0.0.1:{mcp_port}/healthz", timeout=0.3)
                if probe.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("MCP test server did not start")

        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{mcp_port}") as client:
            async with client.stream("GET", "/sse") as stream:
                assert stream.status_code == 200
                endpoint = None
                messages = []
                queue: asyncio.Queue = asyncio.Queue()

                async def reader():
                    async for line in stream.aiter_lines():
                        if line.startswith("data: "):
                            payload = line[len("data: ") :].strip()
                            if payload.startswith("/") and "session_id=" in payload:
                                nonlocal endpoint
                                endpoint = payload
                                continue
                            try:
                                msg = json.loads(payload)
                            except json.JSONDecodeError:
                                continue
                            messages.append(msg)
                            await queue.put(msg)

                task = asyncio.create_task(reader())

                async def wait_reply(request_id, timeout=5):
                    while True:
                        msg = await asyncio.wait_for(queue.get(), timeout)
                        if msg.get("id") == request_id:
                            return msg

                for _ in range(30):
                    if endpoint:
                        break
                    await asyncio.sleep(0.05)
                assert endpoint and "session_id=" in endpoint

                async def rpc(payload):
                    return await client.post(
                        endpoint,
                        json=payload,
                        headers={"Authorization": "Bearer fake-token"},
                    )

                init = await rpc(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "pytest", "version": "1"},
                        },
                    }
                )
                assert init.status_code in (200, 202)
                reply = await wait_reply(1)
                assert "result" in reply, reply

                await rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})

                await rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
                tools_reply = await wait_reply(2)
                names = {t["name"] for t in tools_reply["result"]["tools"]}
                assert "assets_host-list" in names
                assert "authentication_auth-create" not in names

                await rpc(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "assets_host-read", "arguments": {"id": "abc"}},
                    }
                )
                call_reply = await wait_reply(3)
                text = json.dumps(call_reply)
                assert "web-01" in text, call_reply
                assert "isError" not in call_reply.get("result", {}) or not call_reply["result"]["isError"]

                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            uv_server.should_exit = True

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()

    assert any(p == "/api/swagger.json" for p in FakeJumpServer.seen)
    assert any(p.startswith("/api/v1/assets/hosts/abc") for p in FakeJumpServer.seen)
    assert not any("/api/v1/api/v1" in p for p in FakeJumpServer.seen)
