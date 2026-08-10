> 历史参考：此文件记录最初 stdio 版本的实现任务，已被当前 Streamable HTTP
> 架构取代。当前运行方式与配置以本目录 `README.md` 为准。

# 任务：实现一个最小版 Microsoft 365 MCP Server

我正在开发一个企业 AI Assistant Demo，名称暂时为 **Vito's Work Assistant**。

现在不要实现完整系统，只实现一个最小的 Microsoft 365 MCP Server，用来学习和验证：

Microsoft Graph Search → 找到 SharePoint 文档 → 下载文档 → 提取正文 → 通过 MCP Tool 暴露。

请使用 **Python** 实现，并尽量保持代码简单、清晰、容易理解。

---

# 1. 当前阶段的目标

最终希望得到这样一条链路：

```text
MCP Client / MCP Inspector
        │
        ▼
     m365-mcp
        │
        ├── search_sharepoint
        │
        └── read_document
        │
        ▼
 Microsoft Graph API
        │
        ▼
 SharePoint / Microsoft Search
```

当前阶段只需要实现两个 MCP tools：

```text
search_sharepoint
read_document
```

暂时不要实现 DeepAgent。

---

# 2. 技术要求

使用：

- Python 3.11+
- 官方 Python MCP SDK
- Microsoft Authentication Library (MSAL)
- httpx
- python-docx
- pytest（只需要少量关键单元测试）

如果 MCP Python SDK 的 API 在当前版本已经发生变化，请先查阅 **官方 MCP Python SDK 最新文档**，使用当前推荐方式实现。

不要因为我的 Prompt 中可能存在旧写法而使用已经 deprecated 的 API。

Microsoft Graph 相关接口也优先参考 Microsoft 官方文档。

---

# 3. Authentication

当前版本使用：

```text
Delegated Permission
+
Device Code Flow
```

不要使用：

```text
Application Permission
Client Credentials
Client Secret
OBO
```

需要用户自己登录 Microsoft 365。

Graph permissions 至少考虑：

```text
User.Read
Files.Read.All
```

我们已经知道：

```text
Files.Read.All
```

是 Delegated Permission，并已经在 Tenant 中完成 Admin Consent。

---

# 4. Entra App Registration

不要通过代码自动创建 Entra App Registration。

README 中告诉我需要手工创建一个 Entra App Registration，并配置：

```text
Delegated permissions:
- User.Read
- Files.Read.All

Allow public client flows:
- Yes
```

程序通过环境变量读取：

```text
M365_TENANT_ID
M365_CLIENT_ID
```

不要需要 client secret。

提供：

```text
.env.example
```

但不要把真实 tenant ID、client ID 或 token 写进代码。

---

# 5. Token 获取方式

为了避免 MCP stdio transport 和 authentication UI 混在一起，Authentication 和 MCP Server 分开。

希望提供类似：

```bash
python -m m365_mcp.auth login
```

第一次运行时：

```text
Device Code Flow
→ 浏览器登录 Microsoft 365
→ 获取 delegated token
→ 保存 MSAL token cache
```

然后启动 MCP Server：

```bash
python -m m365_mcp.server
```

MCP Server 启动后应该：

```text
MSAL token cache
→ acquire_token_silent()
→ 获取当前登录用户 Graph token
```

如果 token 过期但 refresh token 仍然有效，MSAL 应该自动刷新。

如果完全没有有效登录状态，则返回清晰错误，例如：

```text
No valid Microsoft 365 login session.
Run:

python -m m365_mcp.auth login
```

不要在 MCP tool 调用过程中启动 Device Code Flow。

---

# 6. Token 安全要求

Graph access token：

- 不允许作为 MCP tool 参数
- 不允许返回给 LLM
- 不允许打印到 stdout
- 不允许写入普通 log
- 不允许出现在异常信息中

MCP Tool 应该是：

```python
search_sharepoint(query: str, top: int = 5)
```

而不是：

```python
search_sharepoint(
    query: str,
    access_token: str
)
```

Authentication 应完全封装在 MCP Server 内部。

---

# 7. MCP transport

第一阶段使用：

```text
stdio
```

