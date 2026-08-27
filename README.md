# Vito's Work Assistant

Vito's Work Assistant 是一个学习型企业 AI Assistant Demo。它现在包含一个极简
Vanilla JavaScript Web UI、Microsoft Entra 单租户登录、受 Token A 保护的 FastAPI，
以及使用两段 OBO 保持当前用户身份的 Pydantic AI → Streamable HTTP MCP 链路。

本 README 描述 Work Assistant 的整体配置、运行方式与集成边界。M365 MCP 已迁移到
独立的 [`vitos-m365-mcp`](https://github.com/leonhanl/vitos-m365-mcp) 仓库，并在那里维护
自己的配置、运行和部署文档。

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
vitos-m365-mcp（独立服务）
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

Agent 进程只创建一个共享的 Pydantic AI `Agent`；对话消息由 AG-UI 客户端按 thread
持有并在每次 run 中提交，不在共享 Agent 对象里保存用户状态。Skills 作为独立、版本化 artifact 通过
`SKILLS_DIRECTORY` 注入；目录下每个子目录中的 `SKILL.md` 会在启动时被发现，模型最初
只看到 skill 的名称和描述，调用 `load_capability` 后才加载完整正文。Agent 启动时会校验
artifact，并记录 `SKILLS_VERSION`；修改或切换 artifact 后需要重启 Agent 进程。

这里的 **admin consent** 与登录是两件事：管理员代表 tenant 预先批准 delegated
permission，因此普通用户登录时不应看到 **Permissions requested** 页面；用户仍需完成
账号登录，以及 tenant 要求的 MFA 或 Conditional Access。

## 项目结构

```text
vitos-work-assistant/
├── apps/
│   ├── web/                  # Vite + Vanilla JS + MSAL Browser
│   └── agent/                # FastAPI + Token A validation + Pydantic AI
└── services/
    ├── jira-mcp-http/         # Jira MCP HTTP service 配置与启动文档
    └── salesforce-mcp-http/   # 占位；即将实现
```

API endpoints：

```http
GET  /health    anonymous
GET  /me        Bearer Token A required
POST /chat      Bearer Token A required
POST /feedback  Bearer Token A required
```

`POST /chat` 使用 AG-UI 协议。Web 通过 `@ag-ui/client` 发送包含 thread、run 和完整消息
上下文的 `RunAgentInput`，后端返回 `text/event-stream`。例如首轮请求的核心字段为：

```json
{
  "threadId": "page-local-thread-id",
  "runId": "unique-run-id",
  "state": {},
  "messages": [
    {"id": "message-id", "role": "user", "content": "How do I connect to VPN?"}
  ],
  "tools": [],
  "context": [],
  "forwardedProps": {}
}
```

普通回答以 AG-UI text message events 流式返回；实际的 Portkey trace ID 和读取的文档
分别以 `trace`、`sources` custom event 返回。需要确认的 Jira 创建操作由 Pydantic AI
转成 AG-UI interrupt，客户端用下一次 run 的 `resume` 字段批准或拒绝，不存在项目自定义
的 action endpoint。

## 1. 手工配置 Microsoft Entra

代码不会用 Azure CLI、PowerShell、Terraform 或 Graph API 创建 registration。当前阶段
需要在同一个 Vito tenant 中准备以下三个应用，但本仓库只负责配置前两个；
`vitos-m365-mcp` 由独立 MCP 仓库负责创建和维护，本仓库只检查它的集成配置是否存在：

| App Registration | 配置归属 | OAuth 角色 | 当前权限 |
|---|---|---|---|
| `vitos-work-assistant-client` | 本仓库 | Browser SPA public client | Work Assistant API `access_as_user` delegated permission |
| `vitos-work-assistant-api` | 本仓库 | Protected Web API / confidential client | 暴露自己的 `access_as_user`；调用 MCP 的 `access_as_user` delegated permission |
| `vitos-m365-mcp` | 独立 MCP 仓库 | Protected MCP resource / confidential client | 暴露自己的 `access_as_user`；Microsoft Graph `Files.Read.All` delegated permission |

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

7. 确认下文列出的外部 MCP 前置条件后，进入
   **API permissions → Add a permission → My APIs**，添加
   `vitos-m365-mcp/access_as_user` delegated permission，并由管理员授予 tenant-wide
   admin consent。若 MCP 或其 scope 未出现在 **My APIs**，不要在本仓库创建替代的 MCP
   registration；应先由 MCP owner 修复独立服务的 Entra 配置。
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

### 1.3 检查外部 `vitos-m365-mcp` 前置条件

MCP App Registration 的创建、Graph 权限、服务端凭据和运行配置都属于独立的
[`vitos-m365-mcp` 仓库](https://github.com/leonhanl/vitos-m365-mcp#readme)。不要在本仓库
重新创建或维护这些配置。接入 Work Assistant 前，由双方 owner 在 Microsoft Entra 和部署
环境中确认：

1. `vitos-m365-mcp` App Registration 已存在于 Work Assistant 所在的同一个 tenant，并已
   提供正确的 Directory tenant ID 和 Application client ID。
2. MCP 已启用 v2 access token，并暴露处于 Enabled 状态的完整 delegated scope：
   `api://<MCP_CLIENT_ID>/access_as_user`；如果使用自定义 Application ID URI，则提供准确的
   完整 scope。
3. MCP owner 已确认 Microsoft Graph `Files.Read.All` delegated permission 获得 tenant-wide
   admin consent，并且 MCP 服务端凭据存在且可用。Work Assistant 只确认这些配置存在，
   不读取、复制或保存 MCP client secret。
4. MCP owner 已在 MCP 的 **Authorized client applications** 中加入
   `vitos-work-assistant-api` 的 Application client ID，并允许使用 `access_as_user`。
5. `vitos-work-assistant-api` 的 **API permissions** 中已经添加 MCP `access_as_user`
   delegated permission，且状态显示为已获得 tenant-wide admin consent。
6. 独立 MCP 服务的 `/health` 可以从预期运行环境访问，最终 Streamable HTTP endpoint 与
   Agent 的 `M365_MCP_URL` 一致。

以上任何一项缺失时，应由对应 owner 在所属 App Registration、独立 MCP 仓库或部署平台中
补齐；不要把 Graph permission、MCP secret 或 MCP 服务端配置放入 Work Assistant。

## 2. 启动后端服务

### 2.1 独立启动 `vitos-m365-mcp`

先按独立
[`vitos-m365-mcp` 仓库](https://github.com/leonhanl/vitos-m365-mcp#本地开发)的说明完成
配置并启动 MCP。它拥有独立的 Python 3.13+、`uv` 环境、`.env`、测试和部署生命周期，
不安装到本仓库的虚拟环境中。

本地开发时，MCP 通常可通过 `http://127.0.0.1:8001/mcp` 访问；实际地址以独立服务的
`MCP_RESOURCE_URL` 和部署配置为准。跨主机或生产部署必须使用外部可访问的 HTTPS endpoint。

### 2.2 安装并启动 Work Assistant API

Agent 需要 Python 3.11+：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e "./apps/agent[dev]"
```

在另一个终端中：

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
PORTKEY_BASE_URL=https://api.portkey.ai/v1
PORTKEY_API_KEY=<Work Assistant service API key>
LLM_MODEL=<tool-calling model>
SKILLS_DIRECTORY=/absolute/path/to/work-assistant-skills
SKILLS_VERSION=<immutable artifact version>
ENTRA_TENANT_ID=<Directory tenant ID>
ENTRA_WORK_ASSISTANT_API_CLIENT_ID=<vitos-work-assistant-api Application client ID>
ENTRA_WORK_ASSISTANT_API_CLIENT_SECRET=<backend-only secret>
ENTRA_REQUIRED_SCOPE=access_as_user
# 必填；指向独立运行的 MCP 服务
M365_MCP_URL=http://127.0.0.1:8001/mcp
ENTRA_MCP_CLIENT_ID=<vitos-m365-mcp Application client ID>
# 仅当 MCP 使用自定义 Application ID URI 时设置：
# ENTRA_MCP_SCOPE=api://mcp.internal.example/access_as_user
```

Agent 是 Portkey-only：`PORTKEY_BASE_URL` 可以指向 Portkey 托管、本地或自托管的
OpenAI-compatible Gateway；外部和生产 endpoint 必须使用 HTTPS。同一个
`PORTKEY_API_KEY` 用于 LLM Gateway，并作为 `x-portkey-api-key` 发送给 M365 和 Jira
MCP。Portkey Config 绑定在该 service API key 上，应用不发送 Config ID，也不启用
cache。LLM 请求使用原始 Agent `run_id` 作为 Portkey trace ID；LLM 和 MCP 请求都会
附带可筛选的 `run_id`、`conversation_id`、用户 email/UPN 和 Entra OID metadata。
AG-UI 协议中的 `threadId` 会映射为同一个 Pydantic AI `conversation_id`。MCP Gateway
会为 MCP 日志生成自己的 trace ID，因此跨 LLM/MCP 调用应通过 `run_id` 关联；应用不会
发送 Entra token。

每个成功回答会显示一次性的五星评分。Agent API 把 Pydantic AI 实际使用的 `run_id` 作为
Portkey trace ID，并通过 `trace` custom event 返回给 Web；Web 把该 trace ID 和 `1` 到
`5` 的整数评分发给 `POST /feedback`。Agent API 使用后端 Portkey API key 将反馈写入对应 trace；
API key 不会发送到浏览器。MVP 不在本地保存反馈，分析和存储依赖 Portkey。

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

确认独立 MCP 已经运行，并完成本仓库的 Python、前端依赖、`apps/agent/.env` 和
`apps/web/.env` 配置后，可在仓库根目录运行：

```bash
./scripts/start-local.sh
```

脚本会按 `Agent` → `Web` 的顺序启动，并等待 Agent 就绪后再启动 Web。每次启动会清除
`logs/` 下的旧 `.log` 文件，新日志分别写入 `agent.log` 和 `web.log`。按 `Ctrl+C` 可停止
这两个服务；独立 MCP 的生命周期仍由它自己的仓库或部署平台管理。该脚本只拉起本仓库的
手工测试环境，不执行自动化测试。

### 使用 Docker Compose 启动 Web 与 Agent

Docker 运行方式不使用 `scripts/start-local.sh`。确认 `apps/agent/.env` 和
`apps/web/.env` 已配置后，先在仓库根目录构建两个 container image：

```bash
docker compose build
```

构建完成后，指定运行时使用的 Skills artifact 并启动容器：

```bash
export SKILLS_ARTIFACT_PATH=/absolute/path/to/work-assistant-skills
export SKILLS_ARTIFACT_VERSION=2026.08.16
docker compose up
```

Web 的宿主机端口默认为 `5173`。staging 环境可通过根目录 `.env` 配置为 `80`：

```dotenv
WEB_PORT=80
```

也可以在启动时显式指定环境文件：

```bash
docker compose --env-file .env.staging up -d
```

Compose 会启动两个容器：Nginx Web 默认暴露在 `http://localhost:5173`，Agent 只在
Compose 内部网络监听 `8000`。Nginx 将 `/api/*` 转发到 Agent，并在转发时删除 `/api`
前缀。Agent 的运行时配置来自 `apps/agent/.env`，包括其中配置的 `M365_MCP_URL`。

Skills 不会复制进 Agent 镜像。Compose 将 `SKILLS_ARTIFACT_PATH` 指向的版本化 artifact
只读挂载到 `/opt/work-assistant/skills`，并将 `SKILLS_ARTIFACT_VERSION` 传给 Agent 记录。
artifact 必须至少包含一个 `<skill>/SKILL.md`，否则 Agent 会在启动时失败。当前仓库中的
`apps/agent/skills` 只是迁移期间的本地开发默认值；正式部署应显式提供独立 artifact 的
绝对路径与不可变版本。

Web 的 `VITE_*` 配置只在 Vite 构建时使用。Compose 通过 BuildKit secret 临时挂载
`apps/web/.env`，该文件不会复制进构建上下文或最终 Nginx 镜像；这些 `VITE_*` 标识符仍会
按 Vite 的正常行为出现在浏览器端静态资源中。Web 构建禁用 layer cache，确保修改
`apps/web/.env` 后再次构建会生成新配置。

停止容器：

```bash
docker compose down
```

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
2. client registration 是否误加 Microsoft Graph 权限；并由 MCP owner 确认 Graph
   `Files.Read.All` 只配置在外部 MCP registration；
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
`acquireTokenRedirect`，然后由 AG-UI HTTP client 用 Token A 调用 `POST /api/chat`。
页面消费流式回答、sources custom event 和 approval interrupt，不解析 JWT、不自行刷新
或长期保存 access token。

再输入：

```text
How to connect to the corporate VPN? 
```

调用链是 Alice Token A → FastAPI identifies Alice → Agent OBO 获取 Alice Token M →
Pydantic AI 以 Token M 初始化 MCP → MCP 在工具调用时 OBO 获取 Alice Token G → Graph as
Alice。SharePoint ACL 应是 Alice 自己的 ACL。普通 Python 问题也会为 MCP 初始化获取
Token M，但不会触发 MCP → Graph 的第二段 OBO。

### 临时查看 Token M claims

仅在本地 troubleshooting 时，可以在 `apps/agent/.env` 中临时设置：

```text
ENTRA_LOG_MCP_TOKEN_CLAIMS=true
```

重启 Agent 后发起一次 `/chat`，Agent 日志会输出 Token M 的 decoded JWT header 和
claims，例如 `aud`、`azp`、`oid`、`tid`、`scp`、`iat`、`nbf` 与 `exp`。该功能不会输出
原始 token 或 signature，输出内容仅为未验签的诊断视图，不能代替 MCP Server 的完整
token validation。Claims 仍包含用户身份信息，因此不要在生产环境启用，也不要把日志发送
到不受信任的位置；排查完成后立即恢复为 `false` 并重启 Agent。

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
```

后端认证测试使用本地 RSA signing key，不访问真实 Entra、JWKS、Graph 或 LLM，覆盖
匿名 health、缺 token、无效 token、错误 audience / issuer / tenant、缺 scope，以及
有效 Alice/Bob identity。Agent 测试还覆盖 Skill 正文的按需加载、AG-UI dispatch、Jira
创建审批、可信参数覆盖，以及 Alice/Bob 并发运行时各自的 Token M 确实进入真实本地
Streamable HTTP MCP 请求。它们不会验证真实 tenant 的 admin consent、Conditional
Access 或 SharePoint ACL。
MCP Server 自身的认证、Graph、文档解析和端到端测试由独立的
[`vitos-m365-mcp`](https://github.com/leonhanl/vitos-m365-mcp#readme) 仓库运行。

## 已知限制与下一阶段

当前对话历史只保存在浏览器页面内存中，刷新页面后会丢失。服务端持久化 memory、跨设备
conversation 生命周期以及多实例恢复留给后续阶段。

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
