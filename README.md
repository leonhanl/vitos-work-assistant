# Vito's Work Assistant

Vito's Work Assistant 是一个学习型企业 AI Assistant Demo。它现在包含一个极简
Vanilla JavaScript Web UI、Microsoft Entra 单租户登录、受 Token A 保护的 FastAPI，
以及既有的 DeepAgent → Streamable HTTP MCP → Microsoft Graph 链路。

本 README 是仓库唯一的项目文档入口，统一描述 Web、Agent 和 M365 MCP 的配置、运行方式
与安全边界。

## 当前架构

```text
Browser / Vanilla JS Web UI
   │
   │ Entra login（Authorization Code + PKCE）
   ▼
Microsoft Entra ID
   │ tenant-wide admin consent 已由管理员预先授予
   │ 用户仍需完成登录 / MFA / Conditional Access，但无需 user consent
   │
   │ Token A: Work Assistant API / access_as_user
   ▼
Web UI
   │
   │ Authorization: Bearer <Token A>
   ▼
Work Assistant FastAPI
   │ validate signature / issuer / tenant / audience / exp / nbf / scope
   │ CurrentUser = tid + oid；username 仅用于显示和日志
   ▼
DeepAgent
   │
   │ Streamable HTTP（不传 Token A）
   ▼
m365-mcp-http
   │
   │ existing Device Code identity
   ▼
Microsoft Graph
```

身份边界必须明确：

```text
Frontend identity = 当前 Alice / Bob
API identity      = 当前 Alice / Bob
Graph identity    = m365-mcp-http token cache 中的共享 Device Code 用户
```

因此 Alice 和 Bob 的 Token A 能让 API 区分两人，但 SharePoint 搜索结果与权限仍基于
同一个 MCP Device Code 用户，并不基于 Alice/Bob 自己的 Graph ACL。本阶段没有 OBO、
Graph Token B、Graph `/me`、MCP OAuth 或 per-user Graph identity。

这里的 **admin consent** 与登录是两件事：管理员代表 tenant 预先批准 delegated
permission，因此普通用户登录时不应看到 **Permissions requested** 页面；用户仍需完成
账号登录，以及 tenant 要求的 MFA 或 Conditional Access。

## 项目结构

```text
vitos-work-assistant/
├── apps/
│   ├── web/                  # Vite + Vanilla JS + MSAL Browser
│   └── agent/                # FastAPI + Token A validation + DeepAgent
├── services/
│   ├── m365-mcp-http/        # Streamable HTTP MCP + Device Code Flow
│   └── salesforce-mcp/       # 占位；本阶段未实现
├── libs/                     # 占位
└── infra/                    # 占位
```

API contract 保持不变：

```http
GET  /health    anonymous
GET  /me        Bearer Token A required
POST /chat      Bearer Token A required
```

`POST /chat` request：

```json
{
  "message": "How do I connect to VPN?",
  "thread_id": "optional-page-local-thread-id"
}
```

response：

```json
{
  "thread_id": "...",
  "answer": "...",
  "sources": [{ "name": "KB003 - Corporate VPN", "url": "https://..." }]
}
```

## 1. 手工配置 Microsoft Entra

代码不会用 Azure CLI、PowerShell、Terraform 或 Graph API 创建 registration。当前阶段
需要在同一个 Vito tenant 中手工创建三个应用：

| App Registration | OAuth 角色 | 当前权限 |
|---|---|---|
| `vitos-work-assistant-client` | Browser SPA public client | Work Assistant API `access_as_user` delegated permission |
| `vitos-work-assistant-api` | Protected Web API | 暴露 `access_as_user`；当前没有 Graph permission 或 credential |
| `vitos-m365-mcp` | Device Code public client | Microsoft Graph `User.Read`、`Files.Read.All` delegated permissions |

前两个应用组成 SPA → API 的 Token A 链路；第三个应用只服务于当前共享身份的 M365 MCP。
所有需要批准的 delegated permissions 都由管理员预先授予 tenant-wide admin consent，
不依赖 Alice/Bob 首次登录时执行 user consent。