不要实现：

```text
Streamable HTTP
SSE
FastAPI
OAuth for MCP
remote MCP server
```

以后再做。

特别注意：

MCP stdio 模式下不要把 debug/logging 信息写到 stdout，以免破坏 MCP protocol。

如果需要日志：

```text
写 stderr
```

或者使用标准 logging 正确配置。

---

# 8. Tool 1：search_sharepoint

实现：

```python
search_sharepoint(
    query: str,
    top: int = 5
)
```

内部调用：

```http
POST https://graph.microsoft.com/v1.0/search/query
```

Request body 类似：

```json
{
  "requests": [
    {
      "entityTypes": [
        "driveItem"
      ],
      "query": {
        "queryString": "VPN"
      },
      "from": 0,
      "size": 5
    }
  ]
}
```

不要自己建立：

```text
Vector DB
Embedding
FAISS
Chroma
Azure AI Search
```

这里使用 Microsoft 365 已经存在的 Microsoft Search Index。

---

# 9. search_sharepoint 返回结果

不要把完整 Graph JSON 原样返回给 Agent。

把 Graph Response normalize 成简单的数据结构。

例如：

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

重点从：

```text
value[]
→ hitsContainers[]
→ hits[]
→ resource
```

提取：

```text
rank
summary
resource.name
resource.webUrl
resource.id
resource.parentReference.driveId
```

其中：

```text
item_id = resource.id
drive_id = resource.parentReference.driveId
```

如果某个字段不存在，要安全处理，不要因为一个 search hit 字段缺失导致整个请求失败。

---

# 10. Search Result 的安全模型

不要在应用层自己实现 SharePoint ACL。

Microsoft Graph Search 使用当前用户的 delegated identity。

因此：

```text
Alice token
→ Alice 可以访问的搜索结果

Bob token
→ Bob 可以访问的搜索结果
```

当前 MCP Server 不做额外 ACL filtering。

依赖：

```text
Microsoft Graph
+
SharePoint ACL
+
Microsoft Search security trimming
```

---

# 11. Tool 2：read_document

实现：

```python
read_document(
    drive_id: str,
    item_id: str
)
```

内部调用：

```http
GET https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/content
```

下载文件内容。

第一版重点支持：

```text
.docx
.txt
.md
```

其中：

```text
.docx → python-docx
.txt  → decode text
.md   → decode text
```

PDF 暂时不要求实现。

如果遇到 PDF，可以返回清晰错误：

```text
PDF parsing is not supported in the current version.
```

不要为了支持所有 Microsoft 365 文件格式把项目复杂化。

---

# 12. read_document 返回格式

例如：

```json
{
  "name": "KB003 - How to Connect to Corporate VPN.docx",
  "web_url": "https://...",
  "content": "完整提取出来的正文..."
}
```

如果需要，可以先：

```http
GET /drives/{drive-id}/items/{item-id}
```

取得文件 metadata，再调用：

```http
GET /content
```

但是如果 Search Result 已经提供足够 metadata，也不要增加不必要的 Graph API 调用。

---

# 13. Tool Description

MCP Tool description 要写清楚，让未来的 Agent 能理解什么时候使用。

例如：

### search_sharepoint

用于搜索用户有权限访问的 Microsoft 365 / SharePoint 企业文档。

适用于：

```text
IT KB
公司政策
操作手册
内部文档
产品文档
```

输入应该尽量是简洁的搜索关键词，而不是很长的自然语言问题。

### read_document

读取 search_sharepoint 返回的某一篇文档的完整正文。

通常先使用：

```text
search_sharepoint
```

找到候选文档，再调用：

```text
read_document
```

---

# 14. Graph Client

请单独封装：

```python
class GraphClient:
```

至少提供：

```python
search_files(...)
get_drive_item(...)
download_drive_item(...)
```

不要把所有 Microsoft Graph HTTP request 都直接写在 MCP Tool function 中。

但是也不要设计复杂的 abstract class / dependency injection framework。

保持简单。

---

# 15. 建议项目结构

保持项目很小，例如：

