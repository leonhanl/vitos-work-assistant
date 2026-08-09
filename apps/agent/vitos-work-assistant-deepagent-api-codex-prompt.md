# 任务：为 Vito's Work Assistant 实现最小版 DeepAgent API

我正在开发一个企业 AI Assistant Demo，名称为 **Vito's Work Assistant**。

目前已经完成一个最小版 `m365-mcp`，它通过 Microsoft Graph 提供 Microsoft 365 / SharePoint 能力。

现在进入下一阶段：

> 实现一个 **DeepAgent + MCP + Skill + HTTP API** 的最小版本。

这一阶段 **暂时不要开发前端**，只提供 API。

---

# 1. 当前仓库结构与既有边界

当前仓库是 monorepo，核心结构类似：

```text
vitos-work-assistant/
├── apps/
│   ├── web/                  # 暂不开发
│   └── agent/                # 本阶段主要开发目标
├── services/
│   ├── m365-mcp/             # 已完成，当前阶段禁止修改
│   └── salesforce-mcp/       # 暂不开发
├── libs/
│   └── common/               # 暂不提前扩展
├── infra/                    # 暂不开发
├── docker-compose.yml
├── .env.example
└── README.md
```

当前已经完成：

```text
services/m365-mcp
```

它至少暴露两个 MCP Tools：

```text
search_sharepoint
read_document
```

---

# 2. 重要约束：禁止修改 services/m365-mcp

`services/m365-mcp/**` 视为已经完成并可工作的稳定模块。

本阶段：

```text
禁止修改 services/m365-mcp 目录中的任何文件。
```

Agent 只能通过现有 MCP tool contract 使用：

```text
search_sharepoint
read_document
```

如果在集成过程中发现问题：

1. 优先修改 `apps/agent`；
2. 不要为了方便而改动 `services/m365-mcp`；
3. 如果确认必须修改 `services/m365-mcp` 才能继续：
   - 停止；
   - 明确说明原因；
   - 给出建议修改点；
   - 等待我确认；
   - 不要自行修改。

不要复制一份新的 `m365-mcp`。

不要在 Agent 中重新实现 Microsoft Graph Search、认证或文档解析逻辑。

---

# 3. 当前 m365-mcp 能力

## search_sharepoint

用于搜索当前登录用户有权限访问的 Microsoft 365 / SharePoint 文档。

底层已经由 `m365-mcp` 实现 Microsoft Graph Search。

典型返回：

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

## read_document

用于读取某个搜索结果的完整正文：

```text
read_document(
    drive_id,
    item_id
)
```

典型返回：

```json
{
  "name": "KB003 - How to Connect to Corporate VPN.docx",
  "web_url": "https://...",
  "content": "完整文档正文..."
}
```

---

# 4. 当前 m365-mcp 的认证模式

目前 `m365-mcp` 使用：

```text
Delegated Permission
+
Device Code Flow
+
本地 MSAL Token Cache
```

当前属于：

```text
single-user development mode
```

假设已经事先执行现有项目提供的登录命令，例如：

```bash
python -m m365_mcp.auth login
```

并完成 Microsoft 365 登录。

请先检查现有 `services/m365-mcp` 的 README 和真实启动方式，不要假设路径或命令。

当前阶段：

```text
不要修改 m365-mcp 的认证方式。
不要实现 OBO。
不要实现 multi-user identity。
```

---

# 5. 本阶段目标架构

```text
curl / Postman / API Client
           │
           ▼
    Work Assistant API
           │
           ▼
       DeepAgent
       │       │
       │       └── Skill
       │
       ▼
      MCP Client
           │
           ▼
 services/m365-mcp
           │
           ▼
   Microsoft Graph
           │
           ▼
 SharePoint / M365 Search
```

本阶段要验证：

```text
用户自然语言问题
→ DeepAgent 理解问题
→ 按需使用 Skill
→ 判断是否需要企业知识
→ 决定是否调用 search_sharepoint
→ 自动生成适合 Microsoft Search 的查询词
→ 根据结果决定是否调用 read_document
→ 根据企业文档回答
→ 返回真实引用来源
```

---

# 6. 技术要求

使用：

- Python 3.11+
- Deep Agents / LangChain 当前官方推荐实现
- 官方 MCP Python SDK 或 Deep Agents 官方 MCP integration
- FastAPI
- Pydantic
- pytest
- httpx（用于 API 测试）

如果 Deep Agents、LangChain 或 MCP SDK 的 API 已变化：

> 请优先查询官方最新文档，并使用当前推荐 API。

不要因为本 Prompt 中可能存在旧写法而使用 deprecated API。

