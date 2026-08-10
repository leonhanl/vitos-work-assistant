# m365-mcp-http

这是 Vito's Work Assistant 的只读 Microsoft 365 MCP Server。它以长期运行的
Streamable HTTP 服务提供两个工具：

- `search_sharepoint(query, top=5)`：搜索当前用户有权访问的 SharePoint / Microsoft 365 文档。
- `read_document(drive_id, item_id)`：读取搜索结果中的 DOCX、UTF-8 TXT 或 Markdown 文档。

默认 MCP endpoint 是：

```text
http://127.0.0.1:8001/mcp
```

服务继续使用 Microsoft Graph、Delegated Permission、Device Code Flow 和本地
MSAL Token Cache。当前身份模型是 single-user development mode：所有 MCP 请求都
使用同一个开发者预先登录的 Microsoft 365 delegated identity。本阶段未实现 OBO、
多用户身份、MCP HTTP authentication 或任何写操作。

## 架构与实现选择

```text
MCP Client
    │ Streamable HTTP
    ▼
m365-mcp-http
    │ delegated token from local MSAL cache
    ▼
Microsoft Graph → SharePoint / Microsoft 365
```

服务使用官方 MCP Python SDK v1.x 的 `FastMCP` 高层 API：

```python
FastMCP(stateless_http=True, json_response=True)
mcp.run(transport="streamable-http")
```

两个工具都是独立的 request/response 操作，不依赖 server-side MCP session state，
因此不配置 session persistence、event store 或 resumability。项目目录和 Python
distribution 名称是 `m365-mcp-http`；Python package 保留为 `m365_mcp`，从而保持
现有 module 命令和 MSAL cache 兼容。

参考：[官方 MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)、
[Streamable HTTP specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)。

## 1. Microsoft Entra App Registration

