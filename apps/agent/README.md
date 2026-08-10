# Vito's Work Assistant Agent API

这是一个最小、仅 API 的企业 Assistant Demo：FastAPI 调用 DeepAgent；DeepAgent
按需读取一个企业知识搜索 Skill，并通过持久的 Streamable HTTP MCP session 使用
`services/m365-mcp-http` 的 `search_sharepoint` 与 `read_document` 工具。

## 边界与身份模型

当前调用链是：

```text
API caller
  → Work Assistant API
  → DeepAgent
  → Streamable HTTP
  → services/m365-mcp-http
  → 当前本地 Device Code 登录用户
  → Microsoft Graph
```

这是刻意的 **single-user development mode**。所有 `/chat` 请求最终都使用同一个
本地 Microsoft 365 delegated identity；它不是多用户安全架构，也没有 API
authentication 或 OBO。未来可替换为 Work Assistant Entra Login → user token → OBO
→ Graph delegated token，但本版本不实现。

## 设计

- `config.py`：验证 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 和
  `M365_MCP_URL`。
- `llm.py`：构建一个可配置 `base_url` 的 LangChain `ChatOpenAI`。
  显式使用 Chat Completions，不依赖 OpenAI Responses API。
- `mcp.py`：连接独立运行的 M365 Streamable HTTP endpoint，在 FastAPI 生命周期
  内保持一个 MCP client session，并只加载两个既有工具。Agent 不再 spawn MCP
  subprocess。
- `agent.py`：用 `create_deep_agent`、`StateBackend` 与 `InMemorySaver` 构建 Agent。
  Skill 文件注入内存状态；Agent 不获得宿主文件系统访问权。默认 subagent / `task`
  和不需要的文件写入工具已关闭，本阶段只有一个 Agent。
- `app.py`：仅暴露 `GET /health` 和 `POST /chat`。
- `skills/enterprise-knowledge-search/SKILL.md`：按需加载的检索方法论。

`sources` 只从当前轮真实 `search_sharepoint` / `read_document` 工具消息的结构化
字段提取，不从模型文本猜测 URL。若读取过文档，优先只返回实际读取文档的来源。

## 安装

需要 Python 3.11 或更新版本。以下命令从仓库根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e "./services/m365-mcp-http[dev]"
python -m pip install -e "./apps/agent[dev]"
```

## 1. 登录并启动 m365-mcp-http

使用 M365 服务 README 中的真实流程。先配置它自己的环境文件并登录：

```bash
cd services/m365-mcp-http
cp .env.example .env
# 编辑 .env，填写 M365_TENANT_ID 和 M365_CLIENT_ID
set -a
source .env
set +a
python -m m365_mcp.auth login
cd ../..
```

Token cache 默认位于 `~/.cache/m365-mcp/msal_token_cache.json`。API 不会启动
Device Code Flow；无有效 session 时，`/chat` 返回清晰错误并要求重新登录。

登录成功后，在**终端 A**启动 M365 MCP Server 并保持运行：

```bash
cd services/m365-mcp-http
set -a
source .env
set +a
python -m m365_mcp.server
```

默认 endpoint 是 `http://127.0.0.1:8001/mcp`。当前 endpoint 没有 MCP HTTP
authentication，只应用于本机受信任的 single-user 开发环境。

## 2. 配置 Agent

```bash
cp apps/agent/.env.example apps/agent/.env
# 编辑 apps/agent/.env，填写 LLM 配置和 MCP endpoint，不要提交真实 key
set -a
source apps/agent/.env
set +a
```

最小 LLM 配置：

```dotenv
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=...
LLM_MODEL=...
M365_MCP_URL=http://127.0.0.1:8001/mcp
```

理论上可仅修改这三个值切换 OpenAI、DeepSeek、OpenRouter 或其他兼容服务，
但 endpoint 必须兼容 OpenAI Chat Completions，且所选模型必须支持 Agent 所需的
Tool / Function Calling。项目不承诺所有标称“OpenAI-compatible”的服务都兼容。

当 `LLM_MODEL` 是 GPT-5.6 系列时，代码会自动为 Chat Completions 设置
`reasoning_effort="none"`。这是 GPT-5.6 在 Chat Completions 上同时使用 function
tools 的兼容要求；本 Demo 不会因此切换到 Responses API。

`M365_TENANT_ID`、`M365_CLIENT_ID` 和 token cache 配置只属于独立运行的 M365
Server；Agent 不再读取或转发它们。若 Server 使用其他 host、port 或 path，只需修改
`M365_MCP_URL`。

## 3. 启动 API

保持终端 A 中的 M365 MCP Server 运行。在**终端 B**从仓库根目录加载 Agent 配置并
启动 API：

```bash
set -a
source apps/agent/.env
set +a
uvicorn work_assistant.app:app --reload
```

`GET /health` 是刻意保持简单的进程健康检查，即使启动配置有误也返回
`{"status":"ok"}`；若 `M365_MCP_URL` 无法连接，`/chat` 会返回经过脱敏的
`503 mcp_unavailable`。

## 4. 手工验证

健康检查：

```bash
curl http://localhost:8000/health
```

企业知识问题（应按需 search，必要时 read）：

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"demo-001","message":"How to connect to corporate VPN？"}'
```

通用问题（不应机械搜索 SharePoint）：

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Python 中 list 和 tuple 有什么区别？"}'
```

不传 `thread_id` 时 API 自动生成 UUID。相同 `thread_id` 的历史仅保存在当前进程
内存中，API 重启后丢失。

## 测试

单元测试全部 mock Agent / 外部调用，不登录 Microsoft 365，也不调用真实 LLM：

```bash
python -m pytest apps/agent/tests
```

测试覆盖 health、请求验证、Agent mock、MCP 连接错误到 API 错误的映射、LLM/MCP
配置验证，以及 sources 归一化与响应整形。`test_mcp.py` 会启动本地 stateless
Streamable HTTP MCP Server，验证 Agent 的连接、工具发现和真实 tool call，不访问
Microsoft Graph。
