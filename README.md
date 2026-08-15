# Vito's Work Assistant

Vito's Work Assistant 是一个学习型企业 AI Assistant Demo。它现在包含一个极简
Vanilla JavaScript Web UI、Microsoft Entra 单租户登录、受 Token A 保护的 FastAPI，
以及使用两段 OBO 保持当前用户身份的 Pydantic AI → Streamable HTTP MCP 链路。

本 README 描述 Work Assistant 的整体配置、运行方式与集成边界；待迁出的 M365 MCP
同时维护自己的独立文档：[`vitos-m365-mcp/README.md`](vitos-m365-mcp/README.md)。

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
   │ 每次 /chat：OBO Token A → Token M
   ▼
Pydantic AI Agent
   │
   │ Streamable HTTP；Token M: aud = vitos-m365-mcp
   ▼
vitos-m365-mcp
   │
   │ validate Token M；每次工具调用 OBO Token M → Token G
   ▼
Microsoft Graph as current Alice / Bob（Token G）
```

身份边界必须明确：

```text
Frontend identity = 当前 Alice / Bob
API identity      = 当前 Alice / Bob
MCP identity      = 当前 Alice / Bob（OBO Token M）
Graph identity    = 当前 Alice / Bob（OBO Token G）
```

因此 SharePoint 搜索结果与文档读取会按 Alice/Bob 自己的 Graph ACL 执行 security
trimming。由于受保护的 MCP 在 `initialize` 和 `tools/list` 阶段就要求认证，每次 `/chat`
都会先获取 Token M；只有模型实际调用 M365 工具时，MCP 才继续获取 Token G。Token A、
Token M、Token G 都不会进入 prompt、对话历史、工具参数或日志。

Agent 进程只创建一个共享的 Pydantic AI `Agent`；用户状态不放在 Agent 对象里，而是用
`(tid, oid, thread_id)` 索引独立的内存消息历史。`apps/agent/skills/` 下每个子目录中的
`SKILL.md` 会在启动时被发现，模型最初只看到 skill 的名称和描述；调用
`load_capability` 后才加载完整正文。修改或新增 skill 后需要重启 Agent 进程。

这里的 **admin consent** 与登录是两件事：管理员代表 tenant 预先批准 delegated
permission，因此普通用户登录时不应看到 **Permissions requested** 页面；用户仍需完成
账号登录，以及 tenant 要求的 MFA 或 Conditional Access。

## 项目结构

```text
vitos-work-assistant/
├── apps/
│   ├── web/                  # Vite + Vanilla JS + MSAL Browser
│   └── agent/                # FastAPI + Token A validation + Pydantic AI
├── vitos-m365-mcp/           # 自包含、待迁出为独立 repo 的 MCP service
└── services/
    └── salesforce-mcp-http/  # 占位；即将实现
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
| `vitos-work-assistant-api` | Protected Web API / confidential client | 暴露自己的 `access_as_user`；调用 MCP 的 `access_as_user` delegated permission |
| `vitos-m365-mcp` | Protected MCP resource / confidential client | 暴露自己的 `access_as_user`；Microsoft Graph `Files.Read.All` delegated permission |

三个应用组成 Token A → Token M → Token G 的委托链路。
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

7. 进入 **API permissions → Add a permission → My APIs**，添加
   `vitos-m365-mcp/access_as_user` delegated permission，并由管理员授予 tenant-wide
   admin consent。
8. 进入 **Certificates & secrets → Client secrets**，为本地 MVP 创建 secret，并将它只配置
   到 Agent 后端。不要把 secret 提交到仓库或发送到浏览器。生产环境应改用 certificate。

API registration 不需要 redirect URI。`Admins only` 不会删除 `access_as_user` scope 或
后端的 scope 校验；它只是不允许普通用户自行批准权限，权限必须由企业管理员在应用交付
前统一批准。

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
成为两个 registration 的 Owner。API manifest 中的 `knownClientApplications` 保持空
数组；本项目由管理员分别为 SPA → API、API → MCP、MCP → Graph 三段委托权限预先
授予 consent。

以上是本项目的默认企业部署策略：管理员预先审核 scope、授予 tenant-wide admin
consent，并由 API owner 预授权 SPA。普通用户只进行 Entra SSO，不应看到
**Permissions requested** 页面。三段 permission/consent 彼此独立；Agent 仍会验证 Token A，
MCP 则单独验证 Token M。

如果只允许部分员工使用，再进入 **Enterprise applications** 中对应的 client enterprise
application，将 **Assignment required?** 设为 **Yes**，并分配允许使用的用户或安全组；
tenant-wide admin consent 本身不等于向所有员工开放业务访问。

SPA 是 public client，不能安全保存 credential。不要创建或放入前端 client secret、
certificate、Graph token、refresh token、backend secret 或 OBO secret。Tenant ID 与
client ID 属于公开客户端配置，不是 confidential secret。

### 1.3 `vitos-m365-mcp`

按 [`vitos-m365-mcp/README.md`](vitos-m365-mcp/README.md) 创建独立的单租户
registration，并完成以下关键配置：