不要猜测 SDK 行为。

---

# 7. LLM 兼容性要求

LLM 层只要求兼容：

```text
OpenAI-compatible Chat Completions API
+
Tool / Function Calling
```

目标是让同一套 Agent 代码通过配置切换不同的 OpenAI-compatible 推理服务，例如：

```text
OpenAI
DeepSeek
OpenRouter
其他兼容 OpenAI Chat Completions + Tool Calling 的服务
```

不要把 Agent 写死绑定到某一家 provider。

---

# 8. LLM 配置方式

使用统一环境变量：

```text
LLM_BASE_URL
LLM_API_KEY
LLM_MODEL
```

例如：

```env
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=...
LLM_MODEL=...
```

切换 provider 时，应主要通过修改：

```text
LLM_BASE_URL
LLM_API_KEY
LLM_MODEL
```

完成，而不是修改 Agent 代码。

请提供 `.env.example`，但不要放任何真实 key。

---

# 9. LLM Client 实现原则

优先使用 LangChain 当前推荐的 OpenAI-compatible chat model 实现。

例如，如果当前版本适用，可以使用类似：

```python
ChatOpenAI(
    model=settings.llm_model,
    api_key=settings.llm_api_key,
    base_url=settings.llm_base_url,
)
```

但请先查阅当前官方文档，确认真实参数和推荐写法。

不要自行实现一套 OpenAI HTTP Client。

不要在第一版增加复杂的 provider abstraction framework。

第一版只需要：

```text
OpenAI-compatible endpoint
+
configurable base_url
+
configurable model
+
tool calling
```

---

# 10. Provider 兼容边界

不要假设所有 OpenAI-compatible provider 都支持所有 OpenAI 专属能力。

本阶段尽量只依赖共同子集：

```text
messages
system prompt
普通 chat response
tool / function calling
必要的基础 generation 参数
```

当前不要强依赖：

```text
OpenAI Responses API 专属能力
provider-specific reasoning 参数
provider-specific routing 参数
provider-specific structured output 扩展
专有 tracing metadata
专有 cache API
```

如果某个 provider 不支持 Agent 所需的 Tool Calling：

应给出清晰配置/兼容性错误。

---

# 11. 开发原则

这是一个学习型 Demo。

请遵循：

```text
代码尽量少
模块边界清楚
容易阅读
容易调试
不要过度工程化
不要提前实现未来功能
```

当前重点是理解：

```text
DeepAgent
→ Skill
→ MCP Tool
→ Microsoft Graph
→ Grounded Answer
```

---

# 12. DeepAgent

请创建一个 DeepAgent。

Agent 的职责：

```text
理解用户问题
判断是否需要企业内部知识
必要时调用 MCP tools
根据搜索结果继续决定下一步
基于真实文档内容回答
```

不要写死固定 workflow：

```text
每次请求都必须：
search
→ read
→ answer
```

应该让 DeepAgent 自己判断：

```text
是否需要搜索
搜索什么
是否需要重新搜索
读取哪篇文档
是否需要读取多篇文档
什么时候已经有足够信息回答
```

---

# 13. System Prompt

System Prompt 保持简洁。

不要把所有企业知识检索方法塞进 System Prompt。

可以包含类似原则：

```text
你是 Vito's Work Assistant。

当用户的问题涉及公司内部知识、IT KB、公司政策、操作手册、
内部流程或 Microsoft 365 中的企业文档时，
优先使用可用的企业知识检索能力获取事实依据。

不要编造企业内部事实。

如果现有企业资料无法支持答案，应明确说明信息不足。
```

具体检索方法放到 Skill。

---

# 14. MCP Integration

DeepAgent 需要连接已经存在的：

```text
services/m365-mcp
```

当前 MCP transport 使用：

```text
stdio
```

不要改成：

```text
Streamable HTTP
SSE
Remote MCP
```

请先检查：

```text
services/m365-mcp/README.md
pyproject.toml
实际 module entry point
实际 tool schema
```

并使用现有真实启动方式。

---

# 15. MCP Tool 使用原则

DeepAgent 应该看到现有 MCP Server 暴露的：

```text
search_sharepoint
read_document
```

不要在 `apps/agent` 中重新实现 Microsoft Graph API。

不要增加：

```text
graph_request(method, url, body)
```

这样的万能 Tool。

Graph 细节继续封装在：

```text
services/m365-mcp
```

内部。

---

# 16. Skill

这一阶段只实现 **一个 Skill**。

建议名称：

```text
enterprise-knowledge-search
```

目录例如：

```text
apps/agent/
└── skills/
    └── enterprise-knowledge-search/
        └── SKILL.md
```

