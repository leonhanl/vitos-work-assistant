# Work Assistant 构建产物部署

这个包用于在 Linux amd64 主机上运行 GitHub Actions 构建的镜像。需要安装 Docker 和
Docker Compose。三个下载文件必须来自同一次成功构建，文件名中的 `<version>` 是完整
Git commit SHA；`version.json` 记录对应的镜像和 Skills 版本。

## 1. 解压配置、导入镜像与 Skills

在一个新的部署目录中放入三个下载文件，将下面的 `<version>` 替换为实际值后运行：

```bash
unzip work-assistant-deploy-<version>.zip
docker load -i work-assistant-images-<version>-linux-amd64.tar.gz
mkdir -p skills
tar -xzf work-assistant-skills-<version>.tar.gz -C skills
```

这个包里的 `compose.yaml` 使用 `image:`，不会在服务器重新构建。仓库根目录的
`compose.yaml` 仍用于本地构建和开发，两者用途不同。

## 2. 填写运行时配置

首次部署时复制模板；更新现有部署时保留真实配置：

```bash
cp .env.example .env
cp apps/agent/.env.example apps/agent/.env
cp apps/redteam-bridge/.env.example apps/redteam-bridge/.env
```

- 根目录 `.env`：已经填入本次镜像与 Skills 版本，可以调整宿主机端口和 Skills 路径。
- `apps/agent/.env`：填写 Portkey、Entra、M365 MCP、Jira MCP 和服务台配置。
  Skills 路径和版本由 Compose 覆盖，无需修改模板中的对应值。
- `apps/redteam-bridge/.env`：填写测试用户与客户端 ID、Bridge API key；保留 Docker
  默认地址 `http://agent:8000/chat` 和 `/data/` 路径。

M365 MCP、Jira MCP 和 Portkey 需要独立运行，并且容器必须能访问配置的地址。
模板中的 `127.0.0.1` 指向容器自身，部署时需替换为可达的 MCP 地址。
完整配置说明见仓库 README 和 `apps/redteam-bridge/README.md`。

Web 的 Entra 参数已在 CI 构建时写入镜像，服务器不需要 `apps/web/.env`。
更换这组参数需要更新 GitHub 的 `WEB_ENV` Secret 并重新构建 Web 镜像。
部署域名的 `/redirect.html` 地址还需配置在 Entra SPA 应用的 Redirect URI 中。

## 3. 授权并启动

先校验配置，再在同一个部署目录与 Compose 项目中完成 Bridge 设备码授权：

```bash
docker compose config --quiet
docker compose run --rm --no-deps redteam-bridge redteam-bridge-login
docker compose up -d --pull never
docker compose ps
```

按终端提示在浏览器中使用配置的测试用户完成登录。缓存保存在 `redteam-data` Volume，
不需要重新打包镜像。用户授权过期时，在同一目录重新执行上述登录命令。

Web 默认访问地址为 `http://<host>:5173`，Bridge 为 `http://<host>:8080`。
对外 HTTPS 入口沿用现有反向代理配置。可编辑 `apps/web/public/config/scenarios.json`
后刷新页面调整预设问题，无需重建镜像。

更新部署时，将新包的镜像版本和 Skills 版本同步到现有 `.env`，保留密钥和 Volume。
停止服务使用 `docker compose down`；`down -v` 会删除 Bridge 的授权和会话数据。
