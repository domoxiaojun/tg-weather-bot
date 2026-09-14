# Docker Compose 部署指南

本文命令在**部署服务器**的仓库目录执行。项目提供两种镜像来源：本地根据源码构建，或从 GHCR 使用已发布的 amd64/arm64 tag 镜像。GitHub Actions 不会自动更新服务器上的容器。

## 0. 使用 GHCR 发布镜像（可选）

`Publish Docker image` workflow 只接受 `v*` Git tag 或手动 `workflow_dispatch`，发布到：

```text
ghcr.io/domoxiaojun/tg-weather-bot:<tag>
```

它构建 `linux/amd64` 和 `linux/arm64` manifest。发布 workflow 成功后，服务器可跳过源码构建，使用下面的 Compose override：

```yaml
services:
  bot:
    image: ghcr.io/domoxiaojun/tg-weather-bot:v1.0.0
    build: null
```

私有 GHCR 包需要先在服务器执行 `docker login ghcr.io`；公开包可直接拉取。使用 `latest` 之前应确认它对应的发布运行；tag 和 digest 才是可复现部署依据。镜像发布成功不代表服务器已经更新，更新仍需执行 `docker compose pull bot`、`docker compose up -d bot` 并检查日志和 Telegram 实测。

默认 Compose 文件仍使用 `build: .`，不填写 override 时会继续从源码本机构建。

## 1. 准备服务器

需要 Git、Docker Engine 和 Docker Compose v2 插件。服务器需要能访问 Telegram、和风天气，以及构建所需的镜像仓库、Debian 软件源和 PyPI；使用彩云或 AI 时还需访问对应服务。

```bash
git --version
docker version
docker compose version
git clone https://github.com/domoxiaojun/tg-weather-bot.git
cd tg-weather-bot
mkdir -p data logs secrets
cp .env.example .env
chmod 600 .env
chmod 700 secrets
```

`cp` 只用于首次安装；已有 `.env` 时直接编辑，不能用示例覆盖已有凭据。使用本地构建时，宿主机无需安装 Python、uv 或 Redis，Compose 会构建 Python 应用并启动 Redis 容器。使用 GHCR 镜像时仍需 Docker Compose 和 Redis 容器。使用 Docker 的账户需要访问 Docker daemon 的权限。

## 2. 填写配置

在服务器编辑 `.env`，不要把真实凭据提交到 Git。

### 最小配置：轮询 + 和风 API Key

```dotenv
BOT_TOKEN=替换为BotFather给出的Token
BOT_MODE=polling
QWEATHER_AUTH_MODE=api_key
QWEATHER_API_KEY=替换为和风APIKey
QWEATHER_API_HOST=https://替换为控制台分配的专属Host
ENABLE_CAIYUN_API=false
WEATHER_API_ENABLED=false
```

Host 只填根地址，不加 `/v7`、`/geo/v2` 或接口路径。示例里的占位符必须替换。确认和风凭据有 GeoAPI、实况、逐小时、逐日等所选产品权限；默认请求 `72h` 和 `15d`，免费额度、权限与单价以自己的控制台为准。当前天气接口仍为 v7，天气 v1 的接入方案不等于已经完成迁移。

Polling 同样可用于长期部署，不要求公网域名。一个 Bot Token 只运行一个 polling 实例，更新时不要同时启动另一份本地进程或容器。

### 可选：和风 JWT

已有私钥时放入 `secrets/qweather_ed25519_private.pem`，然后设置：

```dotenv
QWEATHER_AUTH_MODE=jwt
QWEATHER_JWT_PRIVATE_KEY_FILE=secrets/qweather_ed25519_private.pem
QWEATHER_JWT_KID=替换为凭据ID
QWEATHER_JWT_SUB=替换为项目ID
```

文件在容器内对应 `/app/secrets/qweather_ed25519_private.pem`。需要生成密钥时，先构建镜像，再运行仓库密钥工具（无需启动 Bot）：

```bash
docker compose build bot
docker compose run --rm --no-deps \
  -v "$(pwd)/secrets:/app/secrets:rw" \
  bot python scripts/generate_qweather_key.py
chmod 600 secrets/qweather_ed25519_private.pem
```

这条一次性命令将 `secrets` 临时挂为可写，正常 Bot 服务仍使用只读挂载。把工具输出的**公钥**登记到和风控制台，然后填写 KID/SUB。切勿上传私钥。`.dockerignore` 排除 secrets、PEM/KEY 和环境变量备份，正常构建不会把这些文件复制进镜像。

### 可选：彩云、AI 与 Telegram 功能