### 1.1 `vitos-work-assistant-api`

进入 **Microsoft Entra admin center → Identity → Applications → App registrations →
New registration**：

1. Name 填 `vitos-work-assistant-api`。
2. Supported account types 选当前组织目录；Redirect URI 留空。
3. 在 Overview 记录 **Directory (tenant) ID** 和 API 的 **Application (client) ID**。
4. 进入 **Expose an API**，设置 Application ID URI：

   ```text
   api://<WORK_ASSISTANT_API_CLIENT_ID>
   ```

5. 在同一页面添加 delegated scope：

   ```text
   Scope name: access_as_user
   Who can consent: Admins only
   State: Enabled
   ```

   建议 display name 使用 `Access Vito's Work Assistant as the signed-in user`。完整 scope
   必须是：

   ```text
   api://<WORK_ASSISTANT_API_CLIENT_ID>/access_as_user
   ```

6. 进入 **Manifest**，在已有 `api` 对象内将 `requestedAccessTokenVersion` 设为数字
   `2`；不要覆盖 portal 刚创建的 `oauth2PermissionScopes`。

7. 进入 **API permissions**。如果新 registration 自动带有 Microsoft Graph
   `User.Read`，将它删除；完成后这里应没有任何 Microsoft Graph permission。

API registration 本阶段不需要 redirect URI、client secret、certificate 或 Microsoft
Graph permission。`Admins only` 不会删除 `access_as_user` scope 或后端的 scope 校验；它只
是不允许普通用户自行批准权限，权限必须由企业管理员在应用交付前统一批准。

### 1.2 `vitos-work-assistant-client`

再次进入 **App registrations → New registration**：

1. Name 填 `vitos-work-assistant-client`。
2. Supported account types 选当前组织目录。
3. 进入 **Authentication → Add a platform → Single-page application**，精确添加：

   ```text
   http://localhost:5173/redirect.html
   ```

   当前 MSAL Browser v5 使用这个专用 redirect bridge 页面处理登录、silent iframe 和
   logout return。URI 的 scheme、host、port、path 必须完全一致。不要把它登记成 Web
   platform；不要启用 implicit grant 的 access token / ID token 复选框。生产部署应增加
   对应的 HTTPS redirect URI，并对 bridge 页面返回 `Cache-Control: no-store`。

4. 进入 **API permissions → Add a permission → My APIs**，选择
   `vitos-work-assistant-api` → **Delegated permissions** → `access_as_user` →
   **Add permissions**。
5. 仍在 **API permissions** 页面，由管理员点击 **Grant admin consent for
   `<tenant>`**。确认 `access_as_user` 的 Status 显示为已为当前 tenant 授予，而不是等待
   Alice/Bob 首次登录时自行 consent。
6. 回到 `vitos-work-assistant-api` → **Expose an API → Authorized client
   applications → Add a client application**，填写
   `vitos-work-assistant-client` 的 Application (client) ID，勾选 `access_as_user` 并保存。
   这会把 SPA 写入 API 的 `preAuthorizedApplications`，明确预授权这个受信任的企业客户端。

若 API 未出现在 **My APIs**，确认两个 registration 位于同一 tenant，并让当前配置人员
成为两个 registration 的 Owner。不要把 SPA Client ID 加入 API manifest 的
`knownClientApplications`；该字段用于 bundled consent，当前 API 不代表 Alice/Bob 调用
下游 Graph，不需要捆绑两个应用的权限。API manifest 中的 `knownClientApplications` 应
保持空数组。

以上是本项目的默认企业部署策略：管理员预先审核 scope、授予 tenant-wide admin
consent，并由 API owner 预授权 SPA。普通用户只进行 Entra SSO，不应看到
**Permissions requested** 页面。管理员 consent 只批准客户端代表用户调用 API；后端仍会
验证 Token A 的 tenant、audience、签名、有效期与 `scp=access_as_user`。

