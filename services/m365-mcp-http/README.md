# m365-mcp

这是一个只读、stdio 模式的最小 Microsoft 365 MCP Server，用来验证下面这条链路：

```text
MCP Client → search_sharepoint → Microsoft Search → driveItem
           → read_document    → Graph download → text extraction
```

它只暴露两个窄能力：

- `search_sharepoint(query, top=5)`：搜索当前用户有权限访问的 SharePoint / Microsoft 365 文档。
- `read_document(drive_id, item_id)`：读取搜索结果中的 DOCX、UTF-8 TXT 或 Markdown 文档。

当前版本不包含 LLM、DeepAgent、RAG、向量数据库、Web 服务、OBO、多用户认证或任何写操作。

## 1. 手工创建 Microsoft Entra App Registration

在 [Microsoft Entra admin center](https://entra.microsoft.com/) 中：

1. 打开 **App registrations**，创建一个 registration。不要创建 client secret。
2. 记录 **Directory (tenant) ID** 和 **Application (client) ID**。
3. 在 **API permissions** 中添加 Microsoft Graph 的 Delegated permissions：
   - `User.Read`
   - `Files.Read.All`
4. 按租户要求完成 `Files.Read.All` 的 admin consent。
5. 在 **Authentication → Advanced settings** 中，将 **Allow public client flows** 设为 **Yes**。

代码不会自动创建或修改 Entra 配置，也不使用 application permission、client credentials 或 client secret。

## 2. 安装

需要 Python 3.11 或更新版本：

```bash
cd /path/to/vitos-work-assistant
python3 -m venv .venv
source .venv/bin/activate
cd services/m365-mcp
python -m pip install -e ".[dev]"
```

项目使用官方 MCP Python SDK 的当前稳定 v1.x API，并将依赖限制为 `mcp>=1.29,<2`，避免自动切换到不兼容的大版本。

后续命令均假设当前工作目录为：

```text
/path/to/vitos-work-assistant/services/m365-mcp
```

并且仓库根目录中的 `.venv` 已激活。

## 3. 配置环境变量

M365 MCP 的环境配置与服务放在同一目录。复制服务自己的示例文件并填入真实值；`.env` 已被 Git 忽略：

```bash
cd /path/to/vitos-work-assistant/services/m365-mcp
cp .env.example .env
```

`.env` 的内容：

```dotenv
M365_TENANT_ID=your-tenant-id
M365_CLIENT_ID=your-client-id
```

`auth.py` 和 `server.py` 不会自动读取 dotenv 文件，而是读取进程环境变量。因此，每次打开新终端后，在登录或启动服务前先导入当前目录的 `.env`：

```bash
set -a
source .env
set +a
```

也可以由 MCP Client 的 server 配置直接传入这两个环境变量。

## 4. 登录

在普通终端中单独执行：

```bash
set -a
source .env
set +a
python -m m365_mcp.auth login
```

终端会显示 Device Code Flow 的登录地址和短期 user code。完成 Microsoft 365 登录后，MSAL cache 默认保存到：

```text
~/.cache/m365-mcp/msal_token_cache.json
```

在 Unix 系统上，程序新建的默认 cache 目录和文件分别设置为 `0700` 和 `0600`。如需改位置，可以设置 `M365_TOKEN_CACHE_PATH`。cache 包含敏感登录状态，不要提交、打印或发送给 LLM。

MCP tool 调用期间只会使用 `acquire_token_silent()`。如果 access token 过期而 refresh token 仍有效，MSAL 会静默刷新；如果没有有效登录状态，工具会提示重新运行上面的 login 命令，不会在 stdio 会话中启动 Device Code Flow。

## 5. 启动 stdio MCP Server

```bash
set -a
source .env
set +a
python -m m365_mcp.server
```

stdio 是 MCP protocol 通道，进程正常启动后不会显示普通交互提示。日志配置为 stderr，不会把 access token 或 debug 信息写入 stdout。

典型 MCP Client 配置如下，请把 Python 路径改成此项目虚拟环境的绝对路径：

```json
{
  "mcpServers": {
    "m365-mcp": {
      "command": "/absolute/path/to/vitos-work-assistant/.venv/bin/python",
      "args": ["-m", "m365_mcp.server"],
      "cwd": "/absolute/path/to/vitos-work-assistant/services/m365-mcp",
      "env": {
        "M365_TENANT_ID": "your-tenant-id",
        "M365_CLIENT_ID": "your-client-id"
      }
    }
  }
}
```

## 6. 使用 MCP Inspector 验证

启动 Inspector：

```bash
npx -y @modelcontextprotocol/inspector
```

在 Inspector 中选择 **STDIO**，设置：

- Command：仓库根目录 `.venv/bin/python` 的绝对路径
- Arguments：`-m m365_mcp.server`
- Working Directory：`services/m365-mcp` 的绝对路径
- Environment：`M365_TENANT_ID` 和 `M365_CLIENT_ID`

Inspector 不会因为设置了 Working Directory 就自动读取其中的 `.env`。必须在 Inspector 的 Environment 配置中明确填写这两个变量，或者从已经执行过 `source .env` 的终端启动 Inspector。Tenant ID 和 Client ID 不是 client secret，但仍不要把真实值提交到 Git。

先调用：

```text
search_sharepoint(query="VPN", top=5)
```

响应是精简后的列表，而不是完整 Graph JSON：

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

从候选结果中取得 `drive_id` 和 `item_id`，再调用：

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

没有搜索结果时返回空列表。PDF 会返回明确的“不支持”错误；401、403、404、429 会映射为不含 token 或 Authorization header 的开发者可读错误。

## Delegated authentication 与权限边界

这是 delegated authentication。Graph API 代表当前登录用户执行，Microsoft Search 使用该用户身份完成 security trimming：

```text
Alice token → Alice 有权访问的结果
Bob token   → Bob 有权访问的结果
```

`Files.Read.All` 是允许应用代表用户读取文件的 delegated permission，并不赋予用户读取整个 tenant 所有文件的权限。最终能搜索和下载哪些文件仍由 SharePoint / OneDrive ACL 决定；本服务不自行复制或绕过 SharePoint ACL。

## 运行单元测试

测试使用 mock Graph transport，不需要真实 Microsoft 365 账号：

```bash
python -m pytest
```

测试覆盖 Search response normalization、缺少 `parentReference.driveId` 的 hit、DOCX 正文提取，以及 Graph 401/403/404/429/其他错误映射。

## 实现边界

- Graph API 仅允许固定的 `POST /search/query`、driveItem metadata 和 content 下载调用。
- access token 完全封装在 `GraphClient` 内，不是 tool 参数或 tool 返回值。
- 支持 `.docx`、UTF-8 `.txt`、`.md`；暂不解析 PDF 和其他 Office 格式。
- 不暴露任意 method/URL 的 Graph proxy，不提供上传、修改或删除能力。
