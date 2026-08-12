# Vito's Work Assistant Agent API

这是 Vito's Work Assistant 的后端说明。FastAPI 是 Microsoft Entra 单租户、多用户认证
的 Web API；`apps/web` 中的 Vanilla JavaScript SPA 让 Alice 和 Bob 使用各自的
**Token A** 调用受保护 endpoint。DeepAgent 仍通过 Streamable HTTP 连接独立运行的
`services/m365-mcp-http`。

## 当前认证架构与安全边界

```text
Alice / Bob
    │ interactive login
    ▼
Microsoft Entra ID
    │ Token A: aud = Work Assistant API
    ▼
Work Assistant FastAPI
    │ validate signature / iss / tid / aud / exp / nbf / ver / scp
    │ CurrentUser = (tid, oid), username 仅用于显示和日志
    ▼
DeepAgent
    │ Streamable HTTP（不转发 Token A）
    ▼
m365-mcp-http
    │ Device Code Flow + MSAL token cache
    ▼
Microsoft Graph（仍是同一个预先登录的 M365 用户）
```

因此，Work Assistant API 已能认证并区分 Alice 和 Bob，但 Graph 访问还不是
per-user delegated access。Alice 和 Bob 触发的 `search_sharepoint` / `read_document`
仍使用 m365-mcp-http 当前 Device Code 缓存的同一个身份。Token A 只允许调用 Work
Assistant API，绝不能当作 Graph token 或 MCP credential。

本阶段没有 OBO、Graph Token B、MCP OAuth、token propagation、RBAC 或 Graph `/me`。
`GET /me` 只返回已经验证的 Token A claims。

> **Conversation authorization TODO**：`thread_id` 的内存状态尚未绑定 `(tid, oid)`。
> 知道其他 `thread_id` 的已认证用户可能访问其对话历史。当前仅适合受控 Demo，后续需
> 在持久化与授权设计中绑定 conversation owner；本阶段不实现该重构。

下一阶段才会考虑：

```text
Token A → Work Assistant → OBO → Graph Token B → m365-mcp-http
        → Microsoft Graph as current user
```

## 代码边界

- `auth.py`：Bearer 提取、tenant-specific OIDC metadata/JWKS、Token A 验证、scope
  验证及 `CurrentUser`。
- `config.py`：验证 LLM、Entra API 与 MCP endpoint 配置。
- `app.py`：匿名 `GET /health`，受保护的 `GET /me` 和 `POST /chat`。
- `llm.py`：构建可配置 `base_url` 的 LangChain `ChatOpenAI`。
- `mcp.py`：在 FastAPI 生命周期内保持 Streamable HTTP MCP session，只加载
  `search_sharepoint` 与 `read_document`。
- `agent.py`：用 `create_deep_agent`、`StateBackend` 与 `InMemorySaver` 构建 Agent。
- `examples/entra_test_client.py`：开发用 MSAL Python public client，不属于服务端认证。

DeepAgent 不接收 CurrentUser 或原始 JWT；Token A 不进入 prompt、messages、Agent
state 或 MCP request。`sources` 仍只从真实 MCP tool message 的结构化字段提取。

## 安装