如果只允许部分员工使用，再进入 **Enterprise applications** 中对应的 client enterprise
application，将 **Assignment required?** 设为 **Yes**，并分配允许使用的用户或安全组；
tenant-wide admin consent 本身不等于向所有员工开放业务访问。

SPA 是 public client，不能安全保存 credential。不要创建或放入前端 client secret、
certificate、Graph token、refresh token、backend secret 或 OBO secret。Tenant ID 与
client ID 属于公开客户端配置，不是 confidential secret。

### 1.3 `vitos-m365-mcp`

这是当前阶段专供 `m365-mcp-http` 使用的 public client registration。它与 Browser SPA
和 Work Assistant API 是不同的 OAuth client：

1. 创建单租户 App Registration，Name 填 `vitos-m365-mcp`，Redirect URI 留空。
2. 进入 **API permissions → Microsoft Graph → Delegated permissions**，添加
   `User.Read` 和 `Files.Read.All`。
3. 由管理员点击 **Grant admin consent for `<tenant>`**，确认两项 Graph delegated
   permissions 已为 tenant 授予；不要等待 Device Code 登录用户自行 consent。
4. 进入 **Authentication → Advanced settings**，启用 **Allow public client flows**。
5. 记录它自己的 Directory (tenant) ID 与 Application (client) ID，分别配置为
   `M365_TENANT_ID` 和 `M365_CLIENT_ID`。不要与 SPA/API Client ID 混用。
6. 不要创建 client secret 或 certificate；Device Code client 是 public client。

Graph 仍会根据这个 Device Code 登录用户自身的 SharePoint/OneDrive ACL 做 security
trimming。Admin consent 只批准应用请求这些 delegated permissions，不会提升用户自身
的数据权限。

## 2. 安装 Python 服务

需要 Python 3.11+：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e "./services/m365-mcp-http[dev]"
python -m pip install -e "./apps/agent[dev]"
```

### 启动 m365-mcp-http

```bash
cp services/m365-mcp-http/.env.example services/m365-mcp-http/.env
# 编辑 .env，填写 vitos-m365-mcp 自己的 tenant/client ID
set -a
source services/m365-mcp-http/.env
set +a
python -m m365_mcp.auth login
python -m m365_mcp.server
```

最后一条命令在终端 A 持续运行，默认 endpoint 是
`http://127.0.0.1:8001/mcp`。可配置项为：

```dotenv
M365_TENANT_ID=<Directory tenant ID>
M365_CLIENT_ID=<vitos-m365-mcp Application client ID>
# M365_TOKEN_CACHE_PATH=~/.cache/m365-mcp/msal_token_cache.json
MCP_HOST=127.0.0.1
MCP_PORT=8001
MCP_PATH=/mcp
```

`python -m m365_mcp.auth login` 通过 Device Code Flow 创建本地 MSAL cache；工具调用期间
只使用 `acquire_token_silent()`。Cache 包含敏感登录状态，不得提交、打印或发送给 LLM。
重新登录会替换旧 cache，并确定后续所有 MCP 请求共同使用的 Graph identity。

MCP 提供两个只读工具：

- `search_sharepoint(query, top=5)`：搜索 Graph `driveItem`。
- `read_document(drive_id, item_id)`：读取 `.docx`、UTF-8 `.txt` 或 `.md`；当前不支持 PDF。

当前 MCP HTTP endpoint 没有 authentication，默认只允许监听 `127.0.0.1`。不要将它直接
暴露给不受信任网络；生产暴露需要另行设计 TLS、service authentication 和网络边界。

### 启动 Work Assistant API

在终端 B：

```bash
source .venv/bin/activate
cp apps/agent/.env.example apps/agent/.env
# 编辑 LLM 与 Entra API 配置
set -a
source apps/agent/.env
set +a
uvicorn work_assistant.app:app --reload --host 127.0.0.1 --port 8000
```

关键值：

