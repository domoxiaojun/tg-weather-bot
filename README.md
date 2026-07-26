# DomoWeather Bot (Next-Gen)

A powerful, dual-engine Telegram Weather Bot built with Python 3.12+ and optimal architecture.

## 🌟 Features

- **QWeather-first Global Fusion**: QWeather supplies core weather, 5-minute precipitation, air quality, and alerts. When enabled, Caiyun globally fills only fields QWeather did not return, including native hourly feels-like temperature; it never replaces valid QWeather values.
- **Paid-call Protection**: Caiyun's complete `72h/15d/alert` response is cached by normalized coordinates for one hour. Concurrent misses share one request, so one active location consumes at most one Caiyun call per hour in the current single-container deployment.
- **Hourly Detail**: Shows hourly temperature, provider-reported feels-like temperature when available, precipitation probability/amount, humidity, wind speed, and optional UV index.
- **Visual Richness**:
  - Generates **Temperature + Feels-like** and **Probability + Rainfall Amount** trend charts (Matplotlib).
  - Uses **Emoji Reactions** (bot reacts to your messages).
  - Beautiful Markdown formatting with Indices (Life Suggestions).
- **Proactive Intelligence**:
  - **Rain Alerts**: Subscribe to locations; the bot checks every 5 minutes and alerts you if rain is approaching.
  - **Inline Mode**: Type `@botname Shanghai` in any chat to share weather cards.
  - **Interactive**: "Refresh" button to update weather instantly.

## 🛠 Tech Stack

- **Framework**: `python-telegram-bot 22.8` (JobQueue, Async, Persistence; typed support through Telegram Bot API 10.0).
- **Data**: `Pydantic v2` for robust data modeling.
- **Network**: `HTTPX` (HTTP/2 enabled).
- **Reliability**: `Tenacity` for smart retries, `Redis` (optional) or Pickle for persistence.

## 🚀 Quick Start

1.  **Configure**:
    - Rename `.env.example` to `.env`.
    - Fill in `BOT_TOKEN`, `QWEATHER_API_KEY`, and the QWeather root `QWEATHER_API_HOST`.
    - The recommended free-source ranges are `QWEATHER_HOURLY_HOURS=72h` and `QWEATHER_DAILY_DAYS=15d`.
    - Optional: set `ENABLE_CAIYUN_API=true` and `CAIYUN_API_TOKEN` to enable global Caiyun fusion. All weather-producing commands and jobs share the same one-hour coordinate cache; the refresh button only forces QWeather. Review [the command/API cost map](docs/commands-api-mapping.md) for the exact routing and cost ceiling.
    - The current Caiyun package does not include minute-level precipitation. QWeather `/v7/minutely/5m` remains the only minute source; hourly Caiyun precipitation is kept as intensity (`mm/h`), not relabeled as an hourly accumulation (`mm`).
2.  **Run**:

    ```bash
    # 1. 使用 uv 创建虚拟环境
    uv venv --python 3.12 .venv

    # 2. 安装依赖
    uv pip install --python .venv/bin/python -r requirements.txt

    # 3. 启动 Bot
    uv run python main.py
    ```

## 🤖 LLM Configuration

- Select provider with `LLM_PROVIDER=openai` or `LLM_PROVIDER=gemini`.
- OpenAI uses the official Python SDK `2.48.0` and defaults to `gpt-5.6-sol` through the Responses API.
- Gemini model is configured with `GEMINI_MODEL`.
- `LLM_MODEL` is still supported as a legacy fallback when provider-specific model values are not set.
- For GPT-5.6, tune reasoning with `OPENAI_REASONING_EFFORT=none|low|medium|high|xhigh|max` and output length tendency with `OPENAI_VERBOSITY=low|medium|high`; the migration baseline remains `medium`.
- OpenAI defaults to `OPENAI_API_MODE=responses`; set `OPENAI_API_MODE=chat_completions` only for compatible proxies or legacy flows.
- AI weather report style can be overridden with `LLM_WEATHER_REPORT_PROMPT` or `LLM_WEATHER_REPORT_PROMPT_FILE`; the built-in prompt already asks the LLM to use Telegram HTML, emoji headers, and blank-line sectioning.
- AI reports stream by default: the bot progressively edits the Telegram message while GPT generates (`LLM_STREAMING=true`). Gemini falls back to single-shot output.
- Reports are cached per location for `LLM_REPORT_CACHE_TTL_SECONDS` (default 4h, 0 disables); new weather alerts or rain onset invalidate the cache automatically.
- If AI reports feel slow, lower `OPENAI_REASONING_EFFORT`, set `OPENAI_VERBOSITY=low`, and set `OPENAI_MAX_OUTPUT_TOKENS`; `LLM_REPORT_TIMEOUT_SECONDS` (default 60s) controls when the bot gives up. Gemini HTTP timeout is configured with `GEMINI_TIMEOUT_SECONDS`.