需要 Python 3.11 或更新版本。以下命令从仓库根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e "./services/m365-mcp-http[dev]"
python -m pip install -e "./apps/agent[dev]"
```

## 1. 手工配置 Microsoft Entra

代码不会创建或修改 App Registration。请在 Microsoft Entra admin center 的同一个
Vito tenant 中手工创建两个 **Accounts in this organizational directory only** 的
单租户应用。

两个注册代表不同的 OAuth 角色：

```text
vitos-work-assistant-client                 vitos-work-assistant-api
Browser SPA / optional Python test client → FastAPI backend + DeepAgent
发起用户登录并取得 Token A                  暴露 scope 并验证 Token A
SPA 需要 redirect URI                       当前不需要 redirect URI
```

`vitos-work-assistant-api` 不是单独代表 LLM 或 DeepAgent，而是代表包含 Authentication、
FastAPI endpoints 与 DeepAgent 的整个后端安全边界。未来实现 OBO 时，通常仍由这个
API registration 作为 confidential client 用 Token A 换取 Graph Token B；但是本阶段
不创建 credential、不授予 Graph permission，也不实现 OBO。

### 1.1 `vitos-work-assistant-api`（protected Web API）

#### A. 创建 registration

进入 **Microsoft Entra admin center → Identity → Applications → App registrations →
New registration**，输入：

| Portal 字段 | 输入值 |
|---|---|
| Name | `vitos-work-assistant-api` |
| Supported account types | `Accounts in this organizational directory only` |
| Redirect URI | 留空 |

点击 **Register**。在 Overview 页面记录以下值，注意不要混淆 Application ID 与
Object ID：

| README 占位符 | Portal 中的值 | 用途 |
|---|---|---|
| `<TENANT_ID>` | Directory (tenant) ID | 限制只接受 Vito tenant |
| `<WORK_ASSISTANT_API_CLIENT_ID>` | Application (client) ID | Token A audience / API identifier |

进入 **Owners → Add owners**，把当前配置人员加入 Owner。之后 client registration
也添加同一 Owner，可以避免 API 在 **My APIs** 中不可见。

#### B. 设置 Application ID URI

进入 **Expose an API**，点击 Application ID URI 旁的 **Add**，保持推荐值：

```text
api://<WORK_ASSISTANT_API_CLIENT_ID>
```

这里必须使用 API 的 **Application (client) ID**，不是 Object ID；末尾不要添加 `/`。
例如 API client ID 为 `11111111-2222-3333-4444-555555555555` 时，输入：

```text
api://11111111-2222-3333-4444-555555555555
```

点击 **Save**。

#### C. 完整填写 `access_as_user` scope

仍在 **Expose an API**，点击 **Add a scope**，逐项填写：

| Portal 字段 | 输入值 |
|---|---|
| Scope name | `access_as_user` |
| Who can consent? | `Admins and users` |
| Admin consent display name | `Access Vito's Work Assistant as the signed-in user` |
| Admin consent description | `Allows the application to access Vito's Work Assistant API on behalf of the signed-in user.` |
| User consent display name | `Access Vito's Work Assistant` |
| User consent description | `Allow this application to access Vito's Work Assistant on your behalf.` |
| State | `Enabled` |

`Admins and users` 适合这个低权限、学习型 Demo。如果组织策略要求所有应用都经过
管理员审批，也可以选择 `Admins only`；这只改变谁能 consent，不改变 FastAPI 对
`scp=access_as_user` 的检查。点击 **Add scope** 后，页面应显示完整 scope：

```text
api://<WORK_ASSISTANT_API_CLIENT_ID>/access_as_user
```

Portal 会为该 scope 自动生成一个 Permission ID。保留这个自动生成的 GUID；代码和
`.env` 都不需要填写 Permission ID。

#### D. 要求 v2.0 access token

进入 **Manifest**。在现有 `"api"` 对象中找到 `requestedAccessTokenVersion`，将其
值改为数字 `2`：

```json
"requestedAccessTokenVersion": 2
```

不要删除或替换 `"api"` 对象中的 `oauth2PermissionScopes`；它包含刚才由 portal 创建
的 scope。点击 **Save**。这个设置由 resource/API 决定 Token A 的格式；如果保持
`null`，可能得到 v1 token，而本项目会拒绝 `ver != 2.0` 的 token。

#### E. API registration 完成检查

```text
Supported account types     = Current organization only
signInAudience              = AzureADMyOrg
Application ID URI          = api://<WORK_ASSISTANT_API_CLIENT_ID>
Exposed scope               = access_as_user
Scope state                 = Enabled
requestedAccessTokenVersion = 2
Redirect URI                = none
Client secret/certificate   = none（本阶段）
Microsoft Graph permission  = none（本阶段）
```

如果新 registration 自动带有 Microsoft Graph `User.Read`，本阶段可以从 API
permissions 中移除；Work Assistant 对 Graph 的访问仍由独立的 m365-mcp-http App
Registration 和 Device Code identity 完成。

### 1.2 `vitos-work-assistant-client`（Browser SPA public client）

这个 registration 主要代表 `apps/web`。它使用 MSAL Browser v5 的
Authorization Code + PKCE flow，让 Alice/Bob 登录、取得 Token A，然后调用 `/me`
和 `/chat`。它不运行 Agent，也不直接调用 Microsoft Graph。
`examples/entra_test_client.py` 仍可作为可选的后端开发诊断工具；只有要运行该脚本时
才需要额外的 desktop platform 配置。

#### A. 创建 registration

进入 **App registrations → New registration**，输入：