```dotenv
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=<secret>
LLM_MODEL=<tool-calling model>
ENTRA_TENANT_ID=<Directory tenant ID>
ENTRA_WORK_ASSISTANT_API_CLIENT_ID=<vitos-work-assistant-api Application client ID>
ENTRA_REQUIRED_SCOPE=access_as_user
M365_MCP_URL=http://127.0.0.1:8001/mcp
```

`GET http://127.0.0.1:8000/health` 应匿名返回 `{"status":"ok"}`；没有 Bearer 的
`POST /chat` 和 `GET /me` 应返回 `401`。Agent 启动时会建立一个共享 MCP connection；
MCP 连接或必要配置失败时，应用启动直接失败。

## 3. 启动 Web UI

需要 Node.js 20.19+ 或 22.12+。在终端 C：

```bash
cd apps/web
npm install
cp .env.example .env
# 编辑 .env，填写三个 ID 与完整 API scope
npm run dev
```

`apps/web/.env` 示例：

```dotenv
VITE_ENTRA_TENANT_ID=<Directory tenant ID>
VITE_ENTRA_CLIENT_ID=<vitos-work-assistant-client Application client ID>
VITE_WORK_ASSISTANT_API_CLIENT_ID=<vitos-work-assistant-api Application client ID>
VITE_WORK_ASSISTANT_API_SCOPE=api://<WORK_ASSISTANT_API_CLIENT_ID>/access_as_user
```

打开 `http://localhost:5173`。开发环境使用同源路径：

```text
/api/me   → http://127.0.0.1:8000/me
/api/chat → http://127.0.0.1:8000/chat
```

Vite proxy 会显式删除 `/api`，所以本地开发不需要 FastAPI CORS。`npm run build` 生成
静态产物；`npm run preview` 只预览静态构建，生产环境仍需由实际 hosting 将 `/api/*`
路由到 FastAPI（本阶段不实现生产 reverse proxy）。

### 一键启动本地手工测试环境

完成上述 Python、前端依赖和三个 `.env` 文件的配置后，可在仓库根目录运行：

```bash
./scripts/start-local.sh
```

脚本会按 `m365-mcp` → `Agent` → `Web` 的顺序启动，并等待前一个服务就绪后再启动下一个。
每次启动会清除 `logs/` 下的旧 `.log` 文件，新日志分别写入 `m365-mcp.log`、`agent.log`
和 `web.log`。按 `Ctrl+C` 可停止这三个服务。该脚本只拉起手工测试环境，不执行自动化测试。

## 4. Alice / Bob 手工验证

### Alice 登录与 `/me`

1. 打开 `http://localhost:5173`，点击 **Sign in with Microsoft**。
2. 使用 Alice 登录。Alice 可以完成账号选择、密码、MFA 或 Conditional Access，但不应
   看到 **Permissions requested** 用户 consent 页面。
3. 页面应显示 `account.name` / `account.username`，并轻量显示
   `API authenticated as alice@...`。
4. 浏览器实际调用 `GET /api/me`；后端返回 Alice 的 `oid`、tenant `tid` 与 username。

如果仍然出现 **Permissions requested (1 of 2 apps)** / **(2 of 2 apps)**，依次检查：

1. client 的 `access_as_user` 是否已经显示 admin consent granted；
2. client 和 API registration 的 **API permissions** 中是否残留 Microsoft Graph
   `User.Read`；当前项目两边都不需要它；
3. API 的 **Authorized client applications** 是否包含正确的 SPA Client ID 和
   `access_as_user` permission；
4. API manifest 的 `knownClientApplications` 是否误填了 SPA Client ID；本项目应为空。

管理员以后新增或改变 delegated permission 时，必须先重新审核并授予 admin consent，
再让普通用户使用新版本，避免把授权决定推给用户。

### Alice `/chat`

输入：

```text
Python 中 list 和 tuple 有什么区别？
```

前端在每次 API call 前运行 `acquireTokenSilent`，必要时才 fallback 到
`acquireTokenRedirect`，然后用 Token A 调用 `POST /api/chat`。页面显示 answer 与后端
实际返回的 sources，不解析 JWT、不自行刷新或长期保存 access token。

