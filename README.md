# JumpServer MCP（精简修复版）

基于官方 `jumpserver/mcp`，修复了工具调用链路、鉴权与 JumpServer v4/v5 路径兼容问题，并把 OpenAPI 自动生成的约 1100 个操作裁剪为 **50 个常用 + 安全设置工具**（常用只读 26 + 安全设置 24）。

## 修了什么

| 问题 | 原因 | 本版处理 |
|---|---|---|
| 每次调用都 `TypeError` | 原代码给 `fastapi-mcp==0.3.3` 的 `_execute_api_tool()` 传了它不支持的 `base_url` 参数 | 自带 `ApiToolExecutor`，按上游函数真实签名实现，不再依赖该内部方法 |
| 请求 404 | 原代码 `base_url` 已含 `/api/v1`，而旧版 schema 的 path 又带一次 | `normalize_js_host()` 只保留 scheme+host，前缀只来自 schema |
| v5 返回 HTML 而非 JSON | v5 的 swagger path 不带 `/api/v1`（如 `/assets/hosts/`），真实 API 却在 `/api/v1/` 下，裸请求落到前端路由；AK/SK 签名也用错了 path | 发请求前若路径无前缀则自动补 `/api/v1`，签名用补齐后的路径；白名单匹配时对前缀做归一化，兼容 v4（带）与 v5（不带）两种 schema |
| 路径参数变成 `%7Bid%7D` | 部分接口未在 OpenAPI 声明 path 参数 | 自动从参数里替换 `{id}/{pk}/{user}` 等；缺参数直接报明确错误 |
| 鉴权透传失效 | 原代码读 `session._init_options` 私有属性，mcp 2.x 已删除 | 改用 fastapi-mcp 注入的 `_http_request_info`，从客户端 POST 请求头取 `Authorization`；不依赖私有属性 |
| 401 ASGI 报错 / 门禁冲突 | 原中间件响应不完整，且与 JumpServer 的 Authorization 抢同一个头 | 改用 `JSONResponse`，门禁独立用 `X-MCP-Gateway-Key` |
| 上下文爆炸 | 原代码开启 `describe_full_response_schema`，上千个工具的描述极大 | 默认关闭完整响应 schema；裁剪工具、截断超长响应 |
| 依赖漂移 | 官方只锁 `fastapi-mcp==0.3.3`，mcp SDK 会升到不兼容的 2.x | 锁定 `mcp==1.7.1` / `pydantic==2.11.7` / `fastapi==0.115.12` |

## 安装与启动

需要 Python 3.11+。

```bash
git clone <your-repo-url> jumpserver-mcp
cd jumpserver-mcp
python -m venv .venv
# Linux / macOS:
source .venv/bin/activate
# Windows PowerShell:  .venv\Scripts\Activate.ps1
pip install -e .
cp .env.example .env        # Windows: Copy-Item .env.example .env
# 编辑 .env，填 JUMPSERVER_URL 和 ACCESS_KEY_ID / ACCESS_KEY_SECRET，或 API_TOKEN
python main.py
```

服务默认监听 `0.0.0.0:8099`，SSE 地址 `http://127.0.0.1:8099/sse`，健康检查 `GET /healthz`（返回当前工具数）。

### Docker Compose

```bash
cp .env.example .env && vim .env     # 填 JumpServer 地址与凭据
docker compose build
docker compose up -d --force-recreate
curl -s http://127.0.0.1:8099/healthz
```

`docker compose restart` 不会重新读取 `.env`；改完配置要用 `up -d --force-recreate`。若连不上 JumpServer，服务会以空工具集启动（避免容器反复重启），填好配置重建即恢复。

## 客户端配置

```json
{
  "mcpServers": {
    "jumpserver": {
      "type": "sse",
      "url": "http://<host>:8099/sse",
      "headers": {
        "Authorization": "Bearer <JumpServer 用户的 Token>",
        "X-JMS-ORG": "<组织名，可选>"
      }
    }
  }
}
```

