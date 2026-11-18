"""Configuration loaded from environment variables / .env file."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Server
    server_host: str = "0.0.0.0"
    server_port: int = 8099
    base_path: str = "/sse"
    log_level: str = "INFO"
    debug: bool = False

    # JumpServer connection. host is normalized to a scheme+host (no /api/v1 suffix).
    jumpserver_url: str = ""
    api_base_url: str = ""
    swagger_url: str = ""
    verify_ssl: bool = False
    http_timeout: float = 60.0
    schema_timeout: float = 120.0

    # JumpServer auth: Access Key (preferred) or short-lived Bearer token
    access_key_id: str = ""
    access_key_secret: str = ""
    api_token: str = ""
    forward_headers: str = "X-JMS-ORG"

    # Front-door auth for the MCP endpoint itself
    gateway_key: str = ""
    api_key: str = ""  # legacy: Bearer on the MCP endpoint

    # Tool selection
    toolset_preset: str = "curated"  # curated | common | security | all
    toolset_extra_rules: str = ""
    toolset_exclude_paths: str = ""
    allow_common_writes: bool = False
    allow_destructive_tools: bool = False
    allow_settings_write: bool = False

    # Response shaping
    describe_all_responses: bool = False
    describe_full_response_schema: bool = False
    max_response_chars: int = 20000


settings = Settings()
