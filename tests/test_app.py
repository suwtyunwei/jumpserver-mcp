from fastapi.testclient import TestClient

from jumpserver_mcp_server.config import Settings
from jumpserver_mcp_server.server import create_app


SCHEMA = {
    "openapi": "3.0.3",
    "info": {"title": "JumpServer", "version": "v5"},
    "paths": {
        "/api/v1/assets/hosts/": {
            "get": {
                "operationId": "assets_host-list",
                "tags": ["assets"],
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


def test_health_and_curated_tools():
    app = create_app(config=Settings(jumpserver_url="https://jms.example.com"), swagger_json=SCHEMA)
    assert len(app.state.mcp.tools) == 1
    assert app.state.mcp.tools[0].name == "assets_host-list"
    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["tools"] == 1


def test_gateway_guard_and_bad_session():
    app = create_app(
        config=Settings(jumpserver_url="https://jms.example.com", gateway_key="secret-gw"),
        swagger_json=SCHEMA,
    )
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/sse").status_code == 401
        assert client.post("/sse/messages/?session_id=not-a-uuid").status_code == 401