在 [Microsoft Entra admin center](https://entra.microsoft.com/) 中：

1. 创建 App registration，不要创建 client secret。
2. 记录 Directory (tenant) ID 和 Application (client) ID。
3. 添加 Microsoft Graph Delegated permissions：`User.Read` 和 `Files.Read.All`。
4. 按租户要求完成 `Files.Read.All` 的 admin consent。
5. 在 Authentication → Advanced settings 中启用 Allow public client flows。

Graph 代表当前登录用户执行，并继续由 SharePoint / OneDrive ACL 完成 security
trimming；本服务不会提升或绕过用户权限。

## 2. 安装

需要 Python 3.11 或更新版本，并沿用项目现有 pip 工作流：

```bash
cd /path/to/vitos-work-assistant
python3 -m venv .venv
source .venv/bin/activate
cd services/m365-mcp-http
python -m pip install -e ".[dev]"
```

依赖保持为官方 MCP Python SDK 稳定 v1.x：`mcp>=1.29,<2`。

## 3. 配置

```bash
cd /path/to/vitos-work-assistant/services/m365-mcp-http
cp .env.example .env
```

编辑 `.env`：

```dotenv
M365_TENANT_ID=your-tenant-id
M365_CLIENT_ID=your-client-id

# Optional; the defaults are shown here.
M365_TOKEN_CACHE_PATH=~/.cache/m365-mcp/msal_token_cache.json
MCP_HOST=127.0.0.1
MCP_PORT=8001
MCP_PATH=/mcp
```

模块不会自动加载 dotenv。登录或启动前，把变量导入当前 shell：

```bash
set -a
source .env
set +a
```

默认只监听 `127.0.0.1`，用于本地受信任开发环境。当前 MCP HTTP endpoint 没有
authentication；不要把它暴露给不受信任网络。可以主动设置 `MCP_HOST=0.0.0.0`，
但服务会记录安全警告，而且官方 SDK 的 DNS-rebinding protection 仍然生效。生产
暴露需要另行设计 authentication、TLS 和网络边界。

## 4. Device Code 登录

在普通交互终端中执行：

```bash
set -a
source .env
set +a
python -m m365_mcp.auth login
```

终端会显示 Device Code 登录地址和短期 user code。成功后 MSAL cache 默认保存到：

```text
~/.cache/m365-mcp/msal_token_cache.json
```

保留旧 cache 路径是为了让迁移前的登录状态继续可用。在 Unix 系统上，新建的默认
cache 目录和文件权限分别为 `0700` 和 `0600`。cache 包含敏感登录状态，不要提交、
打印或发送给 LLM。

工具调用期间只使用 `acquire_token_silent()`。如果没有有效登录状态，服务会要求
重新运行 login 命令，不会在 HTTP request 中启动 Device Code Flow。

## 5. 启动 Streamable HTTP MCP Server

Device Code 登录只负责创建 token cache，不会启动 MCP Server。请在**终端 A**中
执行下面的命令，并保持该进程运行：

```bash
set -a
source .env
set +a
python -m m365_mcp.server
```

默认连接地址：

```text
http://127.0.0.1:8001/mcp
```

启动日志会显示 transport、host、port 和 path；关闭服务时会记录 shutdown。
日志不会包含 access token、refresh token、Authorization header、完整 cache 或文档正文。

## 6. 使用 MCP Inspector 验证

保持 Server 运行，在另一个终端启动官方 Inspector：

```bash
npx -y @modelcontextprotocol/inspector
```

在 Inspector 中选择 **Streamable HTTP**，连接：

```text
http://127.0.0.1:8001/mcp
```

执行 `list_tools` 应看到：

```text
search_sharepoint
read_document
```

先调用：

```text
search_sharepoint(query="VPN", top=5)
```

响应保持为精简后的现有 schema：

```json
[
  {
    "rank": 1,
    "name": "KB003 - How to Connect to Corporate VPN.docx",
    "summary": "To connect to the corporate VPN...",
    "web_url": "https://...",
    "drive_id": "...",
    "item_id": "..."
  }
]
```

再把结果中的 ID 传给：

```text
read_document(drive_id="...", item_id="...")
```

返回：

```json
{
  "name": "KB003 - How to Connect to Corporate VPN.docx",
  "web_url": "https://...",
  "content": "提取出的完整正文..."
}
```

## 7. 使用最小 Python MCP Client 验证

仓库包含一个使用官方 `streamable_http_client` 的示例；它会 initialize、列出工具，
然后调用 `search_sharepoint`。保持终端 A 中的 Server 运行，并在**终端 B**执行：

```bash
python examples/test_client.py --query VPN
```

自定义 endpoint：

```bash
python examples/test_client.py \
  --endpoint http://127.0.0.1:8001/mcp \
  --query "leave policy"
```

它不是 REST client，也不会自行构造 JSON-RPC。

## 8. 测试

测试不需要真实 Microsoft 365 账号：

```bash
python -m pytest
```

除原有 auth、Graph、normalization 和 document parser 测试外，
`tests/test_streamable_http.py` 会启动本地 MCP HTTP app，通过官方 client 完成：

```text
initialize → list_tools → call_tool(search_sharepoint) → mocked Graph
```

## 错误、日志与并发边界

- 401、403、404、429、Graph timeout 和网络错误继续映射为不含 token 的安全错误。
- 搜索和读取记录 started/completed；失败记录工具名和受控异常，不记录查询正文或文档正文。
- 每个工具调用创建独立 `GraphClient`；没有全局 current request、current user 或 token。
- token cache 每次读取到独立 MSAL cache 对象，保存使用权限收紧的临时文件和原子 replace。
- 当前服务为单进程、单一 delegated identity；没有 distributed lock、Redis 或多用户状态。

## 当前边界与下一阶段

支持 `.docx`、UTF-8 `.txt` 和 `.md`；PDF 和其他 Office 格式仍明确不支持。服务只允许
固定的 Microsoft Search、driveItem metadata 和 content download 调用，不暴露任意
Graph proxy。

`apps/agent` 尚未迁移到 Streamable HTTP MCP client，本任务也没有修改它。下一阶段只需
把 Agent 的 MCP connection 从本地 subprocess 配置改为上述 HTTP endpoint。更后的阶段
才考虑 Work Assistant 登录、OBO 和 per-user Graph delegated identity。
