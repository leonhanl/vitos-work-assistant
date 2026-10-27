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
│   ├── agent/                # FastAPI + Token A validation + Pydantic AI
│   └── redteam-bridge/       # AIRS REST 接入、设备码授权与用户 Token 缓存
├── services/
│   ├── jira-mcp-http/         # Jira MCP HTTP service 配置与启动文档
│   └── salesforce-mcp-http/   # 占位；即将实现
└── samples/
    └── sharepoint/           # SharePoint 演示用示例文档
```

SharePoint 示例文档存放在 [`samples/sharepoint/`](samples/sharepoint/README.md)，用于保存和复用演示文件。

Prisma AIRS AI Red Teaming 可使用独立的
[`redteam-bridge`](apps/redteam-bridge/README.md)。它提供 API Key 保护的 REST 入口，
由测试用户提前完成设备码登录，再携带该用户的 Token A 调用下面现有的 `/chat`，
保留原来的 SSO、OBO、Agent、Skills 与 MCP 权限链路。默认 Compose 会一起构建并启动该服务。

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

如果需要把用户的安全组上报给 AI Gateway，还要在这个 registration 上追加一项可选配置，
见 [§1.4](#14-可选在-token-a-中包含-security-group)。

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

### 1.4 可选：在 Token A 中包含 security group

这一步让 Token A 携带用户所属的安全组，Agent 会把其中与本应用相关的组作为 metadata 上报
给 AI Gateway。不配置的话应用照常工作，只是不上报组信息。

配置在 `vitos-work-assistant-api`（签发 Token A 的那个 registration）上，不是 client：

1. 进入 `vitos-work-assistant-api` → **Token configuration → Add groups claim**。
2. 勾选 **Security groups**。
3. 展开 **Access** token 的格式选项，选择 **Group ID**（默认）。这是 Agent 期望的格式：
   claim 里是 group object ID 的数组。不要选 `sAMAccountName` 或
   `NetBIOSDomain\sAMAccountName`——那些只对从本地 AD 同步的组有效，cloud-only 的组会
   回退成 object ID，导致格式不一致。
4. 保存。可在 **Manifest** 中确认结果：

   ```json
   "groupMembershipClaims": "SecurityGroup"
   ```

   直接编辑 manifest 设置这个值等价于以上 portal 操作。

接着取得需要上报的组的 object ID：**Identity → Groups → All groups**，点开目标组，在
**Overview** 复制 **Object Id**。然后填进 Agent 的 `ENTRA_GROUP_LABELS`
（见 [apps/agent/.env.example](apps/agent/.env.example)）：

```text
ENTRA_GROUP_LABELS='{"<GROUP_OBJECT_ID>":"it_admin","<GROUP_OBJECT_ID>":"finance"}'
```

外层单引号是必需的：`scripts/start-local.sh` 用 shell `source` 加载 `.env`，不加单引号
JSON 里的双引号会被 shell 剥掉，Agent 启动时会报
`error parsing value for field "entra_group_labels"`。

键是 group object ID，值是上报用的短标签，必须是 snake_case，因为每个标签会成为一个
`group_<label>="true"` 的 metadata 键。这张表同时充当 allowlist：**只有列出的组会离开
应用**，用户的其他组即使出现在 claim 里也会被丢弃。这是刻意的——把用户全部组倒进第三方
可观测系统属于目录数据过度外泄。

配置完成后，Alice 需要重新登录才能拿到带 `groups` claim 的新 Token A。验证方式见
[§4 Alice 登录与 `/me`](#alice-登录与-me)：`/me` 响应中的 `groups` 字段会列出映射后的标签。

两个边界值得知道：

- **Group overage。** 用户所属组超过约 200 个时，Entra 不会在 access token 中放 `groups`，
  而是放 `_claim_names` / `_claim_sources` 指向 Graph endpoint。此时应用会当作"没有组
  信息"处理（不报错、不阻断），需要改为调用 Graph 查询才能拿到——MVP 未实现。如果 tenant
  中确实存在这种用户，可在 **Add groups claim** 中改选 **Groups assigned to the
  application**，把 claim 限制在分配给该 enterprise application 的组，从根本上避免 overage。
- **上报组不等于按组授权。** 这些 metadata 只用于 Gateway 侧的可观测性和策略；真正的数据
  授权仍然由 Alice/Bob 自己的 Graph ACL（Token A → M → G 委托链）决定，不受这张映射表影响。

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
PORTKEY_CONFIG_GUARDRAIL_ON=<saved Config ID with guardrails>
PORTKEY_CONFIG_GUARDRAIL_OFF=<saved Config ID without those guardrails>
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

配置了 [§1.4](#14-可选在-token-a-中包含-security-group) 时，用户所属的安全组也会作为
metadata 上报，供 Gateway 侧按组筛选和路由；每个组是一个 `group_<label>="true"` 键，因为
Portkey metadata 的值只能是字符串，claim 里的数组必须拍平。

这一步刻意放在应用层而不是 Gateway。只有应用知道 Entra tenant 和哪些组对本应用有意义；
放在 Gateway 就要让这个与应用无关的基础设施持有一个应用专属的 Graph 凭据，而那个凭据比
应用自己的 OBO 链更宽。技术上也走不通：Gateway 的 guardrail hook 只能改写请求体，写不了
metadata，而且它运行在路由决策之后。组信息直接取自已验证 Token A 的 `groups` claim，
因此没有额外的网络调用、凭据或权限，Token A→M→G 的身份边界不变。

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
`apps/web/.env` 配置，以及 [Bridge 本地配置与依赖](apps/redteam-bridge/README.md#6-本地开发与测试)
后，可在仓库根目录运行：

```bash
./scripts/start-local.sh
```

脚本会按 `Agent` → `Web` → `Red Team Bridge` 的顺序启动，并逐个等待服务就绪。
Bridge 使用自己的 `.venv` 和 `.env`，监听 `127.0.0.1:8081`；首次聊天前需按 Bridge
文档完成设备码授权。每次启动会清除 `logs/` 下的旧 `.log` 文件，新日志分别写入
`agent.log`、`web.log` 和 `redteam-bridge.log`。按 `Ctrl+C` 可停止这三个服务；独立
MCP 的生命周期仍由它自己的仓库或部署平台管理。该脚本只拉起本仓库的手工测试环境，
不自动执行设备码登录或自动化测试。

### 使用 Docker Compose 启动 Web、Agent 与 Red Team Bridge

Docker 运行方式不使用 `scripts/start-local.sh`。确认 `apps/agent/.env`、
`apps/web/.env` 和 [Bridge 配置](apps/redteam-bridge/README.md#2-配置与构建)
`apps/redteam-bridge/.env` 已配置后，先在仓库根目录构建三个 container image：

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

Compose 会启动三个容器：Nginx Web 默认暴露在 `http://localhost:5173`，Agent 只在
Compose 内部网络监听 `8000`。Nginx 将 `/api/*` 转发到 Agent，并在转发时删除 `/api`
前缀。Agent 的运行时配置来自 `apps/agent/.env`，包括其中配置的 `M365_MCP_URL`。
Red Team Bridge 默认暴露在 `http://localhost:8080`，可通过 `REDTEAM_PORT` 调整宿主机
端口；首次使用前需完成 [Bridge 设备码授权](apps/redteam-bridge/README.md#3-首次授权)。

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

### Alice 登录

1. 打开 `http://localhost:5173`，点击 **Sign in with Microsoft**。
2. 使用 Alice 登录。Alice 可以完成账号选择、密码、MFA 或 Conditional Access，但不应
   看到 **Permissions requested** 用户 consent 页面。
3. 页面右上方应显示 `account.name` / `account.username`。登录后前端会调用
   `GET /api/me`，将返回的 `vitos_sales` / `vitos_it_support` 组标签分别显示为姓名旁的
   `Sales` / `IT Support`；同时属于两个组时显示两者，没有匹配组时只显示姓名与邮箱。
4. 如需单独排查 API 身份，可携带 Token A 调用 `GET /api/me`；后端返回 Alice 的 `oid`、tenant `tid`、username 与
   `groups`。`groups` 是映射后的组标签，仅在配置了
   [§1.4](#14-可选在-token-a-中包含-security-group) 时非空。

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

## 5. 预设问题与 guardrail 模式

登录后的左侧栏按 scenario 展示预设问题。点击问题会在右侧显示完整内容并立即发送，
与自由输入共用当前 conversation。Scenario 仅作展示分组；点击其他分组的问题不会
切换或清除会话。需要开始新会话时，由演示者手动刷新页面。聊天历史不持久化。

编辑 [`apps/web/public/config/scenarios.json`](apps/web/public/config/scenarios.json)
即可调整分组、顺序、标题和实际发送的内容：

```json
{
  "scenarios": [
    {
      "title": "VPN Support",
      "prompts": [
        {
          "title": "Find the VPN guide",
          "message": "Find the official instructions for connecting to the company VPN."
        }
      ]
    }
  ]
}
```

页面在加载时请求 `/config/scenarios.json`，不使用浏览器缓存；配置错误会显示在侧栏，
自由输入仍可使用。Docker Compose 只读挂载整个配置目录，修改 JSON 后刷新页面即可，
无需重新构建镜像。JSON 中的标题和问题均为非空字符串，问题最长 20,000 字符。

Guardrails 首次打开默认为 **OFF**，当前标签页的选择保存在 `sessionStorage`。
切换开关先显示 **Cancel / Continue** 确认框：取消保留模式和对话，继续则保存新模式、
刷新页面并创建新 conversation；不会重发旧问题。刷新只清除聊天上下文，不撤销已完成
的工单等业务操作，也不清除 Microsoft 登录缓存。生成回答或等待工单审批时，问题按钮
和开关暂时禁用。

在 `apps/agent/.env` 中分别配置 `PORTKEY_CONFIG_GUARDRAIL_ON` 和
`PORTKEY_CONFIG_GUARDRAIL_OFF`。两项均必填且不可为空；示例值是占位符，请替换为
实际保存的 Config ID，并在 Portkey API key 设置中允许 **Config Override**。
两种模式都会显式发送对应的 `x-portkey-config`，不依赖 API key 的 Default Config。
修改 ENV 后重启本地 Agent；Docker 部署使用 `docker compose up -d --force-recreate agent`
重新加载环境变量。Config 只作用于 LLM 请求，MCP、OBO 和 Jira 审批保持原有行为；
Portkey 中独立强制执行的组织或 workspace guardrails 需在网关侧另行确认。

Web 的普通消息、预设问题和审批 resume 均通过现有 `/chat` 请求携带：

```json
{"forwardedProps": {"guardrails_enabled": false}}
```

Agent 在未提供模式时默认 OFF，显式提供时只接受 JSON 布尔值。模式只决定本次 run
使用的 Config，不修改共享 Agent。Red Team Bridge 支持同名 REST 参数并转发到这里，
不读取 Web 开关，也不保存模式；多轮测试请在每次请求中传入所需模式。切换测试模式时，
省略旧的 `conversation_id` 开始新会话，避免混入旧上下文。

### Streaming 输出检查与回答撤回

Agent 的 Chat Completions 请求统一发送
`x-portkey-strict-open-ai-compliance: false`，以接收 Portkey 的 `hook_results`。
要在响应中获得检查结果，Portkey Guardrail 应配置为同步执行（`async=false`）；
异步检查结果只出现在 Portkey 日志中。参见
[Portkey Streaming Responses 文档](https://portkey.ai/docs/product/guardrails#streaming-responses)。

Portkey 在完整输出组装后进行输出检查，结果可能位于 `data: [DONE]` 之后。
OpenAI SDK 会在看到 `[DONE]` 时停止读取，因此 Agent 在 HTTPX 与 SDK 之间增加了一个
小型 SSE 适配层：

1. 普通内容 chunk 立即转发，Web 继续逐步显示回答，不缓存整篇回答。
2. `[DONE]` 暂存，继续读取本次 HTTP 响应的剩余数据。
3. 收到 `hook_results.after_request_hooks` 时，任一 `verdict` 为 JSON `false` 就终止
   本次 run，发送现有 AG-UI `RUN_ERROR / gateway_guardrail_blocked`，不再发送
   `RUN_FINISHED`。应用按检查失败撤回回答，不依赖 hook 的 `deny` 设置或 HTTP 446。
4. HTTP 响应流正常结束（EOF）且没有失败结果时，向 SDK 补发 `[DONE]`，正常完成 run。
   **检查结果是可选的：没有配置 Guardrail、只有输入检查或未返回 Verdict 都可以正常完成。**
   应用不把“没有 Verdict”解释为“检查通过”。

完成条件是 **HTTP 响应流正常结束，不是必须收到 Verdict**。Agent 不需要提前知道
Config 中是否配置了输出 Guardrail。HTTP 响应结束与底层 TCP 连接断开不同；连接可以
继续复用。这个逻辑要求网关在发完内容和可选检查结果后正常结束本次 HTTP 响应。
如果网关迟迟不结束响应，使用现有 LLM HTTP 超时；连接中断、超时或缺少 `[DONE]`
都按运行错误处理，不伪装成检查通过。

下面是 **Portkey → Agent 适配层** 的简化 SSE 示例，仅保留相关字段；空行分隔事件。
“HTTP 响应结束”是协议结束信号，不是一个额外的 SSE 消息。实际网关格式和结束时序
需要在部署环境中联调确认。

**Sample 1：没有配置 Guardrail。**

```text
data: {"choices":[{"delta":{"content":"This is the answer."}}]}

data: [DONE]

```

随后 HTTP 响应结束。适配层不等待不存在的 Verdict，在 EOF 后补发 `[DONE]` 给 SDK；
前端收到 `RUN_FINISHED`，保留回答。

**Sample 2：配置了输出 Guardrail，检查通过。**

```text
data: {"choices":[{"delta":{"content":"This is the answer."}}]}

data: [DONE]

data: {"hook_results":{"after_request_hooks":[{"verdict":true}]}}

```

随后 HTTP 响应结束。适配层消费检查结果，在 EOF 后补发 `[DONE]` 给 SDK；
前端收到 `RUN_FINISHED`，保留回答。检查结果即使出现在 `[DONE]` 之前也可以处理。

**Sample 3：配置了输出 Guardrail，检查失败。**

```text
data: {"choices":[{"delta":{"content":"Content rejected by the output check."}}]}

data: [DONE]

data: {"hook_results":{"after_request_hooks":[{"verdict":false,"deny":false}]}}

```

前端此时可能已经显示回答。适配层发现失败后立即抛出输出拦截异常，不向 SDK 补发
`[DONE]`。Agent 通过现有错误链路发出下面的事件；不会再发出 `RUN_FINISHED`：

```text
data: {"type":"RUN_ERROR","code":"gateway_guardrail_blocked","message":"This operation was stopped by the corporate security policy. Please contact IT support if you need assistance."}

```

Web 删除本次 run 的整个回答气泡及其参考来源，并通过 AG-UI subscriber 返回运行前的
消息历史，清除本轮新增的 assistant/tool 消息，避免被拦截内容进入下一轮上下文。
之前的对话和本次用户问题保留；已经显示过回答时，界面提示：
“本次回答未通过企业内容安全检查，已撤回。如需协助，请联系 IT 支持。”
尚未显示回答时提示：“本次处理因企业安全策略而中止。如需协助，请联系 IT 支持。”
提示不推断触发原因，也不要求用户修改问题；知识库内容等也可能触发安全策略。

撤回只移除界面内容和本轮生成的对话历史，无法收回用户已经看见的信息，也不撤销
已经完成的工具操作；Jira 创建仍遵守原有审批流程。Red Team Bridge 复用同一
`gateway_guardrail_blocked` 错误，将其映射为 HTTP 446，失败 run 不写入持久化历史。

## 6. 测试

```bash
cd apps/web
npm test
npm run build

cd ../..
python -m pytest apps/agent/tests
```

`npm test` 使用 Node 内置测试工具和 DOM/auth 测试替身，覆盖问题自动发送、跨分组沿用
同一 conversation、开关确认/取消、刷新后模式保留、审批 resume 和配置加载错误；不访问
真实认证或后端。另用真实 AG-UI 客户端与模拟 SSE 验证输出拦截后的气泡撤回、工具消息
清理，以及下一轮请求不再携带被拦截内容。前端视觉布局及真实 Portkey 效果仍需手动确认。

后端认证测试使用本地 RSA signing key，不访问真实 Entra、JWKS、Graph 或 LLM，覆盖
匿名 health、缺 token、无效 token、错误 audience / issuer / tenant、缺 scope，以及
有效 Alice/Bob identity。Agent 测试还覆盖 Skill 正文的按需加载、AG-UI dispatch、Jira
创建审批、可信参数覆盖，以及 Alice/Bob 并发运行时各自的 Token M 确实进入真实本地
Streamable HTTP MCP 请求。它们不会验证真实 tenant 的 admin consent、Conditional
Access 或 SharePoint ACL。
Streaming 测试使用真实 HTTPX/OpenAI/Pydantic AI/AG-UI 与模拟网关响应，验证内容在
EOF 前持续转发、无 Verdict 正常完成、`[DONE]` 后的输出检查通过/失败，以及尾部网络
错误不被当作成功；不连接真实 Portkey 或 Prisma AIRS。
MCP Server 自身的认证、Graph、文档解析和端到端测试由独立的
[`vitos-m365-mcp`](https://github.com/leonhanl/vitos-m365-mcp#readme) 仓库运行。

## 7. GitHub Actions 自动构建

工作流位于 [`.github/workflows/build.yml`](.github/workflows/build.yml)，仅支持手动触发。
运行时先执行 Agent、Red Team Bridge 和 Web 三组测试，再构建 Linux amd64 的三个镜像
并上传下载包。推送代码、tag 或创建 PR 不会触发该工作流。
依赖安装使用两个 Python 项目各自的 `uv.lock` 和前端的 `package-lock.json`。

### GitHub 配置

在仓库 **Settings → Secrets and variables → Actions → Secrets → New repository secret**
创建名称为 `WEB_ENV` 的 Secret，内容直接填写本地 `apps/web/.env` 中的四个实际参数：

```dotenv
VITE_ENTRA_TENANT_ID=<Directory tenant ID>
VITE_ENTRA_CLIENT_ID=<SPA Application client ID>
VITE_WORK_ASSISTANT_API_CLIENT_ID=<Work Assistant API Application client ID>
VITE_WORK_ASSISTANT_API_SCOPE=api://<Work Assistant API Application client ID>/access_as_user
```

请用真实 UUID 替换占位符，scope 中的 API ID 必须与第三行一致。Secret 只包含以上
`VITE_*` 参数和可选注释；不要放入后端 client secret 或 Portkey API key。
这些前端参数会按 Vite 的行为写入浏览器静态资源，Secret 只是它们在 GitHub 上的管理方式。

如果仓库禁用了 Actions，在 **Settings → Actions → General** 启用，并允许工作流使用
`actions/*` 和 `astral-sh/setup-uv`。本流程只需要 `contents: read` 权限，无需 PAT、
GHCR 或 Docker Hub 账号，也不需要在 GitHub 配置后端运行时密钥。

Actions 从 `WEB_ENV` 生成临时文件，通过已有的 `web_env` BuildKit secret 传给 Web
Dockerfile，不读取根目录 Compose。本地仍运行 `docker compose build`，使用本地
`apps/web/.env`；原有 Compose 和 Dockerfile 无需修改。更新 GitHub Secret 后，需要
重新运行构建才会生成新的前端配置。

### 触发与下载

将工作流提交到 GitHub 默认分支后，在 **Actions → Build artifacts → Run workflow**
手动运行。成功后点开具体 run，在页面底部的 **Artifacts**
下载以下三个文件，`<version>` 为该次构建的完整 commit SHA：

| 文件 | 内容 |
|---|---|
| `work-assistant-images-<version>-linux-amd64.tar.gz` | 三个镜像，使用 `docker load` 导入 |
| `work-assistant-skills-<version>.zip` | Git 已跟踪的 Skills 文件，保持独立于 Agent 镜像 |
| `work-assistant-deploy-<version>.zip` | 引用镜像的 Compose、运行配置模板、场景 JSON、版本清单和部署说明 |

文件默认保留 14 天。下载后按部署包中的 `README.md` 配置运行环境和 Bridge 首次授权。
发布用 Compose 的源文件在 [`deploy/compose.yaml`](deploy/compose.yaml)，部署步骤也可在
[`deploy/README.md`](deploy/README.md) 查看。镜像只支持 Linux amd64；ARM 主机需另行构建。
流程不执行真实 Entra、M365、Jira、Portkey 或 AIRS 联调，也不自动更新服务器。

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
