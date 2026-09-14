# 安装与 Action 工作流审查（2026-09-14）

## 结论

当前 `.github/workflows/ci.yml` 的 `container` job 只在 GitHub-hosted runner 中执行：

```text
docker build -t tg-weather-bot:ci .
docker run ... tg-weather-bot:ci ...
```

独立的 `.github/workflows/docker-publish.yml` 才负责 GHCR 发布，使用 `docker/build-push-action`、`push: true`、`linux/amd64,linux/arm64` 和 `packages: write`。只有发布 job 实际成功并可在 GHCR 查询到 tag/digest，才能说明镜像发布完成；workflow 配置或 CI 成功本身不是发布证据，也不代表已经部署。

CI 触发器是推送到 `main`、Pull Request、手动 `workflow_dispatch`；发布 workflow 触发器是 `v*` tag 和手动 `workflow_dispatch`。普通 CI 成功不会发布正式镜像。

## 当前安装路径

### uv 本机运行

适合开发和调试。复制 `.env.example` 为 `.env`，填写 `BOT_TOKEN` 与和风凭据，使用 `uv venv`、`uv pip install` 和 `uv run python main.py`。Redis 不是硬依赖，连接失败会降级到进程内缓存；开启彩云时仍需填写彩云 Token。

### Docker Compose 本机构建

Compose 使用本地 `Dockerfile` 构建 `tg-weather-bot`，同时启动 Redis；不会从远程镜像仓库拉取天气 Bot 镜像。`./data`、`./logs` 和 `./secrets` 挂载到容器，Redis 使用命名卷 `redis_data`。`.env` 不会被复制进镜像。

轮询模式不需要公开端口，但当前 Compose 文件默认仍发布 `WEBHOOK_PORT`（默认 8443），可在 polling 部署中删除 `ports` 段。Webhook 模式才需要该 listener 的入站连通性；天气内部 API 的 8080 端口不在 Compose 中映射到宿主机，只应在私有 Docker 网络中使用。

可执行步骤见[Docker Compose 部署指南](docker-compose.md)。本次补齐 `.dockerignore` 对 `secrets/`、PEM/KEY、`.env.*` 的排除；只读卷挂载并不能阻止 `COPY . .` 将未排除的宿主文件带进镜像。

### 健康检查边界

容器 healthcheck 调用 Telegram `getMe`，验证 Bot Token、DNS/出网和 Telegram 可达性。它不证明 polling/webhook 主循环、和风、彩云、Redis 或 LLM 业务成功。

## 文档风险与修订

- `docker-compose` 是兼容旧版 Compose 的命令；新安装说明统一使用 `docker compose`。
- “Docker Deploy (Recommended)”容易被理解为已有官方镜像；应明确为“本地构建并运行”。
- 不能在 README 写 `docker pull`、GHCR 地址或“Action 构建完成即可部署”，因为当前仓库没有镜像发布流程。
- 若以后新增 GHCR workflow，必须同时定义镜像名、tag 规则、权限 `packages: write`、多架构策略、digest/签名验证、手动/版本触发条件，并在安装文档单独说明。
- `.env.example` 的 `QWEATHER_API_HOST` 必须填根 Host，不要附加 `/v7` 或 `/geo/v2`。
- 使用 JWT 时，`secrets/` 只在宿主机保存并以只读卷挂载；不要把私钥写入环境变量示例、镜像层或 Git。

## 未来镜像发布建议

镜像发布已作为独立 workflow，不让普通 PR 或 main 推送自动推送；版本 tag 或明确手动触发才发布 amd64/arm64。正式上线仍应记录不可变版本 tag 和 digest，并在部署主机验证容器状态、healthcheck、监听端口、持久化目录和真实 Bot 收发。
