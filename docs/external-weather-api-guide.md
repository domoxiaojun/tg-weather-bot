# 外部聊天 Agent 天气调用使用指南

这份指南说明：如何让另一个聊天 Agent 在用户问“北京天气怎么样”时，调用本项目获取天气图片和 AI 天气总结。

这里使用的是天气服务的内网 HTTP API，不是 Telegram Bot-to-Bot 对话。另一个 Bot 不需要、也不应该拿到本项目的 `BOT_TOKEN`。

## 一、调用链

```text
用户
  ↓ 问天气
聊天 Agent Bot
  ↓ 调用 GET /v1/weather（Bearer Token）
DomoWeather 天气服务
  ├─ 和风天气 / 彩云天气获取数据
  ├─ 生成 PNG 天气卡片
  └─ 生成 AI 天气总结
  ↓ 返回 JSON
聊天 Agent Bot 发送图片 + 总结
```

推荐的最小调用方式是一次请求同时获取两样结果：

```text
GET /v1/weather?city=潮安&include=report,image
```

然后由聊天 Agent 发送：

1. `image.base64` 解码出来的 PNG；
2. `report.text` 作为 AI 天气总结。

这样不需要让聊天 Agent 自己重新排版天气表格，也不需要把完整天气数据再次交给大模型总结。

## 二、天气服务端配置

在天气 Bot 的 `.env` 中加入：

```dotenv
WEATHER_API_ENABLED=true
WEATHER_API_HOST=127.0.0.1
WEATHER_API_PORT=8080
WEATHER_API_TOKEN=替换成随机长字符串
WEATHER_API_TIMEOUT_SECONDS=45
WEATHER_API_MAX_CONCURRENCY=4
```

生成 Token 的示例：

```bash
openssl rand -hex 32
```

把命令输出填入 `WEATHER_API_TOKEN`。不要把这个 Token 写入 Git、聊天记录、截图或前端代码。

### 两个 Bot 在同一台机器上运行

如果两个进程都在同一台机器上，保持：

```dotenv
WEATHER_API_HOST=127.0.0.1
```

聊天 Agent 使用：

```text
http://127.0.0.1:8080
```

### 两个 Bot 在同一个 Docker 网络中

天气服务需要监听容器网卡：

```dotenv
WEATHER_API_HOST=0.0.0.0
WEATHER_API_PORT=8080
```

聊天 Agent 使用天气服务名访问：

```text
http://weather_bot:8080
```

两个独立 Compose 项目必须加入同一个 Docker external network。API 端口不需要映射到公网；当前 `docker-compose.yml` 里的 `8443` 是 Telegram Webhook 端口，不是这个天气 API 端口。

### 两个 Bot 在不同服务器上

不要直接把 `8080` 暴露到公网。使用以下任一方式：

- 私有网络、VPN 或云厂商内网；
- 只允许聊天 Agent 来源 IP 的防火墙规则；
- 反向代理 + HTTPS + Token。

HTTPS 是传输加密，Token 是调用身份校验，两者不能互相替代。

## 三、接口和认证

除 `/healthz` 外，接口都需要认证头：

```http
Authorization: Bearer <WEATHER_API_TOKEN>
```

也兼容：

```http
X-Weather-API-Key: <WEATHER_API_TOKEN>
```

### 1. 健康检查

```http
GET /healthz
```

返回：

```json
{"ok": true, "service": "weather-api"}
```

它只表示 HTTP 服务正在监听，不代表和风天气、彩云天气或 LLM 一定可用。

### 2. 天气数据 + 图片 + AI 总结（推荐）

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $WEATHER_API_TOKEN" \
  --get "http://127.0.0.1:8080/v1/weather" \
  --data-urlencode "city=潮安" \
  --data-urlencode "include=report,image"
```

返回结构的关键字段：

```json
{
  "ok": true,
  "location": "潮安, 广东省",
  "updated_at": "2026-08-25T10:46:00+08:00",
  "card": {
    "format": "telegram-rich-blocks",
    "fallback_text": "MarkdownV2 格式的天气正文",
    "image_path": "/v1/weather/card.png?city=%E6%BD%AE%E5%AE%89"
  },
  "report": {
    "available": true,
    "text": "🕐 数据时间：...\n\n🌤️ <b>现在</b>..."
  },
  "image": {
    "media_type": "image/png",
    "base64": "iVBORw0KGgo..."
  },
  "weather": {}
}
```

`weather` 是完整结构化数据；如果你的需求只是图片和 AI 总结，可以不读取它。

### 3. 只取 PNG 图片

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $WEATHER_API_TOKEN" \
  --get "http://127.0.0.1:8080/v1/weather/card.png" \
  --data-urlencode "city=潮安" \
  -o weather.png
```

这个响应的 `Content-Type` 是 `image/png`，可直接交给 Telegram 的 `send_photo`。

### 4. 只取 AI 总结

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $WEATHER_API_TOKEN" \
  --get "http://127.0.0.1:8080/v1/weather/report" \
  --data-urlencode "city=潮安"