如果 Deep Agents 当前官方 Skills 目录规范不同，请使用官方最新推荐结构。

---

# 17. Skill 的职责

Skill 不应该讲 Microsoft Graph REST API 细节。

不要在 Skill 中写：

```text
POST /v1.0/search/query
GET /drives/{id}/items/{id}/content
```

这些属于 MCP Server 实现细节。

Skill 应告诉 Agent：

> 如何高质量地使用企业知识搜索能力。

至少包括以下规则。

## 17.1 什么时候使用企业知识搜索

当问题涉及：

```text
IT KB
公司政策
内部流程
员工操作指南
公司内部产品资料
内部 FAQ
SharePoint 文档
Microsoft 365 中的企业知识
```

通常应该先检索企业知识。

如果只是：

```text
普通常识
数学
通用编程知识
与企业内部无关的问题
```

则不需要强制调用 SharePoint Search。

## 17.2 Query Rewriting

当前 `search_sharepoint` 底层主要依赖 Microsoft Search 的关键词 / lexical retrieval。

因此不要总是把用户完整自然语言问题原样作为 query。

例如：

```text
用户：
我出差的时候怎么访问公司内部系统？
```

可以改写成：

```text
VPN remote access travel
corporate VPN
remote access
```

Skill 应指导 Agent 提取：

```text
核心名词
产品名
系统名
错误信息
操作动作
可能的同义词
```

并生成简洁搜索词。

## 17.3 搜索失败时

如果第一次搜索没有合适结果：

不要立即放弃。

可以重新生成另一组关键词，例如：

```text
VPN
→ corporate remote access
→ secure tunnel
```

但不要无限搜索。

第一版只允许少量合理重试，例如最多 2~3 次。

如果已有高质量结果可以提前停止。

## 17.4 如何使用 summary

`search_sharepoint` 返回：

```text
name
summary
rank
web_url
drive_id
item_id
```

Agent 应先根据：

```text
文档标题
summary
rank
```

判断哪些文档最值得读取。

不要把所有搜索结果全部调用 `read_document`。

优先读取最相关的少数候选。

## 17.5 什么时候 read_document

当 Search Result 的 summary 不足以可靠回答问题时：

调用：

```text
read_document
```

对于：

```text
操作步骤
政策细节
具体配置
限制条件
版本要求
```

通常应该读取完整文档，而不是只依赖 summary。

## 17.6 Grounding

最终答案尽可能基于实际检索到的企业资料。

不得用模型常识补充未经文档支持的企业内部事实。

如果：

```text
没有找到资料
资料与问题不一致
资料明显不足
```

应该明确告诉用户：

```text
目前检索到的企业资料不足以确认这个问题。
```

## 17.7 Sources

回答企业内部知识问题时，尽可能附带真实来源：

```text
文档名称
web_url
```

不要伪造来源。

---

# 18. HTTP API

当前阶段只提供 API。

不要开发：

```text
React
Next.js
Vue
HTML 页面
Chat UI
```

使用 FastAPI。

第一版只需要：

```text
GET /health
POST /chat
```

---

# 19. GET /health

```http
GET /health
```

返回：

```json
{
  "status": "ok"
}
```

不要为了 health check 增加复杂依赖检查。

---

# 20. POST /chat

Request：

```json
{
  "message": "出差的时候怎么连接公司内部网络？"
}
```

允许可选：

```json
{
  "thread_id": "demo-001",
  "message": "出差的时候怎么连接公司内部网络？"
}
```

如果没有 `thread_id`，可以自动生成 UUID。

---

# 21. POST /chat Response

建议返回：

```json
{
  "thread_id": "demo-001",
  "answer": "根据公司的 IT KB，出差时可以通过 Corporate VPN ...",
  "sources": [
    {
      "name": "KB003 - How to Connect to Corporate VPN.docx",
      "url": "https://..."
    }
  ]
}
```

`answer` 必须来自 Agent 最终回答。

`sources` 应尽可能从实际 MCP Tool 返回结果或 Agent execution trace 中提取。

如果当前 Deep Agents API 很难可靠提取结构化 sources：

使用简单、清晰、可维护的方法。

但：

- 不要伪造 source；
- 不要通过正则随便猜 URL；
- 不要为了 sources 引入大型 tracing framework。

---

# 22. Conversation / thread

第一版只需要最简单的 conversation support。

如果 Deep Agents 当前推荐使用：

```text
checkpointer
thread_id
```

则可以使用。

优先使用：

```text
in-memory
```

不要引入：

```text
PostgreSQL
Redis
MongoDB
DynamoDB
```

API 重启后 conversation 丢失可以接受。