| Portal 字段 | 输入值 |
|---|---|
| Name | `vitos-work-assistant-client` |
| Supported account types | `Accounts in this organizational directory only` |
| Redirect URI | 注册页面先留空，下一步在 Authentication 中配置 |

点击 **Register**，记录：

| README 占位符 | Portal 中的值 | 用途 |
|---|---|---|
| `<WEB_CLIENT_ID>` | Application (client) ID | MSAL Browser public client ID |
| `<TENANT_ID>` | Directory (tenant) ID | 必须与 API registration 相同 |

进入 **Owners → Add owners**，添加与 API registration 相同的 Owner。不要进入
Certificates & secrets 创建 client secret；Browser SPA 是 public client，不能安全
保存 secret。

#### B. 配置 SPA redirect

进入 **Authentication → Add a platform → Single-page application**：

1. 添加 redirect URI `http://localhost:5173/redirect.html`。
2. 点击 **Configure** / **Save**。
3. 不要启用 implicit grant 的 access token / ID token 选项。

这个 URI 必须精确匹配 `apps/web` 中的 MSAL v5 redirect bridge。开发主页仍是
`http://localhost:5173`；bridge 只处理 authentication response。生产环境应增加实际
HTTPS bridge URI，并让 hosting 对 bridge 返回 `Cache-Control: no-store`。

可选：若仍需运行 `examples/entra_test_client.py`，再添加 **Mobile and desktop
applications** platform 的 `http://localhost`，并把 **Allow public client flows** 设为
`Yes`。Web UI 本身不需要这个 desktop redirect 或开关。

#### C. 给 client 添加 API delegated permission

进入 **API permissions → Add a permission → My APIs**：

1. 选择 `vitos-work-assistant-api`。
2. 选择 **Delegated permissions**，不要选择 Application permissions。
3. 勾选 `access_as_user`。
4. 点击 **Add permissions**。

完成后 API permissions 页面应显示：

```text
API / Permission name           Type       Admin consent required
vitos-work-assistant-api
  access_as_user                Delegated  取决于 scope/tenant consent policy
```

若 `vitos-work-assistant-api` 没出现在 **My APIs**，先确认两个 registration 位于同一
tenant，且当前账号是两个 registration 的 Owner。若 tenant 禁止 user consent，则由
管理员点击 **Grant admin consent for <tenant>**。

新 registration 如果自动带有 Microsoft Graph `User.Read`，请将它移除。当前 Client
唯一需要的业务 permission 是 Work Assistant API 的 `access_as_user`，不需要 Graph
`User.Read`、`Sites.Read.All`、`Files.Read.All` 或 `.default`。

#### D. Client registration 完成检查

```text
Supported account types       = Current organization only
Application type              = Single-page application / public client
Redirect URI                  = http://localhost:5173/redirect.html
Implicit grant                = disabled
Client secret/certificate     = none
Delegated API permission      = vitos-work-assistant-api / access_as_user
Microsoft Graph permissions   = none
```

Web UI 请求的唯一业务 scope 是：

```text
api://<WORK_ASSISTANT_API_CLIENT_ID>/access_as_user
```

后端只需要 tenant ID 与 API client ID。Web client ID 应写入 `apps/web/.env`，不要和
API client ID 混用。可选 Python test client 才读取 `ENTRA_TEST_CLIENT_ID`：

```dotenv
ENTRA_TENANT_ID=<两个 registrations 共同的 Directory tenant ID>
ENTRA_WORK_ASSISTANT_API_CLIENT_ID=<vitos-work-assistant-api Application client ID>
ENTRA_REQUIRED_SCOPE=access_as_user
ENTRA_TEST_CLIENT_ID=<vitos-work-assistant-client Application client ID>
WORK_ASSISTANT_API_URL=http://127.0.0.1:8000
```

Web 配置与完整 SPA 手工步骤见仓库根
[`README.md`](../../README.md)。不要把 `api://...` 填进后端的
`ENTRA_WORK_ASSISTANT_API_CLIENT_ID`；该变量只接受 GUID。

### Token version 与验证方式

Web UI 与可选 Test Client 都使用 tenant-specific v2 authority；API 使用以下
tenant-specific metadata：

```text
https://login.microsoftonline.com/<TENANT_ID>/v2.0/.well-known/openid-configuration
```

API 从 metadata 获取 issuer 与 JWKS，并使用 PyJWT 验证 RS256 signature、`iss`、
`aud`、`exp`、`nbf`，再强制检查 `ver=2.0`、`tid`、`oid` 和 `scp`。v2 自定义 API
token 的 `aud` 应为 `vitos-work-assistant-api` 的 Application (client) ID；Graph
token 的 `aud` 不同，因此会被拒绝。

