# Webhook 部署指南

## 概述

Bot 现在支持两种运行模式：

- **Polling 模式** (默认): 适合开发和测试
- **Webhook 模式**: 适合生产环境，更高效、更稳定

---

## 配置 Webhook 模式

### 1. 修改 `.env` 文件

```.env
# 将模式改为 webhook
BOT_MODE=webhook

# 配置 Webhook URL (必需)
WEBHOOK_URL=https://yourdomain.com

# 可选配置
WEBHOOK_PORT=8443
WEBHOOK_PATH=/webhook
WEBHOOK_SECRET=your-random-secret-token
```

### 2. 重启 Bot

```bash
python main.py
```

Bot 会自动检测到 webhook 模式并监听指定端口。

---

## Webhook vs Polling 对比

| 特性           | Polling 模式 | Webhook 模式     |
| -------------- | ------------ | ---------------- |
| **延迟**       | ~1-3秒       | ~100ms           |
| **服务器负载** | 持续轮询     | 按需响应         |
| **网络要求**   | 无           | 需要公网 IP/域名 |
| **适用场景**   | 开发/测试    | 生产环境         |
| **配置复杂度** | 简单         | 需要反向代理     |

---

## Nginx 反向代理配置示例

```nginx
server {
    listen 443 ssl;
    server_name yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location /webhook {
        proxy_pass http://127.0.0.1:8443/webhook;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## Docker Compose Webhook 配置

修改 `docker-compose.yml`:

```yaml
services:
  bot:
    # ... 其他配置
    ports:
      - "8443:8443" # 映射 webhook 端口
    environment:
      - BOT_MODE=webhook
      - WEBHOOK_URL=https://yourdomain.com
      - WEBHOOK_PORT=8443
```

---

## 安全建议

1. **使用 HTTPS**: Telegram 要求 Webhook 必须使用 HTTPS
2. **配置 Secret Token**: 增强安全性
3. **防火墙规则**: 只允许 Telegram IP 访问 Webhook
4. **日志监控**: 监控异常请求

---

## 故障排查

### Bot 无法注册 Webhook

```bash
# 检查 URL 是否可访问
curl https://yourdomain.com/webhook

# 检查端口是否开放
netstat -tuln | grep 8443
```

### Telegram 报错 "Certificate invalid"

- 确保使用有效的 SSL 证书
- 不能使用自签名证书（生产环境）

---

## 切换回 Polling 模式

只需修改 `.env`:

```bash
BOT_MODE=polling
```

无需其他配置，Bot 会自动切换。