---

# 23. 建议项目结构

请先检查当前 repository。

优先在现有：

```text
apps/agent
```

中实现。

可以采用类似：

```text
apps/agent/
├── pyproject.toml
├── README.md
├── .env.example
├── src/
│   └── work_assistant/
│       ├── __init__.py
│       ├── app.py
│       ├── agent.py
│       ├── mcp.py
│       ├── llm.py
│       ├── config.py
│       └── models.py
└── skills/
    └── enterprise-knowledge-search/
        └── SKILL.md
```

如果现有 repository 已经有更合理结构：

优先保持现有风格。

不要为了符合示例强行重构整个 repo。

---

# 24. 模块职责

## app.py

负责：

```text
FastAPI
/health
/chat
```

不要放 Agent 业务细节。

## agent.py

负责：

```text
创建 DeepAgent
加载 MCP tools
配置 Skill
配置 system prompt
执行 Agent
```

## mcp.py

负责：

```text
stdio MCP client configuration
连接 services/m365-mcp
加载 MCP tools
```

## llm.py

负责：

```text
根据：
LLM_BASE_URL
LLM_API_KEY
LLM_MODEL

创建 OpenAI-compatible Chat Model
```

不要在这里写 provider-specific 大量分支。

## config.py

负责：

```text
读取环境变量
基础配置验证
```

## models.py

只放必要的：

```text
ChatRequest
ChatResponse
Source
```

不要建立大型 domain model。

---

# 25. MCP Server 生命周期

请正确处理 stdio MCP Server 生命周期。

避免：

```text
每一次 /chat
→ 启动一个新的 m365-mcp
→ 请求结束立即关闭
```

如果官方推荐模式允许：

尽量在 Work Assistant API 生命周期内维护 MCP connection / client，例如：

```text
FastAPI startup
→ 建立 MCP connection

FastAPI shutdown
→ 正确关闭 MCP connection
```

必须遵循当前 Deep Agents / MCP SDK 官方推荐方式。

不要自行发明脆弱的 subprocess management。

---

# 26. Error Handling

至少清晰处理：

```text
m365-mcp 无法启动
MCP connection 失败
Microsoft 365 没有有效登录 session
Graph 401
Graph 403
Graph 429
search 没有结果
read_document 失败
LLM endpoint 不可达
LLM authentication 失败
LLM model 不存在
LLM provider 不支持所需 Tool Calling
```

API 返回适当 HTTP status 和清晰 error message。

不要暴露：

```text
Graph access token
refresh token
Authorization header
LLM_API_KEY
环境变量内容
完整内部 stack trace
```

---

# 27. Logging

第一版使用 Python 标准 logging。

至少能看出：

```text
API request 开始
Agent execution 开始
MCP connection error
LLM 调用失败
request 成功 / 失败
```

不要打印：

```text
Graph token
LLM_API_KEY
完整 Authorization Header
```

不要让日志破坏 MCP stdio protocol。

---

# 28. Tests

不要追求高 coverage。

至少包括：

```text
GET /health
POST /chat request validation
Agent service 可以被 mock
MCP integration error → API error
LLM configuration validation
sources normalization / response shaping
```

unit tests 不要真的：

```text
登录 Microsoft 365
调用 Graph
调用真实 LLM API
```

都应该 mock。

---

# 29. Manual Integration Test

README 中提供最简单完整验证流程。

## Step 1

确保现有 `m365-mcp` 已完成登录。

使用 `services/m365-mcp` 当前 README 中的真实命令。

## Step 2

配置 LLM：

```env
LLM_BASE_URL=...
LLM_API_KEY=...
LLM_MODEL=...
```

## Step 3

启动 Work Assistant API，例如：

```bash
uvicorn work_assistant.app:app --reload
```

如果真实 module path 不同，使用实际命令。

## Step 4

检查：

```bash
curl http://localhost:8000/health
```

## Step 5

测试企业知识问题：

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "出差的时候怎么连接公司内部网络？"
  }'
```

预期 DeepAgent 能自主调用：

```text
search_sharepoint
```

必要时调用：

```text
read_document
```

并根据真实 KB 回答。

---

# 30. 第二个测试场景

README 再提供一个不需要企业搜索的问题：

```json
{
  "message": "Python 中 list 和 tuple 有什么区别？"
}
```

用于验证：

> Agent 不应该为了所有问题都强制调用 SharePoint Search。

---

# 31. OpenAI-compatible Provider 测试说明

README 中说明：

理论上可以通过修改：

```text
LLM_BASE_URL
LLM_API_KEY
LLM_MODEL
```

切换到不同 OpenAI-compatible provider。

但必须满足：

```text
Chat Completions compatible
+
支持 Agent 所需的 Tool / Function Calling
```

不要承诺所有号称 “OpenAI-compatible” 的服务都能完整支持 DeepAgent。

---

# 32. 当前明确不要实现的内容

以下全部不属于当前任务：

```text
Frontend
React
Next.js
Web UI