再输入：

```text
出差的时候怎么访问公司的内部系统？
```

调用链仍是 Alice Token A → FastAPI identifies Alice → DeepAgent → m365-mcp-http →
Graph as existing Device Code user。此时 SharePoint ACL 不是 Alice 自己的 ACL。

### Bob 与 Sign out

1. 点击 **Sign out**；页面调用 MSAL `logoutRedirect`，清理 MSAL account/token cache
   并完成 Entra server sign-out，不只是隐藏用户名。
2. 再次点击 **Sign in with Microsoft**，选择 Bob。
3. `/me` 应满足 `Bob oid != Alice oid`、`Bob tid == Alice tid`，username 为 Bob；Bob
   也可以调用 `/chat`。

## 5. 测试

```bash
cd apps/web
npm run build

cd ../..
python -m pytest apps/agent/tests
python -m pytest services/m365-mcp-http/tests
```

后端认证测试使用本地 RSA signing key，不访问真实 Entra、JWKS、Graph 或 LLM，覆盖
匿名 health、缺 token、无效 token、错误 audience / issuer / tenant、缺 scope，以及
有效 Alice/Bob identity。MCP 测试使用 mocked Graph 和本地 Streamable HTTP server；这些
测试不会验证真实 tenant 的 admin consent、Conditional Access 或 SharePoint ACL。

## 已知限制与下一阶段

当前 `thread_id` 只保存在 Agent 进程内存中，刷新页面可以丢失。它尚未绑定
authenticated `(tid, oid)`；持久化多用户版本必须实现 conversation ownership。

下一阶段 TODO（本阶段未实现）：

```text
Browser
   ↓ Token A
Work Assistant API
   │ validate Token A；不得把 token 放入 prompt / Agent state / 日志
   ↓ OBO（confidential client）
Graph Token B for current Alice / Bob
   ↓ request-scoped secure propagation
m365-mcp-http
   ↓
Microsoft Graph as Alice / Bob
```

实现 OBO 时，`vitos-work-assistant-api` 需要成为 confidential client，使用保存在后端的
certificate 或 client secret，并添加实际需要的 Microsoft Graph **delegated** permissions。
这些新增 Graph permissions 也必须由管理员预先授予 tenant-wide admin consent；当前为
SPA → Work Assistant API 授予的 `access_as_user` admin consent 不会自动覆盖 API → Graph。
完成管理员授权后，Alice/Bob 仍只需登录，不应分别执行 user consent。

代码层面还需要安全保留 Token A 作为 OBO assertion、按请求取得/传递 Token B，并让 MCP
调用绑定当前用户，不能继续复用当前全局 Device Code identity 或连接级共享 token。OBO
完成后可移除 `vitos-m365-mcp` 的 Device Code 登录链路；在此之前两种身份模型不得混用。

## 官方资料

- [Initialize MSAL Browser](https://learn.microsoft.com/en-us/entra/msal/javascript/browser/initialization)
- [Sign in users and configure the MSAL v5 redirect bridge](https://learn.microsoft.com/en-us/entra/msal/javascript/browser/login-user)
- [Acquire a token in a SPA](https://learn.microsoft.com/en-us/entra/identity-platform/scenario-spa-acquire-token)
- [Sign out users](https://learn.microsoft.com/en-us/entra/msal/javascript/browser/logout)
- [Microsoft identity platform access tokens](https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens)
- [Expose a web API scope](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-configure-app-expose-web-apis)
- [Configure a client to access a web API](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-configure-app-access-web-apis)
- [Permissions and consent overview](https://learn.microsoft.com/en-us/entra/identity-platform/permissions-consent-overview)
- [Grant tenant-wide admin consent](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent)
- [OAuth 2.0 on-behalf-of flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow)
- [Vite environment variables](https://vite.dev/guide/env-and-mode)
- [Vite dev server proxy](https://vite.dev/config/server-options.html#server-proxy)
