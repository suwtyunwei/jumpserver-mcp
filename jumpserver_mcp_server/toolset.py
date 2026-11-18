"""Curated tool selection.

The upstream server exposes every operation in JumpServer's OpenAPI schema
(~900 tools). This module keeps a small subset: everyday read operations plus
ACL / security-settings operations. Passwords, MFA, token issuing, secret
reading and file/replay download endpoints are denied outright.

Defaults are deliberately conservative: common tools are read-only, ACL and
security-settings tools may write, DELETE always requires an explicit flag.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import Settings

PLACEHOLDER = r"\{[^/{}]+\}"


@dataclass(frozen=True)
class ToolRule:
    path: str
    methods: frozenset[str]
    group: str

    def matches(self, path: str, method: str) -> bool:
        # 兼容不同 JumpServer 版本：schema 路径可能带 /api/v1 前缀，也可能不带。
        # 规则统一按带前缀书写，匹配时对实际路径做前缀归一化。
        normalized = re.sub(r"^/api/v1(?=/)", "", path)
        rule_path = re.sub(r"^/api/v1(?=/)", "", self.path)
        return (
            bool(re.fullmatch(compile_path(rule_path), normalized))
            and method.lower() in self.methods
        )


def compile_path(spec: str) -> str:
    """Turn ``/api/v1/users/{id}/`` into a regex matching {id}, {pk}, {user}..."""
    parts = re.split(r"(\{[^}]+\})", spec)
    return "^" + "".join(
        PLACEHOLDER if part.startswith("{") and part.endswith("}") else re.escape(part)
        for part in parts
    ) + "$"


def _rules(spec: str, group: str) -> list[ToolRule]:
    result = []
    for line in spec.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        path, methods = line.split("|", 1)
        result.append(
            ToolRule(path.strip(), frozenset(m.strip().lower() for m in methods.split(",")), group)
        )
    return result


COMMON_SPEC = """
/api/v1/users/profile/ | get
/api/v1/users/users/ | get,post
/api/v1/users/users/{id}/ | get,put,patch,delete
/api/v1/users/groups/ | get
/api/v1/orgs/orgs/current/ | get
/api/v1/assets/hosts/ | get,post
/api/v1/assets/hosts/{id}/ | get,put,patch,delete
/api/v1/assets/assets/ | get
/api/v1/assets/assets/{id}/ | get
/api/v1/assets/nodes/ | get
/api/v1/assets/platforms/ | get
/api/v1/accounts/accounts/ | get,post
/api/v1/accounts/accounts/{id}/ | get,put,patch,delete
/api/v1/accounts/account-templates/ | get
/api/v1/perms/asset-permissions/ | get,post
/api/v1/perms/asset-permissions/{id}/ | get,put,patch,delete
/api/v1/perms/users/{user}/assets/ | get
/api/v1/ops/jobs/ | get
/api/v1/ops/job-executions/ | get
/api/v1/terminal/sessions/ | get
/api/v1/terminal/commands/ | get
/api/v1/audits/login-logs/ | get
/api/v1/audits/operate-logs/ | get
/api/v1/audits/user-sessions/ | get
/api/v1/audits/password-change-logs/ | get
/api/v1/settings/server-info/ | get
"""

SECURITY_SPEC = """
/api/v1/acls/login-acls/ | get,post
/api/v1/acls/login-acls/{id}/ | get,patch
/api/v1/acls/command-filter-acls/ | get,post
/api/v1/acls/command-filter-acls/{id}/ | get,patch
/api/v1/acls/login-asset-acls/ | get,post
/api/v1/acls/login-asset-acls/{id}/ | get,patch
/api/v1/acls/connect-method-acls/ | get,post
/api/v1/acls/connect-method-acls/{id}/ | get,patch
/api/v1/acls/command-groups/ | get,post
/api/v1/acls/command-groups/{id}/ | get,patch
/api/v1/settings/security/block-ip/ | post
/api/v1/settings/security/unlock-ip/ | post
/api/v1/settings/setting/ | get,patch
/api/v1/rbac/roles/ | get
/api/v1/rbac/permissions/ | get
"""

COMMON_RULES = _rules(COMMON_SPEC, "常用")
SECURITY_RULES = _rules(SECURITY_SPEC, "安全设置")

DENY_PATTERNS = [
    re.compile(p)
    for p in (
        r"/authentication/(auth|tokens|mfa|password|access-keys|access-tokens|confirm)",
        r"/(password|mfa|pubkey|unblock|secret-key|private-key)(/|$)",
        r"/(account-secrets|account-template-secrets|access-keys|access-tokens)",
        r"/(file|files|upload|download|replay|replay-index|encrypted-config)",
        r"/settings/(mail|ldap|wecom|dingtalk|feishu|lark|slack|sms|vault|chatai)/testing",
        r"/settings/(vault/sync|vault/restore|logo)",
        r"/terminal/(terminal-registrations|registration)",
    )
]


def _extra_rules(raw: str) -> list[ToolRule]:
    """``/api/v1/foo/{id}/=get,patch`` separated by semicolons."""
    rules = []
    for item in raw.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        path, methods = item.split("=", 1)
        rules.append(
            ToolRule(path.strip(), frozenset(m.strip().lower() for m in methods.split(",")), "自定义")
        )
    return rules


def select_tools(
    tools: list[Any], operation_map: dict[str, dict[str, Any]], config: Settings
) -> tuple[list[Any], dict[str, dict[str, Any]], dict[str, Any]]:
    """Filter tools and their operation map without mutating the inputs."""
    preset = config.toolset_preset.strip().lower()
    stats: dict[str, Any] = {
        "total": len(tools),
        "kept": 0,
        "denied": 0,
        "unmatched": 0,
        "writes": 0,
        "destructive": 0,
    }
    if preset == "all":
        stats["kept"] = len(tools)
        return tools, operation_map, stats

    if preset == "common":
        rules = list(COMMON_RULES)
    elif preset == "security":
        rules = list(SECURITY_RULES)
    else:
        rules = list(COMMON_RULES) + list(SECURITY_RULES)
    rules += _extra_rules(config.toolset_extra_rules)

    exclude = [re.compile(p) for p in config.toolset_exclude_paths.split(",") if p.strip()]

    kept_tools: list[Any] = []
    kept_ops: dict[str, dict[str, Any]] = {}
    groups: dict[str, int] = {}

    for tool in tools:
        op = operation_map.get(tool.name) or {}
        path = str(op.get("path", ""))
        method = str(op.get("method", "")).lower()

        if any(p.search(path) for p in DENY_PATTERNS) or any(p.search(path) for p in exclude):
            stats["denied"] += 1
            continue

        matched = next((rule for rule in rules if rule.matches(path, method)), None)
        if matched is None:
            stats["unmatched"] += 1
            continue
        if method == "delete" and not config.allow_destructive_tools:
            stats["destructive"] += 1
            continue
        if matched.group == "常用" and method != "get" and not config.allow_common_writes:
            stats["writes"] += 1
            continue
        if (
            re.fullmatch(r"(?:/api/v1)?/settings/setting/", path)
            and method in ("patch", "put")
            and not config.allow_settings_write
        ):
            stats["destructive"] += 1
            continue

        kept_tools.append(tool)
        kept_ops[tool.name] = op
        groups[matched.group] = groups.get(matched.group, 0) + 1

    stats["kept"] = len(kept_tools)
    stats["groups"] = groups
    return kept_tools, kept_ops, stats