开发时可以在浏览器的 `jwt.ms` 检查 token payload 中的 `ver`、`aud`、`tid`、`oid`
和 `scp`，但 access token 是敏感 credential：不要粘贴到不受信任的网站、日志、
截图或 issue。仅查看 payload 不能替代 API 的签名验证。

## 2. 登录并启动 m365-mcp-http

此部分保持原有 Device Code 架构。配置服务自己的环境并登录：

```bash
cd services/m365-mcp-http
cp .env.example .env
# 编辑 .env，填写 M365_TENANT_ID 和 M365_CLIENT_ID
set -a
source .env
set +a
python -m m365_mcp.auth login
```

在终端 A 保持服务运行：

```bash
python -m m365_mcp.server
```

默认 endpoint 是 `http://127.0.0.1:8001/mcp`。它仍没有 MCP HTTP authentication，
只适用于本机受信任的 single-user 开发环境；Agent 不向其发送 Token A。
Token cache 默认位于 `~/.cache/m365-mcp/msal_token_cache.json`。Work Assistant API
不会启动 Device Code Flow；cache 无有效 session 时，`/chat` 返回脱敏后的服务错误并
要求按 M365 README 重新登录。

## 3. 配置并启动 Work Assistant

从仓库根目录执行：

```bash
cp apps/agent/.env.example apps/agent/.env
# 编辑 apps/agent/.env；不要提交真实 key、token 或密码
set -a
source apps/agent/.env
set +a
uvicorn work_assistant.app:app --reload
```

关键配置如下：

```dotenv
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=...
LLM_MODEL=...

ENTRA_TENANT_ID=<Directory tenant ID>
ENTRA_WORK_ASSISTANT_API_CLIENT_ID=<vitos-work-assistant-api client ID>
ENTRA_REQUIRED_SCOPE=access_as_user

M365_MCP_URL=http://127.0.0.1:8001/mcp

ENTRA_TEST_CLIENT_ID=<vitos-work-assistant-client client ID>
WORK_ASSISTANT_API_URL=http://127.0.0.1:8000
```

`M365_TENANT_ID`、`M365_CLIENT_ID` 与 M365 token cache 配置只属于独立 M365
Server。两个 App Registration 的 client ID 职责不同，不要混用。

LLM endpoint 必须兼容 OpenAI Chat Completions，模型必须支持 Agent 所需的 tool /
function calling。`LLM_MODEL` 为 GPT-5.6 系列时，现有代码继续使用 Chat Completions
并设置其工具调用所需的 `reasoning_effort="none"`。

## 4. 手工验证

### Health 与匿名拒绝

`/health` 不需要 Token A：

```bash
curl http://127.0.0.1:8000/health
```

预期为 `{"status":"ok"}`。匿名 `/chat` 必须返回 `401`：

```bash
curl -i -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hello"}'
```

### Alice

保持 Agent 环境变量已加载，运行 public test client：

```bash
python apps/agent/examples/entra_test_client.py \
  --login-hint alice@vitosdemo.com \
  --message "Python 中 list 和 tuple 有什么区别？"
```

MSAL 会打开 Microsoft 登录页面并使用 Authorization Code + PKCE 获取 access token。
脚本先调用 Work Assistant `GET /me`，再调用 `POST /chat`；默认只显示过期时间和
response，不打印 Token A。先只验证身份而不执行 Agent/LLM，可加 `--me-only`。

记录 Alice 的 `oid`、`tid` 与 `username`。

### Bob

再次运行同一命令，并在浏览器 account chooser 中选择 Bob：

```bash
python apps/agent/examples/entra_test_client.py \
  --login-hint bob@vitosdemo.com \
  --me-only
```

预期 Bob 的 `oid` 和 `username` 与 Alice 不同，`tid` 相同。去掉 `--me-only` 后，Bob
也应能成功调用 `/chat`。脚本每次使用 `prompt=select_account`，不会在 `.env` 收集或
保存 Alice/Bob 密码。

不传 `thread_id` 时，`/chat` 仍自动生成 UUID；同一 `thread_id` 的历史仅保存在当前
进程内存，重启后丢失。认证没有改变既有 ChatRequest/ChatResponse contract。

### 错误语义与 wrong resource

