# Jira MCP HTTP 服务

本目录使用 [sooperset/mcp-atlassian](https://github.com/sooperset/mcp-atlassian)
运行一个本地的 Streamable HTTP MCP 服务，用于访问 Jira Service Management（JSM）。

Work Assistant 通过此服务完成以下操作：

- 查询指定服务台中可用的请求类型；
- 查询某个请求类型需要填写的字段；
- 在用户确认 ticket 内容后创建 JSM 客户请求。

该服务有意限制为只开放以下 MCP 工具：

- `jira_get_request_types`
- `jira_get_request_type_fields`
- `jira_create_customer_request`

## Jira 与 Jira Service Management 的区别

Jira 是底层的工作跟踪平台，提供项目、字段、工作流、权限、工作项和 issue key
等基础能力。

Jira Service Management 是构建在 Jira 之上的服务管理产品。它在 Jira 的基础上增加了
客户门户、请求类型、队列、服务台 Agent、SLA 和面向客户的请求等能力。JSM 请求与 IT
团队处理的 Jira 工作项，是同一个工作单元的两个视图：客户看到的是 request，服务台
Agent 处理的是对应的 Jira work item。更多信息可参考 Atlassian 的
[请求与工作项说明](https://support.atlassian.com/jira-service-management-cloud/docs/what-are-issues-and-requests/)。

本集成创建的是 **JSM 客户请求**，而不是普通的 Jira issue。JSM 客户请求必须指定服务台、
请求类型以及该请求类型要求的字段，同时还可以代表提出问题的员工创建请求。

## 开发租户

建议使用一个独立的 Atlassian Cloud 开发站点，并在其中启用 Jira 和 Jira Service
Management。与 Work Assistant 场景对应的开发环境大致包含：

- 一个供 IT 支持团队使用的 JSM 服务项目；
- 一个适用于 VPN 故障或通用 IT 支持的客户请求类型；
- 该请求类型包含 `summary` 和 `description`，以及项目配置的其他必填字段；
- Alice 和 Bob 两个测试用户，他们作为客户可以在该服务项目中提交请求；
- 不配置 SSO，Alice 和 Bob 在开发租户中使用普通的 Atlassian 账号。

Alice 和 Bob 的身份应当能够从 Work Assistant 已认证的用户身份，稳定映射到 JSM
接受的 `raiseOnBehalfOf` 标识。在简单的开发环境中，通常可以使用其电子邮件地址或
Atlassian account identifier，但最终取决于该 JSM 站点接受的标识类型。

JSM 中的客户与服务台 Agent 是不同的角色：客户负责提出支持请求，具有许可证的 Agent
负责分类、处理和解决请求。更多信息可参考 Atlassian 的
[客户与组织说明](https://support.atlassian.com/jira-service-management-cloud/docs/what-are-customers-and-organizations-in-your-service-project/)。

创建服务项目后，需要记录它的数字 service desk ID。Work Assistant 目前在
`apps/agent/.env.example` 中使用 `service_desk_id=3` 表示该配置。它属于 Agent 侧的
业务配置，不会被 Jira MCP 容器直接读取。

request type ID 可以由 Agent 在运行时通过 `jira_get_request_types` 查询并选择；如果部署
环境始终使用同一个 IT 请求类型，也可以在 Agent 侧将其配置为固定值。

## Service account 与 API token

为本集成创建一个专用、非交互式的 Atlassian service account，例如
`jsm-mcp-integration`。不要使用 Alice、Bob 或管理员的个人账号。

为 service account 分配应用访问权限，以及满足以下操作所需的最小 JSM 项目角色和权限：

- 访问 IT 服务项目；
- 读取请求类型及其字段；
- 创建客户请求；
- 代表 Alice 和 Bob 创建请求。

Service account 需要比普通客户账号更多的权限，因为 JSM API 不允许只有客户权限的用户
设置 `raiseOnBehalfOf`。相关 API 约束可参考
[创建客户请求](https://developer.atlassian.com/cloud/jira/service-desk/rest/api-group-request/#api-rest-servicedeskapi-request-post)。

为 service account 创建 scoped API token，只授予三个已开放工具所需的 Jira/JSM 读取
和写入 scope。Token 应设置有限的有效期，并制定轮换计划。Atlassian service-account
token 同时受到 API scope 和 Jira 项目权限的检查。更多信息可参考
[管理 service account API token](https://support.atlassian.com/user-management/docs/manage-api-tokens-for-service-accounts/)。

Scoped service-account token 必须通过 Atlassian API Gateway URL 访问 Jira，并使用站点的
**Cloud ID**，而不是 Organization ID：

```text
https://api.atlassian.com/ex/jira/<cloud-id>
```

## 配置 MCP 服务

根据 `.jira.env.example` 创建 `services/jira-mcp-http/.jira.env`，并替换其中的所有
占位值：

```dotenv
JIRA_URL=https://api.atlassian.com/ex/jira/<cloud-id>
JIRA_USERNAME=<service-account-email>@serviceaccount.atlassian.com
JIRA_API_TOKEN=<service-account-api-token>

ALLOW_GLOBAL_CRED_FALLBACK=true
IGNORE_HEADER_AUTH=true
```

| 环境变量 | 用途 |
| --- | --- |
| `JIRA_URL` | 开发站点对应的 Atlassian API Gateway 基础 URL。 |
| `JIRA_USERNAME` | Atlassian 为 service account 分配的电子邮件地址。 |
| `JIRA_API_TOKEN` | 为该 service account 创建的 scoped API token。 |
| `ALLOW_GLOBAL_CRED_FALLBACK` | 允许 MCP 调用使用容器中配置的固定凭据。 |
| `IGNORE_HEADER_AUTH` | 忽略传入的 `Authorization` header，始终使用配置的 service account 访问 Jira。 |

不要提交 `.jira.env`，不要在日志中输出 token，也不要把该 token 放入 Agent 配置。生产环境
应当使用部署平台提供的 secret store 保存凭据。

## 启动服务

启动前需要满足以下条件：

- 已安装并启动 Docker；
- 当前机器能够访问 `api.atlassian.com`；
- `.jira.env` 已创建并包含有效的 service-account 凭据。

由于启动脚本中的 `--env-file` 使用相对路径，因此需要从本目录运行脚本：

```bash
cd services/jira-mcp-http
./startup.sh
```

启动脚本会替换已有的 `mcp-atlassian` 容器，启动上游发布的 Docker 镜像，并在以下地址
提供 MCP endpoint：

```text
http://127.0.0.1:9000/mcp
```

可以通过以下命令查看容器状态和日志：

```bash
docker ps --filter name=mcp-atlassian
docker logs mcp-atlassian
```

修改 `.jira.env` 后，重新运行 `./startup.sh` 即可应用新配置。停止服务但保留配置文件：

```bash
docker stop mcp-atlassian
```

当前启动脚本使用 `latest` 镜像标签。在共享环境或生产环境中使用之前，应当固定到经过测试的
`mcp-atlassian` 版本。

## 认证与请求用户身份

系统中存在两个独立的信任关系：

```text
Work Assistant -> jira-mcp-http -> Atlassian JSM API
       无 MCP 认证       service account + API token
```

Agent 不会把 Alice 或 Bob 的 Atlassian 凭据传给 MCP 服务。MCP 服务始终使用固定的
service account 向 JSM 进行认证。在最终调用 `jira_create_customer_request` 时，Agent
需要通过 `raise_on_behalf_of` 传入当前用户可信的 Jira 标识。

Agent 还必须设置 `strict_on_behalf=true`。如果 JSM 无法代表当前用户创建请求，整个操作
必须失败，而不能静默回退为由 service account 提交 ticket。Agent 必须从可信的应用状态
中获取 `service_desk_id` 和 `raise_on_behalf_of`，不能使用模型自行生成的值。

由于 Agent 到 MCP 之间没有认证，而 MCP 使用具有写权限的固定凭据，因此任何能够访问此
endpoint 的进程都可以通过 service account 执行操作。启动脚本目前将端口绑定到
`127.0.0.1`。部署到其他环境时，应将服务保留在私有网络内，并使用网络策略、service mesh
或可信 gateway 限制调用方。

## 预期的 ticket 流程

1. Alice 或 Bob 向 Work Assistant 请求协助解决 VPN 问题。
2. Agent 使用 Microsoft 365 MCP 工具执行基础故障排查。
3. 如果问题仍未解决，Agent 整理 ticket 的 summary 和 description。
4. 用户检查并确认将要提交的准确内容。
5. Agent 选择配置的 service desk 和有效的 request type，提供所有必填字段，并将当前用户
   设置为 `raise_on_behalf_of`。
6. Jira MCP 服务使用自己的 service account 创建客户请求，并返回新的 Jira issue key
   和请求信息。

只有最后的创建操作属于写操作。查询 request type 及其字段定义可以在用户确认前完成。

## 常见问题

- **Atlassian 返回 401：** 检查 API token 是否仍然有效、service-account 邮箱是否正确，
  并确认 `JIRA_URL` 使用的是 Cloud ID，而不是 Organization ID。
- **返回 403 或查询不到请求类型：** 同时检查 token scope、service account 的应用访问
  权限、JSM 项目角色和项目权限。
- **`raise_on_behalf_of` 失败：** 确认 service account 有权代表客户创建请求，Alice 或
  Bob 已经是该服务项目能够识别的客户，并确认开发站点接受哪一种用户标识。
- **缺少必填字段：** 使用 `jira_get_request_type_fields` 查询请求类型，并在
  `request_field_values` 中提供全部必填字段。
- **无法访问 MCP endpoint：** 检查 Docker 状态和 `docker logs mcp-atlassian`，并确认
  MCP 客户端连接的是 `9000` 端口下的 `/mcp` 路径。

上游项目的更多配置及工具信息，请参考
[mcp-atlassian 配置文档](https://github.com/sooperset/mcp-atlassian/blob/main/docs/configuration.mdx)
和 [Jira Service Desk 工具文档](https://github.com/sooperset/mcp-atlassian/blob/main/docs/tools/jira-service-desk.mdx)。