1. 暴露 `api://<MCP_CLIENT_ID>/access_as_user` delegated scope。
2. 添加 Microsoft Graph `Files.Read.All` delegated permission，并授予 admin consent。
3. 创建仅供 MCP 服务端执行 Token M → Token G OBO 的 client secret。
4. 在 **Authorized client applications** 中预授权 `vitos-work-assistant-api` 使用
   `access_as_user`。

Graph permission 和 MCP secret 都属于 MCP registration，不属于 SPA，也不要放回 Agent
registration。

### 1.4 旧 Device Code public client registration

现有集成不再使用早期为 Device Code Flow 创建的 public client registration 或本地
MSAL token cache。确认当前流程手工验证通过后，可以删除旧 registration；这里所说的
旧 registration 与现在的 `vitos-m365-mcp` 服务目录不是同一个概念。

## 2. 安装 Python 服务

需要 Python 3.11+：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e "./vitos-m365-mcp[dev]"
python -m pip install -e "./apps/agent[dev]"
```

### 启动 vitos-m365-mcp

```bash
cp vitos-m365-mcp/.env.example vitos-m365-mcp/.env
set -a
source vitos-m365-mcp/.env
set +a
python -m m365_mcp.server
```

最后一条命令在终端 A 持续运行，默认 endpoint 是
`http://127.0.0.1:8001/mcp`，匿名健康检查为
`http://127.0.0.1:8001/health`。可配置项为：

```dotenv
MCP_HOST=127.0.0.1
MCP_PORT=8001
MCP_PATH=/mcp
```

MCP 不保存登录状态。`/mcp` 的 `initialize`、`tools/list` 和 `tools/call` 都要求 Agent 在
HTTP `Authorization` header 中传入当前 `/chat` 请求通过 OBO 获取的 Token M；MCP 只在
`tools/call` 时继续将 Token M 交换为 Graph Token G。

MCP 提供两个只读工具：

- `search_sharepoint(query, top=5)`：搜索 Graph `driveItem`。
- `read_document(drive_id, item_id)`：读取 `.docx`、UTF-8 `.txt` 或 `.md`；当前不支持 PDF。

MCP endpoint 受 Entra Token M 保护，但本地默认仍只监听 `127.0.0.1`。跨主机部署还需要
TLS 和相应的网络边界。

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
ENTRA_WORK_ASSISTANT_API_CLIENT_SECRET=<backend-only secret>
ENTRA_REQUIRED_SCOPE=access_as_user
M365_MCP_URL=http://127.0.0.1:8001/mcp
ENTRA_MCP_CLIENT_ID=<vitos-m365-mcp Application client ID>
# 仅当 MCP 使用自定义 Application ID URI 时设置：
# ENTRA_MCP_SCOPE=api://mcp.internal.example/access_as_user
```

`GET http://127.0.0.1:8000/health` 应匿名返回 `{"status":"ok"}`；没有 Bearer 的
`POST /chat` 和 `GET /me` 应返回 `401`。Agent 启动时不会连接 MCP；每次 `/chat` 先执行
Token A → Token M OBO，再用 Token M 建立独立的 stateless MCP session。MCP 不可用时该次
请求失败，不影响 Agent API 进程启动。

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
2. client registration 是否误加 Microsoft Graph 权限；Graph `Files.Read.All` 应只添加
   在 API registration；
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

调用链是 Alice Token A → FastAPI identifies Alice → Agent OBO 获取 Alice Token M →
Pydantic AI 以 Token M 初始化 MCP → MCP 在工具调用时 OBO 获取 Alice Token G → Graph as
Alice。SharePoint ACL 应是 Alice 自己的 ACL。普通 Python 问题也会为 MCP 初始化获取
Token M，但不会触发 MCP → Graph 的第二段 OBO。

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
python -m pytest vitos-m365-mcp/tests
```

后端认证测试使用本地 RSA signing key，不访问真实 Entra、JWKS、Graph 或 LLM，覆盖
匿名 health、缺 token、无效 token、错误 audience / issuer / tenant、缺 scope，以及
有效 Alice/Bob identity。Agent 测试还覆盖 Skill 正文的按需加载、OBO scope、用户历史
隔离，以及 Alice/Bob 并发运行时各自的 Token M 确实进入真实本地 Streamable HTTP MCP
请求。它们不会验证真实 tenant 的 admin consent、Conditional Access 或 SharePoint ACL。

## 已知限制与下一阶段

当前对话历史只保存在 Agent 进程内存中，进程重启后会丢失。内部 key 已绑定
`(tid, oid, thread_id)`，同一 thread 的并发请求会串行执行；持久化 memory 和 conversation
生命周期留给后续阶段。

本 MVP 使用 client secret，生产部署应改用 certificate 或托管的 secret store。当前没有
实现完整的 Conditional Access / CAE claims-challenge 往返；需要额外交互的 OBO 请求会
返回安全错误，而不会自动让 SPA 携带 claims 重新登录。跨主机部署 MCP 时还需要 HTTPS
和网络边界。

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