修改 services/m365-mcp

Entra ID 登录 Work Assistant
API authentication
OBO
multi-user identity
Alice/Bob per-user Graph token

Streamable HTTP MCP
Remote MCP
MCP OAuth

Salesforce
Email
Outlook
Teams

Write operations
Create Case
HITL

Vector DB
Embedding
Reranker
Azure AI Search

复杂 LangGraph 自定义 workflow
Supervisor multi-agent
多个 Agent

Persistent database
PostgreSQL
Redis

复杂 Provider Adapter Framework
Provider-specific Advanced Features

Production deployment
Kubernetes
Terraform
CI/CD
```

不要提前实现。

---

# 33. 当前阶段的认证边界

请在 README 中明确说明：

```text
API caller
   ↓
Work Assistant API
   ↓
DeepAgent
   ↓
services/m365-mcp
   ↓
当前本地 Device Code 登录用户
   ↓
Microsoft Graph
```

因此：

> 当前所有 `/chat` 请求最终都使用同一个本地 Microsoft 365 delegated identity。

这是刻意的：

```text
single-user development mode
```

当前阶段可以接受。

不要描述成 multi-user secure architecture。

---

# 34. 未来架构说明

README 可以简短写 TODO：

```text
Device Code Flow
```

未来替换为：

```text
Work Assistant Entra Login
→ user token
→ OBO
→ Graph delegated token
```

未来目标：

```text
Alice
→ Work Assistant
→ OBO
→ Graph as Alice

Bob
→ Work Assistant
→ OBO
→ Graph as Bob
```

但这一阶段不要实现。

---

# 35. 验收标准

完成后，我应该能够：

1. `services/m365-mcp` 保持完全不变；
2. 在 `apps/agent` 中完成 DeepAgent API；
3. 通过配置连接一个支持 Tool Calling 的 OpenAI-compatible LLM；
4. 启动 Work Assistant API；
5. 调用：

```http
POST /chat
```

输入：

```json
{
  "message": "我出差时应该怎么访问公司的内部系统？"
}
```

Agent 能够：

```text
理解这是企业内部 IT 问题
→ 使用 enterprise-knowledge-search Skill
→ 生成适合检索的 query
→ 调用 search_sharepoint
→ 找到相关 KB
→ 必要时调用 read_document
→ 根据正文回答
→ 返回真实 source
```

同时输入：

```json
{
  "message": "Python 中 dict 和 list 有什么区别？"
}
```

Agent 不应该机械搜索 SharePoint。

---

# 36. 开发方式

请按下面顺序：

1. 检查当前 repository；
2. 阅读根目录 README；
3. 阅读 `services/m365-mcp/README.md`；
4. 确认现有 MCP Server 的真实启动命令和 Tool schema；
5. 不修改 `services/m365-mcp/**`；
6. 查阅 Deep Agents、LangChain、MCP integration 当前官方文档；
7. 给出简短 implementation plan；
8. 说明准备创建 / 修改哪些 `apps/agent` 文件；
9. 开始实现；
10. 每完成关键阶段运行测试；
11. 最后给出启动和验证命令。

如果现有代码与本 Prompt 中假设不同：

> 优先适配已经工作的现有实现，而不是重写它。

---

# 37. 最重要的职责边界

始终保持：

```text
DeepAgent
= reasoning / planning / deciding what to do

Skill
= 如何高质量完成企业知识检索任务的方法论

MCP Tool
= 可执行的企业能力

services/m365-mcp
= 已完成的 Microsoft Graph integration / authentication / document handling
  本阶段 READ ONLY

Microsoft Graph
= M365 数据与 SharePoint ACL

OpenAI-compatible LLM
= 推理与 Tool Calling
  通过 base_url / api_key / model 解耦 provider
```

不要把这些层重新揉在一起。

---

# 38. 最终停止点

当下面链路成功后：

```text
POST /chat
→ DeepAgent
→ enterprise-knowledge-search Skill
→ services/m365-mcp
→ search_sharepoint
→ read_document
→ grounded answer + sources
```

并且能够通过配置切换 OpenAI-compatible LLM provider 后，就停止。

不要继续开发：

```text
OBO
Frontend
Salesforce
更多 Skills
更多 MCP Servers
Production deployment
```

请现在开始。
