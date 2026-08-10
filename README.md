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
`http://127.0.0.1:8001/mcp`。`apps/agent` 仍保留上一阶段的 stdio client，尚未迁移
到这个 endpoint，因此当前不能与迁移后的服务直接联调；Agent client migration 是
下一阶段任务。Web、Salesforce MCP 和基础设施目录仍是边界清晰的占位。

## 当前模块快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e "./services/m365-mcp-http[dev]"

cp services/m365-mcp-http/.env.example services/m365-mcp-http/.env
# 编辑 .env，填写 M365_TENANT_ID 和 M365_CLIENT_ID
set -a
source services/m365-mcp-http/.env
set +a

python -m m365_mcp.auth login
python -m m365_mcp.server
```

完整 Entra 配置、MCP Inspector 和测试步骤见
[`services/m365-mcp-http/README.md`](services/m365-mcp-http/README.md)。

## 测试

从仓库根目录运行：

```bash
python -m pytest services/m365-mcp-http/tests
python -m pytest apps/agent/tests
```
