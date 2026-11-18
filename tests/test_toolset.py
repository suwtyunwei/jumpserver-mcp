from types import SimpleNamespace

from jumpserver_mcp_server.config import Settings
from jumpserver_mcp_server.toolset import select_tools


def tool(name):
    return SimpleNamespace(name=name)


def op(path, method="get"):
    return {"path": path, "method": method, "parameters": []}


OPS = {
    "assets_hosts_list": op("/api/v1/assets/hosts/"),
    "assets_hosts_create": op("/api/v1/assets/hosts/", "post"),
    "assets_hosts_read": op("/api/v1/assets/hosts/{id}/"),
    "assets_hosts_delete": op("/api/v1/assets/hosts/{id}/", "delete"),
    "acls_login": op("/api/v1/acls/login-acls/"),
    "acls_login_patch": op("/api/v1/acls/login-acls/{id}/", "patch"),
    "settings_security": op("/api/v1/settings/security/block-ip/", "post"),
    "settings_all": op("/api/v1/settings/setting/", "patch"),
    "auth_token": op("/api/v1/authentication/auth/", "post"),
    "account_secrets": op("/api/v1/accounts/account-secrets/"),
    "password_logs": op("/api/v1/audits/password-change-logs/"),
    "unknown_api": op("/api/v1/some/new/api/"),
    "file_download": op("/api/v1/terminal/sessions/{id}/replay/"),
}


def run(config):
    tools = [tool(name) for name in OPS]
    return select_tools(tools, OPS, config)


def test_curated_keeps_common_and_security():
    kept, ops, stats = run(Settings())
    names = {t.name for t in kept}
    assert {"assets_hosts_list", "assets_hosts_read", "acls_login", "acls_login_patch", "settings_security"} <= names
    assert set(ops) == names
    assert stats["total"] == len(OPS)
    assert stats["kept"] == len(names)
    assert stats["denied"] >= 3
    assert stats["unmatched"] >= 1
    assert stats["writes"] >= 1  # common writes disabled by default
    assert stats["destructive"] >= 2  # delete + settings/setting patch
    assert "assets_hosts_create" not in names


def test_denied_endpoints_never_exposed():
    kept, _, _ = run(Settings())
    names = {t.name for t in kept}
    assert not {"auth_token", "account_secrets", "file_download"} & names
    assert "password_logs" in names  # audit log, not a password-change endpoint


def test_preset_all_and_flags():
    _, _, stats = run(Settings(toolset_preset="all"))
    assert stats["kept"] == len(OPS)

    kept, _, _ = run(
        Settings(
            allow_destructive_tools=True,
            allow_settings_write=True,
            allow_common_writes=True,
        )
    )
    names = {t.name for t in kept}
    assert "assets_hosts_delete" in names
    assert "assets_hosts_create" in names
    assert "settings_all" in names


def test_extra_rules():
    kept, _, _ = run(Settings(toolset_extra_rules="/api/v1/some/new/api/=get"))
    assert "unknown_api" in {t.name for t in kept}


def test_security_preset():
    kept, _, _ = run(Settings(toolset_preset="security"))
    names = {t.name for t in kept}
    assert "acls_login" in names
    assert "assets_hosts_list" not in names
