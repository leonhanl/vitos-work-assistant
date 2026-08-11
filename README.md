# Vito's Work Assistant

Vito's Work Assistant 是一个面向企业场景的 AI Assistant 项目。仓库按 monorepo
组织：Agent 与 Web 应用放在 `apps/`，外部系统的 MCP Server 放在 `services/`，
可复用代码放在 `libs/`，部署配置放在 `infra/`。

## 项目结构

```text
vitos-work-assistant/
├── apps/
│   ├── web/                  # Web 应用（待实现）
│   └── agent/                # DeepAgent + MCP + Skill HTTP API
├── services/
│   ├── m365-mcp-http/        # Microsoft 365 Streamable HTTP MCP Server
│   └── salesforce-mcp/       # Salesforce MCP Server（待实现）
├── libs/
│   └── common/               # 跨应用共享代码（待实现）
├── infra/                    # 部署与基础设施配置（待实现）
├── docker-compose.yml
├── .env.example
└── README.md
```

当前实现了 [`services/m365-mcp-http`](services/m365-mcp-http/README.md) 与最小版
[`apps/agent`](apps/agent/README.md)。M365 MCP 提供：

- `search_sharepoint`：使用 Microsoft Graph Search 搜索当前用户可访问的文档。
- `read_document`：下载并提取 DOCX、UTF-8 TXT 或 Markdown 正文。

M365 MCP 现在作为独立的 Streamable HTTP 服务运行，默认 endpoint 是
`http://127.0.0.1:8001/mcp`。`apps/agent` 通过持久的 Streamable HTTP MCP client
连接该 endpoint，不再 spawn 本地 stdio subprocess。Agent API 使用 Microsoft Entra
Token A 保护 `GET /me` 与 `POST /chat`；`GET /health` 保持匿名。

当前身份边界是：Alice/Bob 的 Token A 只认证 Work Assistant API。下游
`m365-mcp-http` 仍使用 Device Code cache 中的同一个 Microsoft 365 用户访问 Graph，
Agent 不向 MCP 转发 Token A；OBO、Graph Token B 与 per-user Graph access 尚未实现。
Web、Salesforce MCP 和基础设施目录仍是边界清晰的占位。

## 当前模块快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e "./services/m365-mcp-http[dev]"
python -m pip install -e "./apps/agent[dev]"

cp services/m365-mcp-http/.env.example services/m365-mcp-http/.env
# 编辑 .env，填写 M365_TENANT_ID 和 M365_CLIENT_ID
set -a
source services/m365-mcp-http/.env
set +a

python -m m365_mcp.auth login
# 保持这个独立服务运行；另一个终端再启动 Agent API
python -m m365_mcp.server
```

完整 Entra/MCP Server 配置见
[`services/m365-mcp-http/README.md`](services/m365-mcp-http/README.md)。Work Assistant
API 所需的两个 Entra App Registration、Token A 验证、Agent 环境变量、MSAL Python
Alice/Bob 测试及启动步骤见 [`apps/agent/README.md`](apps/agent/README.md)。

## 测试

从仓库根目录运行：

```bash
python -m pytest services/m365-mcp-http/tests
python -m pytest apps/agent/tests
```
