# Work Assistant Red Team Bridge

独立的 REST 接入服务，供 Prisma AIRS AI Red Teaming 调用现有 Work Assistant。
不修改 `apps/web`、`apps/agent`，不绕过 Agent、Skills、网关、MCP 或 OBO。

```text
操作人员自己的浏览器 ── Microsoft 设备码登录 / MFA
                                      │
                                      ▼
Prisma AIRS ── 测试 API Key ── redteam-bridge
                                      │ MSAL 用户授权缓存 → Token A
                                      ▼
                              现有 Agent /chat
                                      │ 原来的两段 OBO
                                      ▼
                                 MCP → Graph
```

一个部署固定代表一个测试用户，由 `REDTEAM_USER_OID` 绑定。API 请求不能选择其他用户，
也不接收客户端提供的 Token、工具定义或审批结果。AIRS 不需要执行用户 OAuth，
也不持有 Microsoft 用户密码、Access Token 或 Refresh Token。

## 1. Entra 配置

当前 Work Assistant 已正常运行。本节只配置新增的 `vitos-redteam-client`，复用现有租户、
Work Assistant API 和测试用户。原有 API scope、Token 版本、OBO、MCP 与 Graph 权限
无需重新配置或逐项检查。

### 1.1 Step 1：新建 `vitos-redteam-client`

1. 从截图中的 **Default Directory → App registrations → All applications** 页面开始，
   点击 **+ New registration**。
2. **Name** 填写 `vitos-redteam-client`。
3. **Supported account types** 选择当前目录的单租户选项：
   **Accounts in this organizational directory only**，或新版页面的
   **Single tenant only — `<当前目录>`**。
4. **Redirect URI (optional)** 保持为空，点击 **Register**。
5. 在新应用 **Overview** 中复制 **Application (client) ID**，保存为后面要填写的
   `REDTEAM_CLIENT_ID`。

[Microsoft：注册应用](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app)

### 1.2 Step 2：为新客户端启用设备码登录

以下操作都在**新建的 `vitos-redteam-client`** 中完成：

1. 进入 **Authentication → Advanced settings**。
   如果是 **Authentication (Preview)** 页面，进入 **Settings** 标签。
2. 找到 **Allow public client flows**，设置为 **Yes / Enabled**。
3. 点击 **Save**。