- 彩云：设置 `ENABLE_CAIYUN_API=true` 和 `CAIYUN_API_TOKEN`。综合响应默认缓存一小时；刷新天气只强制更新和风，彩云继续复用缓存。分钟降水仍由和风提供。
- AI：设置 `LLM_PROVIDER` 及对应的 `OPENAI_API_KEY` 或 `GEMINI_API_KEY`；不配置时不提供 AI 日报，基础天气仍可用。
- Inline：在 BotFather 开启 `/setinline`；Inline AI 占位消息更新还需要 `/setinlinefeedback` 为 100%。Inline 图表需要配置 `SUPER_ADMIN_ID`，且该用户先私聊 Bot。
- Guest：在 BotFather 的 Bot Settings 开启 Guest Mode；仅在 `.env` 打开开关不足以获得服务端能力。
- 其他选项参见根目录 `.env.example` 和 README。

## 3. 端口、环境变量和数据卷

默认 `docker-compose.yml` 启动两个服务：`bot`（容器名 `weather_bot`）和 `redis`（容器名 `weather_redis`）。固定容器名会使同机第二套部署冲突；多套部署需分别修改名称、目录和端口。

| 项目 | 默认行为 |
| --- | --- |
| 8443 | Compose 默认发布 `${WEBHOOK_PORT:-8443}` 到宿主机所有网卡。polling 无需此端口，可删除 bot 下整个 `ports:` 段 |
| Webhook | 仅 `BOT_MODE=webhook` 启动 HTTP listener，端口映射本身不会切换模式 |
| 天气 API 8080 | 默认功能关闭；启用后容器内监听 0.0.0.0，但 Compose 不发布此端口 |
| Redis 6379 | 仅 Compose 网络内访问，不发布到宿主机 |
| `./data:/app/data` | 订阅、推送状态 `bot_data.pickle`、启动轮转备份及自定义 Emoji 覆盖配置，必须保留 |
| `./logs:/app/logs` | 文件日志，另有 Docker stdout/stderr 日志 |
| `./secrets:/app/secrets:ro` | JWT 私钥只读挂载 |
| `redis_data` 命名卷 | Redis 数据目录；Redis 用于缓存，订阅保存在 `./data`，不是 Redis |

Compose 的 `environment` 优先于 `.env`：它固定覆盖 `REDIS_URL=redis://redis:6379/0`、`TZ=Asia/Shanghai`、`WEATHER_API_HOST=0.0.0.0`，并从环境变量/`.env` 取 `WEATHER_API_PORT`。不要误以为修改 `.env` 中的同名值一定能覆盖 Compose。Bot 的订阅时区设置 `TIMEZONE` 与容器 `TZ` 是不同配置。

Redis 连接失败时应用会使用进程内缓存，但这不代表跨重启、跨实例缓存仍有效。`depends_on` 只控制启动顺序，不保证 Redis 已就绪。保留 `redis_data` 卷也不代表自动获得每一次缓存写入的持久性保证。

## 4. 构建、启动与验收

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 -f bot
```

`config --quiet` 验证配置，不输出展开后的 Token。`Ctrl+C` 只退出日志跟随，不会停止后台容器。检查日志中的运行模式、认证方式以及错误，再在 Telegram 实测 `/start`、`/tq 佛山`，并点击刷新和逐小时按钮。启用 AI、订阅或外部天气 API 后分别验证这些功能。

容器健康检查每 5 分钟请求 Telegram `getMe`，连续 3 次失败会显示 unhealthy。它检查 Token 与 Telegram 出网，不代表天气接口、主循环或 LLM 正常。`restart: always` 会重启退出的容器，**不会仅因 unhealthy 自动重启一个仍在运行的进程**。

## 5. 可选：Webhook 与内部天气 API

### Webhook

```dotenv
BOT_MODE=webhook
WEBHOOK_URL=https://你的域名
WEBHOOK_PATH=/webhook
WEBHOOK_PORT=8443
WEBHOOK_SECRET=替换为随机字符串
```

本项目 `run_webhook` 没有配置 TLS 证书，容器监听的是 HTTP。外部 HTTPS 由反向代理终止，并转发 `/webhook` 到容器 HTTP listener；不能仅因端口号是 8443 就把上游写成 HTTPS。

若反向代理运行在同一宿主机，可将 Compose 端口行改为下面的回环绑定，代理上游使用 `http://127.0.0.1:8443`：

```yaml
ports:
  - "127.0.0.1:${WEBHOOK_PORT:-8443}:${WEBHOOK_PORT:-8443}"
```

若反向代理也在容器中，应加入同一私有网络，上游使用 `http://bot:8443`（端口随配置调整），不要在代理容器里使用 `127.0.0.1` 指向 Bot。保留 Telegram 的 Webhook secret 请求头。切换配置后重新创建 Bot 容器。

### 内部天气 API

