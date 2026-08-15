# vitos-m365-mcp

`vitos-m365-mcp` 是一个只读、无状态的 Streamable HTTP MCP Server，通过 Microsoft Graph 提供 SharePoint 搜索和文档读取能力。

## 认证架构

该服务既是受 Microsoft Entra 保护的资源，也是一个机密中间层：

```text
MCP Client / Agent
  │ Token M：aud = vitos-m365-mcp，scp = access_as_user
  ▼
vitos-m365-mcp
  │ 验证签名、issuer、tenant、audience、有效期、azp、oid 和 scope
  │ OBO：Token M → Token G
  ▼
Microsoft Graph
  │ Token G：aud = Microsoft Graph，包含用户委托权限
  ▼
当前登录用户有权访问的 SharePoint 数据
```

Token M 绝不会被转发给 Graph。只有实际调用 MCP tool 时才会获取 Token G；Token G 仅在该次调用期间保存在内存中，不会出现在 tool 参数、返回结果或日志中。

MCP 端点还实现了 OAuth Protected Resource Metadata（RFC 9728）：

- MCP 端点：`/mcp`
- 公开存活检查：`/health`
- `/mcp` 的 metadata：`/.well-known/oauth-protected-resource/mcp`

匿名请求或携带无效 Token 的 MCP 请求会收到 `401`；Token 有效但缺少 MCP 委托 scope 的请求会收到 `403`。健康检查和 metadata 端点无需认证。

## 功能

- `search_sharepoint(query, top=5)` 搜索 Microsoft Graph 中的 `driveItem` 资源。
- `read_document(drive_id, item_id)` 提取 DOCX、UTF-8 TXT 和 Markdown 文档的文本。

PDF 解析和写操作不在当前范围内。

## Microsoft Entra 配置

### 1. MCP Resource Server

创建一个名为 `vitos-m365-mcp` 的单租户 App Registration：

1. 记录 Directory（tenant）ID 和 Application（client）ID。
2. 在 **Manifest** 中，将 `requestedAccessTokenVersion` 设置为 `2`。
3. 在 **Expose an API** 中，将 Application ID URI 设置为 `api://<ENTRA_MCP_CLIENT_ID>`。
4. 创建名为 `access_as_user` 的委托 scope。对于内部服务，**Who can consent** 通常应设置为 **Admins only**。
5. 在 **API permissions** 中添加 Microsoft Graph 委托权限 `Files.Read.All`，并授予租户级管理员同意。
6. 为本地开发创建 client secret。该 secret 只属于这个 MCP Server，用于执行从 MCP 到 Graph 的 OBO 交换。生产环境应使用证书或 workload identity。

不要为该服务复用 Work Assistant API 或浏览器的 App Registration。发送到 `/mcp` 的 access token 必须以这个 MCP App Registration 的 client ID 作为 `aud` claim。

### 2. 真实验证客户端

Microsoft Entra 不提供 Dynamic Client Registration，因此真实的交互式测试需要一个预先注册的 public client。创建另一个名为 `vitos-m365-mcp-live-test` 的单租户 App Registration：

1. 在 **Authentication → Advanced settings** 中启用 **Allow public client flows**。
2. 不要创建 client secret。
3. 在 **API permissions → My APIs** 中添加 `vitos-m365-mcp/access_as_user` 委托权限，并授予管理员同意。
4. 可以选择将该 public client 的 Application ID 添加到 `vitos-m365-mcp → Expose an API → Authorized client applications`，并选择 `access_as_user`。

这个 App Registration 只供 `scripts/verify_live.py` 中的 Device Code 登录使用，不属于部署后的 MCP 服务。

### 3. 后续接入 Agent

当前阶段不会修改 Agent。后续需要为 Agent 的 App Registration 添加 MCP `access_as_user` 委托权限，并在 MCP App Registration 中预授权该 Agent。之后，Agent 会将自己的 API Token 交换为 Token M，再把 Token M 发送给本服务。在 Agent 代码完成相应修改前，当前 Agent 无法调用这个受保护的 MCP 端点。