当前设备码方案不需要为新客户端创建 Client Secret 或添加 redirect URI。
[Microsoft：Public Client 与设备码配置](https://learn.microsoft.com/en-us/entra/identity-platform/scenario-desktop-app-configuration#enable-public-client-flow)

### 1.3 Step 3：添加新客户端的委托权限

先授权新客户端调用已有 Work Assistant API：

1. 在 **`vitos-redteam-client` → API permissions** 点击 **+ Add a permission**。
2. 选择 **APIs my organization uses**，搜索 `vitos-work-assistant-api`，或直接输入其
   Application (client) ID：`3be46bfa-3306-4322-bfcb-13b1f526c2cb`。
3. 点击搜索结果中的 **`vitos-work-assistant-api` → Delegated permissions**，
   勾选已有的 **`access_as_user`**，点击 **Add permissions**。

此路径已在当前租户中验证可找到该 API；**My APIs** 列表为空时也直接使用上述路径。

新客户端必须添加的业务权限为：

| API | Permission | Type |
|---|---|---|
| `vitos-work-assistant-api` | `access_as_user` | Delegated |

#### 可选：显式配置 OIDC 权限，供管理员提前统一同意

现有网页使用的 MSAL Browser 和桥接服务使用的 MSAL Python，都会在登录请求中自动加入
`openid`、`profile`、`offline_access`。这不是红队客户端特有的要求，也不要求一定先在
portal 的 Configured permissions 表中手工列出它们；Entra 支持登录时动态请求和同意。

| Scope | 用途 |
|---|---|
| `openid` | 请求 ID Token，用于识别登录用户 |
| `profile` | 请求用户基本身份 claims，包括本服务绑定用户所用的 Object ID |
| `offline_access` | 请求 Refresh Token，供 MSAL 后续静默续期 |

如果希望把这三个 scope 也纳入下一步的管理员统一同意，可在**新客户端**中执行：

1. 点击 **+ Add a permission → Microsoft Graph → Delegated permissions**。
2. 勾选 **`openid`**、**`profile`**、**`offline_access`**。
3. 点击 **Add permissions**，随后按 Step 4 授予管理员同意。

执行此可选操作后，权限列表会额外显示：

| API | Permission | Type |
|---|---|---|
| Microsoft Graph | `openid` | Delegated |
| Microsoft Graph | `profile` | Delegated |
| Microsoft Graph | `offline_access` | Delegated |

手动声明是否可省略取决于新客户端所需同意是否已满足，以及租户是否允许用户同意。
若登录要求管理员批准，应由管理员处理新客户端实际请求的权限；不能通过省略 portal
中的声明来绕过 consent。原有网页客户端的 consent 不会自动转移给新注册。
如果已经添加这三项，可以保留，无需删除。SharePoint 数据访问继续使用现有 OBO 链路。

[Microsoft：客户端 API 权限](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-configure-app-access-web-apis#add-permissions-to-access-your-web-api)、
[MSAL Browser 默认 scope 与动态同意](https://learn.microsoft.com/en-us/entra/msal/javascript/browser/resources-and-scopes#default-scopes)、
[OIDC scopes](https://learn.microsoft.com/en-us/entra/identity-platform/scopes-oidc#openid-connect-scopes)

### 1.4 Step 4：只为新客户端授予管理员同意

1. 保持在 **`vitos-redteam-client` → API permissions**。
2. 由有权限的管理员点击 **Grant admin consent for `<当前目录>`**，确认弹窗。
3. 点击 **Refresh**，确认上一步添加的权限显示 **Granted for `<当前目录>`**。

这一步是给**新增的客户端**授权，原有网页客户端的 consent 不会自动转移到新注册。
如果按钮不可用，需由具备 Cloud Application Administrator 或相应授权角色的管理员完成。
[Microsoft：管理员同意](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent#grant-admin-consent-in-app-registrations-pane)

本流程使用上述管理员同意完成授权，不再要求额外修改现有 API 的 **Authorized client
applications**。API 预授权是可选的 consent 配置，不是此接入方案的必需步骤。
[Microsoft：可选客户端预授权](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-configure-app-expose-web-apis#add-a-scope-requiring-admin-and-user-consent)

### 1.5 Step 5：绑定已有测试用户并填写配置

Tenant ID 和 API Client ID 直接复用现有 Agent 配置，无需再进入 portal 逐项核查：

| 桥接变量 | 取值来源 |
|---|---|
| `ENTRA_TENANT_ID` | 直接沿用现有 `apps/agent/.env` 中的同名变量和值 |
| `REDTEAM_CLIENT_ID` | Step 1 新建注册的 Application (client) ID |
| `ENTRA_WORK_ASSISTANT_API_CLIENT_ID` | 直接沿用现有 `apps/agent/.env` 中的同名变量和值 |
| `REDTEAM_USER_OID` | 本次选用的已有测试用户，例如 Alice 的 Object ID |

取得测试用户 OID：

1. 进入 **Microsoft Entra ID → Users → All users**，选择已有的测试用户。
2. 在用户 **Overview → Basic information / Basic info** 中复制 **Object ID**。
   这里使用用户的 Object ID，不是应用注册的 Object ID。

[Microsoft：获取用户 Object ID](https://learn.microsoft.com/en-us/partner-center/account-settings/find-ids-and-domain-names#find-the-user-object-id)

按第 2 节复制 `.env.example` 后，填写 `apps/redteam-bridge/.env`。截图中现有 API 的
Client ID 为 `3be46bfa-3306-4322-bfcb-13b1f526c2cb`；如果后续已变更，以正在运行的
Agent 配置为准：

```dotenv
ENTRA_TENANT_ID=<现有 Agent 的同名变量值>
REDTEAM_CLIENT_ID=<Step 1 新建客户端的 Application client ID>
ENTRA_WORK_ASSISTANT_API_CLIENT_ID=3be46bfa-3306-4322-bfcb-13b1f526c2cb
REDTEAM_USER_OID=<已有测试用户的 Object ID>
REDTEAM_API_KEY=<按第 2 节生成的随机 API Key>
```

桥接容器仍从自己的 `.env` 接收配置；将现有 API 的同名变量复制到这里即可，不会自动
读取 Agent 的 `.env`。`REDTEAM_CLIENT_ID` 对应新增客户端，
`ENTRA_WORK_ASSISTANT_API_CLIENT_ID` 对应已经运行的 API。

尖括号内容替换为实际值。`REDTEAM_API_KEY` 是桥接服务的调用凭据，由你生成；
它不是 Entra Client Secret。

Entra 增量配置到此完成。继续按第 2 节构建、第 3 节完成一次设备码登录，再按第 4 节
调用 REST 接口。登录时选择 `REDTEAM_USER_OID` 对应的测试用户。

### 仅在新增客户端配置或登录失败时排查

这些是出现相应错误后的处理，不是上线前需要重做的现有配置：

| 现象 | 处理 |
|---|---|
| API 搜索没有结果 | 确认当前标签是 **APIs my organization uses**，并按现有 API 的 Application (client) ID 搜索；不要使用应用 Object ID |
| `invalid_client`，详细错误包含 `AADSTS7000218` / 要求 `client_secret` | 确认 `REDTEAM_CLIENT_ID` 是新红队客户端，并检查该注册的 Public Client Flow 设置；不要把原 API 的 Client ID 填在这里 |
| `consent_required` / Need admin approval | 检查新注册的权限和 Step 4 的管理员同意状态 |
| 新客户端登录提示用户未分配到应用 | 在 **Enterprise applications → `vitos-redteam-client` → Users and groups** 分配测试用户，仅在该企业应用要求分配时处理 |
| 设备码登录或续期被 Conditional Access 阻止 | 查看 **Monitoring & health → Sign-in logs → 失败记录 → Conditional Access**，由租户管理员判断是否允许本次设备码流程；原有网页登录成功不等于设备码流程一定被允许 |
| `wrong_test_user` | 重新执行登录并选择 `REDTEAM_USER_OID` 对应用户；浏览器若默认登录管理员，可使用无痕窗口 |

排查参考：[Public Client 错误](https://learn.microsoft.com/en-us/troubleshoot/entra/entra-id/app-integration/confidential-client-application-authentication-error-aadsts7000218)、
[设备码流程与 Conditional Access](https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-authentication-flows)。

## 2. 配置与构建

以下命令都在仓库根目录运行。Compose 需要支持 `env_file.required`（v2.24.0+）。

```bash
cp apps/redteam-bridge/.env.example apps/redteam-bridge/.env
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

编辑新 `.env` 中四个 ID，并用生成的值替换 `REDTEAM_API_KEY`。配置项如下：

| 变量 | 含义 / 默认值 |
|---|---|
| `ENTRA_TENANT_ID` | 测试用户所在租户，沿用 Agent 的同名变量和值 |
| `REDTEAM_CLIENT_ID` | 新增的 red-team Public Client 注册 |
| `ENTRA_WORK_ASSISTANT_API_CLIENT_ID` | 原有 Work Assistant API 注册，沿用 Agent 的同名变量和值 |
| `REDTEAM_USER_OID` | 固定测试用户的 Object ID |
| `REDTEAM_API_KEY` | AIRS 调用桥接 API 的凭据，至少 32 字符 |
| `REDTEAM_ASSISTANT_URL` | `http://agent:8000/chat`；也可以是现有站点的完整 `/api/chat` URL |
| `REDTEAM_CACHE_PATH` | `/data/msal_token_cache.json` |
| `REDTEAM_CONVERSATIONS_PATH` | `/data/conversations.sqlite3` |
| `REDTEAM_REQUEST_TIMEOUT` | 上游 HTTP 超时，默认 120 秒；AIRS 和反向代理也应允许足够的请求时间 |

```bash
docker compose --profile redteam build redteam-bridge
```

`redteam` 是可选 profile，原有 `docker compose up` 不启动接入服务。

## 3. 首次授权

```bash
docker compose --profile redteam run --rm --no-deps redteam-bridge redteam-bridge-login
```

命令会显示 Microsoft 登录网址和设备码。在**你自己电脑的浏览器**打开网址并输入设备码，
使用 `REDTEAM_USER_OID` 对应用户登录，完成 MFA，然后等待命令显示授权成功。
服务器不需要浏览器，也不需要登录 Prisma 的服务器。

登录错误用户时拒绝保存新授权，保留原来的缓存。成功时 MSAL 缓存写入 `redteam-data`
Volume；API 进程每次取 Token 都重新读取缓存，因此重新授权后不需要重启服务。
Access Token 到期时，MSAL 使用 Refresh Token 静默续期。

如果 Entra 禁止设备码流程、要求重新 MFA 或撤销授权，需由操作人员解决策略 / consent
问题并重新执行上面的登录命令。当前版本只实现设备码授权，不自动切换其他登录方式。
重新授权应在扫描暂停时操作：登录命令与 API 对缓存使用同一文件锁，以避免互相覆盖。

## 4. 启动与 REST 接口

```bash
docker compose --profile redteam up -d redteam-bridge
curl http://127.0.0.1:8081/health
```

`/health` 只检查服务进程；完整认证和 OBO 需要实际 `/redteam/chat` 请求验证。

设置本地 shell 变量 `REDTEAM_API_KEY` 为 `.env` 中的相同值后调用：

```bash
curl http://127.0.0.1:8081/redteam/chat \
  -H "Authorization: Bearer ${REDTEAM_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"How do I connect to VPN?"}'
```

示例响应（ID 为示意值）：

```json
{
  "response": "The assistant's final answer...",
  "conversation_id": "11111111-1111-4111-8111-111111111111",
  "status": "completed",
  "interrupts": [],
  "trace_id": "actual-portkey-trace-id",
  "sources": []
}
```

首轮省略 `conversation_id` 或传 `null`，每次建立独立对话。后续轮次携带响应中的 ID：

```json
{
  "prompt": "What should I do if that fails?",
  "conversation_id": "11111111-1111-4111-8111-111111111111"
}
```

服务保存 AG-UI 的消息、工具参数与结果、reasoning 元数据和状态快照，作为下一轮上下文。
不同对话可并发，同一个对话串行执行。SQLite 数据同样在 Volume 中，容器重启后可继续。
目前仅支持**一个服务进程、一个副本**；容器使用一个 Uvicorn worker。
对话数据不自动清理，完成扫描后可按需清理测试 Volume；`docker compose down -v`
会同时删除缓存和历史，下次必须重新授权。

### Jira 人工审批

Agent 返回审批 interrupt 时，响应为 HTTP 200，`status` 为 `approval_required`，
`interrupts` 保留 Agent 提供的审批信息；无文本时 `response` 明确说明需要人工审批。
桥接层**不会自动批准或拒绝、不会调用 resume、不会把 prompt 中的 “yes” 当作批准**。
该会话后续调用返回 HTTP 409，需开始新会话。这可以验证 Agent 是否正确停在审批边界，
但不覆盖用户批准后的 Jira 创建流程；审批信息也不会自动成为 AIRS 的工具执行证据。

### 错误

错误响应为 `{"detail":{"code":"...","message":"..."}}`，没有成功回答字段。

| 状态 | 场景 |
|---|---|
| 401 | 缺少或错误的桥接 API Key |
| 503 / `user_authorization_required` | 用户尚未授权、授权失效或 Agent 拒绝 Token A |
| 403 | Agent 拒绝请求，检查权限、OBO consent 与 Agent 日志 |
| 404 | 对话不存在，或不属于当前配置的测试身份 |
| 409 | 会话已停在人工审批边界 |
| 429 / `gateway_rate_limited` | Agent 的限流事件，带 `Retry-After: 60` |
| 412 / `gateway_budget_exhausted` | 网关预算耗尽 |
| 446 / `gateway_guardrail_blocked` | 网关安全策略拦截 |
| 502 | 上游不可达、执行失败、流被截断或不支持的协议事件 |
| 504 | 上游 HTTP 超时 |

即使 Agent 已返回 HTTP 200，流中的 `RUN_ERROR` 也会转换为对应的错误状态。
只有完整收到 `RUN_FINISHED` 的轮次才写入对话历史；失败的部分响应不作为成功答案保存。

## 5. Prisma AIRS Target 配置

1. Connection Method 选择 **REST API**，Text modality。
2. URL 使用 AIRS 可以访问的 `https://<your-host>/redteam/chat`。
3. Authentication 选择 **Using Headers**，配置
   `Authorization: Bearer <REDTEAM_API_KEY>`，不选择用户 OAuth。
4. 请求模板为 `{"prompt":"{INPUT}","conversation_id":null}`。
5. 先取得真实样例响应，将 `response` 的值替换为 `{RESPONSE}`，其他字段保持样例结构。
6. 单轮扫描每次使用空 conversation ID；多轮扫描启用 **Supports Sessions = Yes**，
   配置 `response_id_field` 和 `request_id_field` 都指向 `conversation_id`，让 AIRS 在同一
   攻击会话的后续轮次回传 ID，不同会话从 `null` 开始。具体字段路径格式以租户界面为准，
   Validate 时确认 ID 确实回传；否则每轮都会新建会话。
7. 在 Advanced Configurations 配置实际限流、拦截响应：限流状态 429；Guardrails / Content
   Filters 状态 446。预算错误 412 和审批 409 需与安全拦截区分。

默认端口只绑定主机 `127.0.0.1:8081`。需要通过你现有的 HTTPS 反向代理将上述路径发布给
AIRS，或使用已有的 Network Channel 让 AIRS 能到达该服务。后者仅提供网络连通，
**不使用 Custom Target Adapter**。若反向代理在另一台机器，可在仓库根 `.env` 设置
`REDTEAM_BIND_ADDRESS` 为代理可达的主机接口；不要把 localhost URL 直接填给云端 AIRS。

Volume 包含用户刷新凭据和内部文档 / 对话数据，不提交到 Git，不放进镜像。
API Key 持有者可通过此服务以固定测试用户运行 Agent，因此只将该凭据配置给相应测试任务。
当前 MVP 使用文件权限和私有 Volume 保存缓存，没有额外实现静态加密。

首次联调应验证：无授权返回明确错误；正确用户登录后请求走完 OBO；重启后仍可调用；
Token 正常续期；两轮 SharePoint 问答保留上下文；Jira 创建停在审批边界。
本地 mock / SDK 测试不代表已完成真实 Entra、Graph 或 AIRS 联调。

## 6. 本地开发与测试

本节直接运行本地 Python 服务，不构建或启动 Docker image。先完成第 1 节的 Entra
客户端配置，并准备好现有 Agent 和 Web 的依赖及环境变量。

### 6.1 本地环境变量与依赖

从仓库根目录进入 Bridge 目录，首次创建配置文件并安装依赖；已有 `.env` 时保留其内容：

```bash
cd apps/redteam-bridge
test -f .env || cp .env.example .env
uv sync --extra dev --frozen
```

在 `apps/redteam-bridge/.env` 中按第 1.5 节填写四个 ID 和 Bridge 自己的
`REDTEAM_API_KEY`（至少 32 字符）；已有的正确值可以直接保留。将 Docker 默认地址和
数据路径替换为本地配置：

```dotenv
REDTEAM_ASSISTANT_URL=http://127.0.0.1:8000/chat
REDTEAM_CACHE_PATH=./data/msal_token_cache.json
REDTEAM_CONVERSATIONS_PATH=./data/conversations.sqlite3
REDTEAM_REQUEST_TIMEOUT=120
```

Bridge 直接连接 Agent 的 8000 端口；`agent` 是 Docker 服务名，本地运行使用
`127.0.0.1`。两个相对数据路径以进程工作目录为基准，所以下面的登录和单独启动命令
都在 `apps/redteam-bridge` 下执行；一键脚本也会切换到该目录。

### 6.2 首次授权测试用户

在 `apps/redteam-bridge` 目录运行：

```bash
uv run --frozen --env-file .env redteam-bridge-login
```

按照终端显示的网址和设备码，在浏览器中使用 `REDTEAM_USER_OID` 对应用户完成登录和
MFA，等待授权成功。现有网页的登录状态不会自动成为 Bridge 的授权缓存，Bridge 需要
完成自己的设备码授权。缓存保存在 `data/msal_token_cache.json`，后续正常启动可复用；
出现 `user_authorization_required` 时按第 3 节说明重新授权。

### 6.3 启动本地服务

完成上述准备后，先停止之前单独启动的本地 Agent、Web 和 Bridge，避免占用相同端口。
在仓库根目录运行一键脚本：

```bash
./scripts/start-local.sh
```

脚本依次启动 Agent（8000）、Web（5173）和 Bridge（8081），并检查各自是否就绪。
每次启动会清除 `logs/` 下旧的 `.log` 文件，分别写入 `agent.log`、`web.log` 和
`redteam-bridge.log`。按 `Ctrl+C` 一起停止三个服务。脚本只启动服务，不自动执行
设备码登录或自动化测试；外部 MCP 仍按现有 Agent 配置独立运行。

如果现有 Agent 已单独运行，也可以只在 `apps/redteam-bridge` 目录启动 Bridge：

```bash
uv run --frozen --env-file .env uvicorn redteam_bridge.app:app --host 127.0.0.1 --port 8081 --workers 1
```

### 6.4 手动验证请求

新开终端，从仓库根目录进入 Bridge 目录并加载调用密钥：

```bash
cd apps/redteam-bridge
source .env

curl http://127.0.0.1:8081/health

curl http://127.0.0.1:8081/redteam/chat \
  -H "Authorization: Bearer ${REDTEAM_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"hi"}'
```

`/health` 应返回 `{"status":"ok"}`；聊天应返回 `status: completed` 和 `response`。
健康检查只验证 Bridge 进程，聊天请求才会经过真实的用户授权、Agent、MCP 和模型链路。
需要验证多轮上下文时，将首轮返回的 `conversation_id` 放入下一次请求，格式见第 4 节。

如果返回 `agent_execution_failed`，先检查 `logs/agent.log`；返回
`user_authorization_required` 时重新执行设备码登录。`REDTEAM_REQUEST_TIMEOUT`
控制 Bridge 等待 Agent 的 HTTP 超时，Agent 的 MCP 初始化超时由其 `MCPToolset`
的 `init_timeout` 单独控制，增大前者不会改变后者。

### 6.5 单元测试

在 `apps/redteam-bridge` 目录运行：

```bash
uv run --frozen --extra dev pytest -q
```

现有单元测试使用模拟服务和临时数据，不需要真实密钥、设备码授权或运行中的 Agent。
它们通过后，仍需按上面的步骤验证真实联调。

参考：

- [MSAL 设备码与静默授权](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens)
- [MSAL 缓存持久化](https://learn.microsoft.com/en-us/entra/msal/python/advanced/msal-python-token-cache-serialization)
- [Prisma AIRS REST 与会话配置](https://docs.paloaltonetworks.com/prisma-airs/ai-red-teaming/identify-ai-system-risks-with-ai-red-teaming/get-started-with-prisma-airs-ai-red-teaming/targets/connection-methods/add-a-target-rest-api-or-streaming-cm)