## Telegram Bot API Compatibility

- Telegram's server API is currently 10.2 (2026-07-14). Bot API 10.1 introduced Rich Messages, while 10.2 added outgoing rich blocks and ephemeral messages.
- The latest stable `python-telegram-bot 22.8` only provides typed support through Bot API 10.0. Existing weather commands, photos, callbacks, Inline mode, webhooks, and scheduled jobs remain compatible with the newer Telegram server.
- Rich/ephemeral messages are intentionally not sent through private raw requests. The AI report continues to use supported Telegram HTML until PTB exposes the new objects and methods.
- See [the local Telegram Bot API compatibility audit](docs/telegram-bot-api-update-2026-07.md) for the exact version boundary and follow-up plan.

## 🐳 Docker Deploy (Recommended)

1.  **Configure `.env`** as above.
2.  **Run**:
    ```bash
    docker-compose up -d --build
    ```
3.  **Logs**:
    ```bash
    docker-compose logs -f
    ```

## 📝 Commands

- `/start` - Welcome message.
- `/tq <city>` - Query weather (e.g., `/tq Beijing`, `/tq Beijing 07-05`).
- `/tq <city> hourly 24` - Hourly temperature, native feels-like when available, and precipitation details.
- `/chart <city>` - View temperature/rain charts.
- `/report <city>` - **AI Weather Report** (Requires OpenAI/Gemini Key).
- `/rain_sub <city>` / `/rain_unsub <city>` / `/rain_my` - Manage rain alerts (`/rain_my` offers one-tap unsubscribe buttons).
- `/daily_sub <city> [HH:MM]` / `/daily_unsub <city>` / `/daily_my` - Manage daily brief subscriptions with an optional custom push time (default 08:00 in `TIMEZONE`).
- **Send Location** - Auto-query + Rain Chart.
- **Inline**: `@your_bot Beijing` - Share weather anywhere.

## 🔔 Subscriptions

- Click the **🔔 Subscribe Rain Alert** button under any weather message to enable 24/7 rain monitoring for that location. (Updates every 5 mins).
- Scheduled rain alerts and daily briefs can be toggled with `ENABLE_RAIN_ALERTS` and `ENABLE_DAILY_BRIEF`.
- Daily briefs fire at 08:00 in the `TIMEZONE` configured in `.env` (default `Asia/Shanghai`); each subscription can override this with `/daily_sub <city> HH:MM` (minute-level dispatcher).
- Rain alerts attach the hourly precipitation chart and respect `RAIN_ALERT_COOLDOWN_HOURS` (default 4h) per chat+location.
- Subscription locations are geocoded and normalized on subscribe; chats that block the bot are removed from push lists automatically.
- Ambiguous city names (e.g. 朝阳) prompt an inline chooser instead of silently picking the first match.

## 📚 API Notes

- [天气 API 本地文档索引与双源策略](docs/weather-api-index.md)
- [项目命令、API 调用与成本路由](docs/commands-api-mapping.md)
- [彩云天气当前套餐 API 整理](docs/caiyun-api-reference-2026-07.md)
- [和风天气完整 API 整理](docs/qweather-api-reference-2026-07.md)
- [和风天气 2026-07 更新核对](docs/qweather-api-update-2026-07.md)
- [Telegram Bot API 10.1/10.2 与 PTB 兼容性核对](docs/telegram-bot-api-update-2026-07.md)
- [OpenAI SDK 2.48 与 GPT-5.6 Sol 迁移记录](docs/openai-gpt-5p6-upgrade-2026-07.md)