- 客户端传的 `Authorization` 会原样转发给 JumpServer，操作记在该用户名下；没传时用服务端 `.env` 的 Access Key / Token。
- 如果设了 `GATEWAY_KEY`，客户端还要加 `X-MCP-Gateway-Key: <key>`。网关 key 不会被转发给 JumpServer。
- Access Key 签名方式（`(request-target) accept date`，hmac-sha256）与 JumpServer v4.10/v5 一致。
- 只实现了 SSE 传输，不是 Streamable HTTP。

## 保留的工具（默认 50 个）

**常用（26 个，默认只读）**

- 用户 / 组织：`users/profile`、`users/users`、`users/groups`、`orgs/orgs/current`
- 资产：`assets/hosts`、`assets/assets`、`assets/nodes`、`assets/platforms`（含各自的详情接口）
- 账号：`accounts/accounts`、`accounts/account-templates`
- 授权：`perms/asset-permissions`、`perms/users/{user}/assets`
- 任务 / 会话：`ops/jobs`、`ops/job-executions`、`terminal/sessions`、`terminal/commands`
- 审计：`audits/login-logs`、`audits/operate-logs`、`audits/user-sessions`、`audits/password-change-logs`
- 信息：`settings/server-info`

**安全设置（24 个，默认可写）**

- `acls/login-acls`（登录 ACL）
- `acls/login-asset-acls`（登录资产 ACL）
- `acls/command-filter-acls`（命令过滤 ACL）
- `acls/command-groups`（命令组）
- `acls/connect-method-acls`（连接方式 ACL）

  以上每类含 `list` / `read` / `create` / `partial_update`。
- `settings/security/block-ip`、`settings/security/unlock-ip`（封禁 / 解锁 IP）
- `settings/setting`（默认只允许 GET，写需 `ALLOW_SETTINGS_WRITE=true`）
- `rbac/roles`、`rbac/permissions`（只读）

**明确不暴露**：登录取 token、MFA、改密码、Access Key / Secret、令牌、文件 / 录像下载、测试连接类接口。

白名单定义在 `jumpserver_mcp_server/toolset.py` 的 `COMMON_SPEC` / `SECURITY_SPEC`（一行一条 `路径|方法`），启动时编译为 `COMMON_RULES` / `SECURITY_RULES`。

## 自定义工具范围

`.env` 中的开关：

```ini
TOOLSET_PRESET=curated          # curated / common / security / all
ALLOW_COMMON_WRITES=false       # true 后常用类放开 POST/PUT/PATCH
ALLOW_DESTRUCTIVE_TOOLS=false   # true 后放开 DELETE
ALLOW_SETTINGS_WRITE=false      # true 后放开 settings/setting 的 PATCH
TOOLSET_EXTRA_RULES=/api/v1/tickets/tickets/=get;/api/v1/tickets/tickets/{id}/=get
TOOLSET_EXCLUDE_PATHS=/api/v1/audits/login-logs/
```

用 `TOOLSET_PRESET=all` 可恢复官方的「全部 OpenAPI 操作」（约 1100 个）。

不改服务、先看看会暴露哪些工具：

```bash
python tools_check.py
python tools_check.py --call assets_hosts_list --args '{"limit": 1}'
```

## 验证

```bash
python -m pytest -q
```

15 个测试全部通过，包含一次**真实 SSE 握手**：initialize → tools/list → tools/call，使用本地模拟 JumpServer，验证了工具裁剪、路径占位符替换、无双前缀、v5 路径补前缀、响应回填。

## 范围与已知限制

- 兼容 JumpServer v4.10.x 与 v5.1.x：两者 `/api/swagger.json` 与 drf-spectacular 0.29 一致；schema 路径带 / 不带 `/api/v1` 均已处理。
- 只实现 SSE 传输（与官方一致），没有 Streamable HTTP。
- 工具在启动时根据目标实例的 OpenAPI 生成；实例版本不同，最终工具数会在 50 附近浮动（路径不存在则自然不出现）。
- 依赖已锁版本，不要单独升级 `mcp` 到 2.x 或 `pydantic` 到 2.12+，否则会破坏 fastapi-mcp 0.3.3。