设置 `WEATHER_API_ENABLED=true` 和随机 `WEATHER_API_TOKEN`。同一 Compose 网络里的调用方使用 `http://bot:8080`；`weather_bot` 是本项目的容器名，也可在该网络解析。独立 Compose 项目默认网络互不相通，需显式加入共同 external network。不要把天气 API Token 和 Bot Token 混用。

完整请求、鉴权和网络说明见[外部聊天 Agent 指南](external-weather-api-guide.md)。

## 6. 更新源码与修改配置

更新前记录当前提交，并备份下一节列出的持久化文件。确认 `git status --short` 中的本地修改已保存；拉取冲突时先解决，不能用强制重置覆盖配置或本地工作。

使用 GHCR override 更新已发布 tag：

```bash
docker compose -f docker-compose.yml -f compose.ghcr.yml pull bot
docker compose -f docker-compose.yml -f compose.ghcr.yml up -d bot
docker compose -f docker-compose.yml -f compose.ghcr.yml ps
```

`docker compose pull` 只会拉取 `image:`，不会更新 `build: .` 服务。发布 tag、digest 和服务器实际运行容器必须分别记录。

```bash
git rev-parse HEAD
git status --short
git pull --ff-only origin main
docker compose config --quiet
docker compose up -d --build bot
docker compose ps
docker compose logs --tail=100 bot
```

Bot 是 `build: .` 服务，`docker compose pull` 不会更新它的源码或重新构建它。修改代码/Dockerfile/依赖后需要 `--build`；需要主动刷新基础镜像时先执行 `docker compose build --pull bot`，再 `docker compose up -d bot`。

只修改 `.env` 时必须重新创建容器，`restart` 不会重新读入容器环境变量：

```bash
docker compose config --quiet
docker compose up -d --force-recreate --no-deps bot
```

修改 Compose 网络、端口或挂载后使用 `docker compose up -d` 应用服务配置。不要在更新时覆盖 `.env`、`data` 或 `secrets`。运行多个副本会影响 polling 和缓存去重，本指南按单 Bot 实例部署。

## 7. 备份与恢复

订阅主文件为 `data/bot_data.pickle`，启动时会按 `PERSISTENCE_BACKUP_COUNT` 轮转备份。完整备份还需包含 `.env`、`secrets/`、`docker-compose.yml`、本地 Compose override 文件及当前源码提交号。日志可按需要归档，Redis 缓存通常可以重新获取。

下面示例先暂停 Bot，避免备份正在写入的 Pickle。归档含 Token 和私钥，应只保留在受控存储中：

```bash
umask 077
mkdir -p ../weather-backups
git rev-parse HEAD > ../weather-backups/source-commit.txt
docker compose stop bot
tar -czf "../weather-backups/weather-$(date +%Y%m%d-%H%M%S).tar.gz" \
  .env docker-compose.yml data secrets
docker compose start bot
```

如果使用额外 override 文件，必须手动加进归档。确认备份命令成功，再记录对应提交号。恢复时先停 Bot，把可信归档解压到单独目录核对后恢复文件、权限和匹配源码，再运行 `docker compose up -d --build bot` 验证。Pickle 只可从自己可信的备份恢复，不能导入陌生文件。

普通停机使用 `docker compose stop`；删除容器但保留命名卷可用 `docker compose down`。不要在常规更新中使用 `down -v`，它会删除命名卷。绑定目录 `data` 和 `secrets` 仍需单独备份，不能把“卷未删除”当作备份成功。

## 8. 常见问题

| 现象 | 检查方向 |
| --- | --- |
| `docker compose` 不存在 | 部署服务器是否安装 Compose v2 插件，而非只有旧的 `docker-compose` |
| 构建依赖下载失败 | 镜像仓库、Debian 软件源、PyPI 和服务器出网；查看构建日志，不要反复更换天气凭据 |
| Bot 启动即退出 | `docker compose logs --tail=100 bot`；Token、JWT路径/权限、必填配置、Webhook URL |
| polling 报 Conflict | 同一 Token 是否还有另一台服务器、本机进程或旧容器在轮询 |
| Telegram 正常但天气失败 | 和风 Host、凭据、产品权限/额度及供应商连通性；healthcheck不覆盖这些 |
| `.env` 修改没生效 | Compose override优先级，是否用 force-recreate重建容器而非restart |
| 订阅重建后消失 | 工作目录是否改变、`./data` 是否指向原目录，勿删除pickle文件 |
| 内部 API connection refused | 功能是否启用、端口、共同网络、调用方是否误用localhost |
| unhealthy 但容器没重启 | 健康状态不自动触发restart策略；先定位getMe失败原因和主进程状态 |

检查输出可能包含运行位置或错误上下文，分享日志前脱敏凭据。实际验收以服务器容器和 Telegram 收发为准，本机静态检查及 Git push 都不是部署完成证明。