- 缺少或无效 Bearer、错误 signature、过期、尚未生效、错误 issuer/tenant、Graph
  token 或其他错误 audience：`401 Unauthorized`。
- Token A 有效但 `scp` 不含 `access_as_user`：`403 Forbidden`。
- OIDC metadata 或 JWKS 暂时不可用：`503 Service Unavailable`。

若已有一个用于调试的 Graph access token，可在受控本机环境中用它调用 `/me` 或
`/chat`；预期 `401`，因为它的 `aud` 不是 Work Assistant API。不要为此给 Test
Client 添加 Graph permissions，也不要把 token 保存进仓库或日志。

### 精确撤销某个用户的开发 consent

`examples/revoke_entra_user_consent.ps1` 用于清理 Alice/Bob 的个人 delegated consent。
脚本根据两个 **Application (client) ID** 自动解析 Enterprise Application/service
principal，只匹配：

```text
consentType = Principal
principalId = 指定用户的 Object ID
clientId = vitos-work-assistant-client 或 vitos-work-assistant-api service principal
```

它会跳过 `AllPrincipals` tenant-wide grants、其他用户和其他应用。默认是 dry run，
只有显式添加 `-Execute` 并再次输入目标 UPN 才删除。

macOS 尚未安装 PowerShell 时：

```bash
brew install powershell
pwsh -Command 'Install-Module Microsoft.Graph -Scope CurrentUser'
```

从仓库根目录加载真实 ID，先执行只读预览：

```bash
set -a
source apps/agent/.env
set +a

pwsh -File apps/agent/examples/revoke_entra_user_consent.ps1 \
  -TenantId "$ENTRA_TENANT_ID" \
  -UserPrincipalName "bob@vitosdemo.com" \
  -ClientApplicationId "$ENTRA_TEST_CLIENT_ID" \
  -ApiApplicationId "$ENTRA_WORK_ASSISTANT_API_CLIENT_ID"
```

脚本会要求管理员 interactive login，并请求 `Application.Read.All`、`User.Read.All` 与
高权限 `DelegatedPermissionGrant.ReadWrite.All`。只应在受控 Demo tenant 中由适当的
Entra administrator 使用。确认 dry-run 表格中的每一行都属于 Bob 后，再执行：

```bash
pwsh -File apps/agent/examples/revoke_entra_user_consent.ps1 \
  -TenantId "$ENTRA_TENANT_ID" \
  -UserPrincipalName "bob@vitosdemo.com" \
  -ClientApplicationId "$ENTRA_TEST_CLIENT_ID" \
  -ApiApplicationId "$ENTRA_WORK_ASSISTANT_API_CLIENT_ID" \
  -Execute
```

删除是可通过用户重新 consent 恢复的，但已经签发的 access token 在自身过期前仍可能
有效。Entra portal 的 User consent 页面也可能延迟几分钟才反映变化。脚本不会删除
App Registration、Enterprise Application、用户或 admin consent。

## 测试

单元测试使用本地临时 RSA key，不访问 Entra、Graph、真实 JWKS 或 LLM：

```bash
python -m pytest apps/agent/tests
```

覆盖匿名 health、Bearer 缺失/格式错误、无效 signature、过期/`nbf`、错误
audience、issuer/tenant、token version、缺少 scope，以及有效 Alice/Bob claims。
`test_mcp.py` 继续用本地 Streamable HTTP MCP Server 验证原有 Agent/MCP integration。

## 参考资料

- [Microsoft identity platform access tokens](https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens)
- [Expose a web API scope](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-configure-app-expose-web-apis)
- [Configure a client to access a web API](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-configure-app-access-web-apis)
- [Configure a desktop public client and redirect URI](https://learn.microsoft.com/en-us/entra/identity-platform/scenario-desktop-app-configuration)
- [MSAL Python interactive token acquisition](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens)
- [Microsoft Graph application manifest reference](https://learn.microsoft.com/en-us/entra/identity-platform/reference-microsoft-graph-app-manifest)
- [List delegated permission grants](https://learn.microsoft.com/en-us/graph/api/oauth2permissiongrant-list?view=graph-rest-1.0)
- [Remove-MgOauth2PermissionGrant](https://learn.microsoft.com/en-us/powershell/module/microsoft.graph.identity.signins/remove-mgoauth2permissiongrant?view=graph-powershell-1.0)
- [PyJWT API reference](https://pyjwt.readthedocs.io/en/stable/api.html)