## 配置

将 `.env.example` 复制为 `.env`，然后完成配置：

```dotenv
MCP_HOST=127.0.0.1
MCP_PORT=8001
MCP_PATH=/mcp
MCP_RESOURCE_URL=http://127.0.0.1:8001/mcp

ENTRA_TENANT_ID=<Directory tenant ID>
ENTRA_MCP_CLIENT_ID=<vitos-m365-mcp Application client ID>
ENTRA_MCP_CLIENT_SECRET=<仅供服务端使用的 secret>
ENTRA_REQUIRED_SCOPE=access_as_user
```

`MCP_RESOURCE_URL` 是对外可见的准确 MCP 端点地址，用于 Resource Metadata 和 `WWW-Authenticate`。在部署环境中，即使进程监听的是内部 HTTP 地址，这里也必须配置公开的 HTTPS URL。

向 MCP Client 公布的 scope 默认为：

```text
api://<ENTRA_MCP_CLIENT_ID>/access_as_user
```

如果 App Registration 使用了自定义 Application ID URI，请显式配置完整值：

```dotenv
ENTRA_MCP_SCOPE=api://mcp.internal.example/access_as_user
```

## 本地开发

需要 Python 3.11 或更高版本：

```bash
uv sync --locked --extra dev
set -a
source .env
set +a
uv run vitos-m365-mcp
```

运行自动化测试：

```bash
uv run pytest
```

自动化测试使用本地签名密钥以及模拟的 OBO 和 Graph 边界，覆盖 Token claim、无效签名、401/403 响应、Protected Resource Metadata、scope 强制检查，以及请求范围内 Token M 与 Token G 的隔离。

## 真实端到端验证

该仓库包含一个独立的真实验证脚本。它只使用 MSAL 通过 Device Code Flow 获取真实的 Token M，随后使用官方 MCP Inspector CLI 完成 MCP 初始化、`tools/list` 以及一次真实的 `search_sharepoint` 调用。最后一次调用必须完整执行 Token M → OBO → Token G → Microsoft Graph 链路。

在终端 A 中保持 MCP Server 运行，然后在终端 B 中执行：

```bash
cp .env.live.example .env.live
# 填写 ENTRA_LIVE_TEST_CLIENT_ID 和其他公开 ID。
set -a
source .env.live
set +a
uv run python scripts/verify_live.py
```

该脚本会执行以下检查：

1. 匿名访问 `/health` 成功。
2. 匿名访问 `/mcp` 时返回符合标准的 401 challenge。
3. Protected Resource Metadata 包含正确的 Entra issuer 和完整 MCP scope。
4. Device Code Flow 为 live-test public client 获取真实的 Token M。
5. `npx -y @modelcontextprotocol/inspector@2.2.0` 列出真实 MCP tools。
6. Inspector 调用 `search_sharepoint`；MCP Server 执行真实 OBO，并由 Graph 返回当前登录用户的真实响应。

Token M 不会被打印，也不会作为命令行参数传递。它只会被写入权限模式为 `0600` 的临时 Inspector 配置文件，并在 `finally` 块中删除。

默认的 `MCP_INSPECTOR_PACKAGE` 固定为验证该流程时使用的 CLI 版本。升级时应明确修改版本，并重新执行真实验证后再更新该基准。

搜索结果为空仍然代表认证、OBO 和 Graph 验证成功；这只表示测试查询没有匹配到该用户有权查看的文档。

## 容器

```bash
docker build -t vitos-m365-mcp:local .
docker run --rm \
  --env-file .env \
  -p 127.0.0.1:8001:8001 \
  vitos-m365-mcp:local
```

镜像以非 root 用户运行。绝不要将 `.env`、MCP 凭据、Token M 或 Token G 写入镜像。

## 参考资料

- [MCP 授权规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
- [MCP Inspector](https://github.com/modelcontextprotocol/inspector)
- [Microsoft identity platform OBO 流程](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow)
- [使用 Microsoft Entra 配置 MCP Server 授权](https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-mcp)