```

如果天气 Bot 没有配置 LLM，返回：

```json
{"report": {"available": false, "error": "report_unavailable", "text": null}}
```

如果 AI 未配置、超时或供应商失败，`report.available` 都会是 `false`，并分别带有
`report_unavailable`、`report_timeout` 或 `report_failed`。聊天 Agent 应该继续发送天气图片，
而不是把整次请求判定为失败。

## 四、聊天 Agent 的调用函数

建议把天气服务注册成一个 Agent 工具，例如工具名叫 `get_weather`，参数只有一个：

```json
{
  "name": "get_weather",
  "description": "查询城市天气，并返回天气图片和 AI 天气总结",
  "parameters": {
    "type": "object",
    "properties": {
      "city": {
        "type": "string",
        "description": "城市名，例如 北京、潮安、上海"
      }
    },
    "required": ["city"],
    "additionalProperties": false
  }
}
```

Python 调用示例：

```python
import base64

import httpx


async def get_weather(city: str) -> dict:
    # 服务端会把整次请求限制在 WEATHER_API_TIMEOUT_SECONDS 内（默认 45s）。
    async with httpx.AsyncClient(timeout=55) as client:
        response = await client.get(
            "http://127.0.0.1:8080/v1/weather",
            params={"city": city, "include": "report,image"},
            headers={"Authorization": "Bearer 你的 WEATHER_API_TOKEN"},
        )
        response.raise_for_status()
        payload = response.json()

    image_bytes = base64.b64decode(payload["image"]["base64"])
    report = payload.get("report") or {}
    return {
        "location": payload["location"],
        "image_bytes": image_bytes,
        "report_text": report.get("text") if report.get("available") else None,
        "report_error": report.get("error") if not report.get("available") else None,
        "fallback_text": payload["card"]["fallback_text"],
    }
```

实际项目中不要把 Token 硬编码在函数里，应从聊天 Agent 自己的环境变量读取：

```python
import os

WEATHER_API_BASE = os.environ["WEATHER_API_BASE"]
WEATHER_API_TOKEN = os.environ["WEATHER_API_TOKEN"]
```

## 五、在 Telegram 里发送结果

推荐分成两条消息：第一条发图片，第二条发 AI 总结。这样不会受到 Telegram 图片 caption 约 1024 字符限制。

```python
import io
from telegram import InputFile


result = await get_weather("潮安")

await telegram_bot.send_photo(
    chat_id=chat_id,
    photo=InputFile(io.BytesIO(result["image_bytes"]), filename="weather.png"),
)

if result["report_text"]:
    await telegram_bot.send_message(
        chat_id=chat_id,
        text=result["report_text"],
        parse_mode="HTML",
    )
else:
    await telegram_bot.send_message(
        chat_id=chat_id,
        text=result["fallback_text"],
        parse_mode="MarkdownV2",
    )
```

AI 总结使用 Telegram HTML，只允许 `<b>`、`<i>` 等项目现有输出约定。如果你的聊天 Agent 发送 HTML 失败，应降级为不设置 `parse_mode` 的纯文本，而不是把异常信息发给用户。

## 六、Agent 提示词建议

可以在聊天 Agent 的工具说明中加入：

```text
当用户询问天气、温度、下雨、体感或出门建议时，调用 get_weather。
城市不明确时先追问城市，不要猜测。
调用成功后优先发送天气图片；如果有 report_text，再原样发送 AI 天气总结。
不要重新编造天气数字，不要把 weather 原始 JSON 全部展示给用户。
如果 report 不可用，发送 fallback_text。
```

## 七、错误处理

| HTTP 状态 | `error` | 处理建议 |
|---:|---|---|
| 400 | `missing_city` / `city_too_long` | 检查城市参数，必要时让用户重新说明 |
| 401 | `unauthorized` | 检查 Token，不要重试同一个错误 Token |
| 404 | `city_not_found` | 让用户补充省份或更完整的城市名 |
| 408 | `weather_timeout` / `report_timeout` | 稍后重试；若只是报告超时，仍发送图片 |
| 503 | `weather_provider_unavailable` | 稍后重试，不要循环高频请求 |

建议聊天 Agent 自己增加 30～60 秒的短缓存，避免同一用户连续追问时反复消耗天气 API 和 LLM 配额。天气服务本身已有供应商缓存，但缓存不是调用方可以依赖的永久契约。

## 八、上线前检查清单

- [ ] `WEATHER_API_ENABLED=true` 且 `WEATHER_API_TOKEN` 非空。
- [ ] `WEATHER_API_HOST` 只对需要访问的网络开放。
- [ ] API 端口没有被无意映射到公网。
- [ ] 聊天 Agent 的 Token 从环境变量读取，不写进代码和提示词。
- [ ] `/healthz` 返回 200。
- [ ] 带正确 Token 的 `/v1/weather?city=潮安&include=report,image` 返回 200。
- [ ] 错误 Token 返回 401。
- [ ] 城市不存在时能处理 404。
- [ ] LLM 不可用时仍能发送 PNG 和 `fallback_text`。
- [ ] 图片、AI 总结和降级文本都在聊天 Agent 的真实 Telegram 会话中试发一次。

## 九、明确的边界

这个接口目前只用于“外部聊天 Agent 查询天气”。它不提供订阅管理、雨警推送、台风、潮汐或 Telegram 会话代理，也不会共享两个 Bot 的聊天历史。

如果未来需要这些能力，应为每项能力单独设计接口和权限，不要把 Telegram Bot Token 交给另一个 Bot。