```text
m365-mcp/
│
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
│
└── src/
    └── m365_mcp/
        ├── __init__.py
        ├── server.py
        ├── auth.py
        ├── graph.py
        └── document_parser.py
```

如果你认为有更简单、合理的目录结构，也可以调整。

不要过度工程化。

---

# 16. Error Handling

至少清晰处理：

```text
401 → authentication/token 问题

403 → Graph permission / SharePoint permission 问题

404 → document 不存在

429 → Graph throttling

Graph Search 没结果

不支持的文件类型
```

错误信息应该适合开发者调试，但不要泄露：

```text
access token
refresh token
Authorization header
client secret
```

---

# 17. Tests

只写少量真正有价值的测试。

不要追求高 coverage。

至少测试：

```text
Graph Search response parsing

正常 driveItem → normalized result

缺少 parentReference.driveId 的 search hit

DOCX text extraction

Graph API error mapping
```

Graph API 测试使用 mock。

不要要求真实 Microsoft 365 account 才能运行 unit tests。

---

# 18. README

README 要非常清楚地写出完整操作步骤：

```text
1. 创建 Entra App Registration

2. 配置 delegated permission

   User.Read
   Files.Read.All

3. Enable public client flow

4. 配置：

   M365_TENANT_ID
   M365_CLIENT_ID

5. 安装：

   pip install ...

6. 登录：

   python -m m365_mcp.auth login

7. 启动 MCP server

8. 使用 MCP Inspector 测试：

   search_sharepoint("VPN")

9. 从结果中取得：

   drive_id
   item_id

10. 调用：

    read_document(drive_id, item_id)
```

README 还需要解释：

```text
这是 delegated authentication。

Graph API 是代表当前登录用户执行。

Files.Read.All 并不意味着用户能够读取整个 tenant 的所有文件。

最终能够访问哪些文件仍然受到 SharePoint / OneDrive ACL 限制。
```

---

# 19. 当前明确不要实现的内容

以下内容全部不属于当前任务：

```text
DeepAgent
LangGraph
LLM
OpenAI API
RAG generation
Vector Database
Embedding
Reranker

OBO
multi-user authentication
Web frontend
FastAPI
Streamable HTTP MCP

Email
Outlook
Teams
Salesforce

Write operations
upload document
delete document
modify SharePoint

Application Permission
client credentials
client secret
```

如果发现这些需求，不要提前实现。

---

# 20. 一个重要设计原则

不要实现这样的 MCP Tool：

```python
graph_request(
    method: str,
    url: str,
    body: dict
)
```

不允许让未来的 LLM 任意调用 Microsoft Graph endpoint。

我们希望 MCP Server 暴露的是 narrow capabilities：

```text
search_sharepoint
read_document
```

而不是一个万能 Microsoft Graph proxy。

---

# 21. 第一阶段验收标准

完成以后，我应该能够做到：

```text
python -m m365_mcp.auth login
```

使用 Alice 的 Microsoft 365 账号登录。

然后启动 MCP Server。

通过 MCP Inspector 调用：

```text
search_sharepoint("VPN")
```

能够找到类似：

```text
KB003 - How to Connect to Corporate VPN.docx
```

并得到：

```text
drive_id
item_id
name
summary
web_url
```

然后调用：

```text
read_document(
    drive_id="...",
    item_id="..."
)
```

能够返回这篇 DOCX 的正文。

做到这里就停止。

不要继续实现 DeepAgent 或 OBO。

---

# 22. 开发方式

请不要一次性生成大量复杂代码。

先：

1. 检查当前 repository。
2. 给出一个简短 implementation plan。
3. 说明准备创建/修改哪些文件。
4. 然后开始实现。
5. 每完成一个关键阶段，运行对应测试。
6. 最后给出运行命令和测试方法。

如果遇到 Microsoft Graph / MCP SDK API 不确定的问题，请查询官方最新文档，不要猜测。

代码目标不是“功能越多越好”，而是：

```text
代码尽量少
边界清楚
容易阅读
容易调试
能够让我理解：

MCP
→ delegated authentication
→ Microsoft Graph Search
→ driveItem
→ document content
```

请现在开始实现这个最小版本。
