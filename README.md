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
  - **Rain Alerts**: Subscribe to locations; the bot checks on a configurable interval (`RAIN_CHECK_INTERVAL_MINUTES`, default 30) and alerts once per rain episode when rain is approaching.
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

## Rich Messages (Bot API 10.1/10.2)

Rich output is **enabled by default** (`ENABLE_RICH_MESSAGES=true`) through a thin wrapper over PTB's public `Bot.do_api_request` escape hatch — `python-telegram-bot 22.8` is typed only through Bot API 10.0, so the newer methods are called directly while every surface keeps its MarkdownV2/HTML fallback.

- **Tables** for the hourly and daily views (real column alignment instead of emoji-prefixed text runs), with a full-width date separator row when the hourly table crosses midnight.
- **Compact weather-card hierarchy**: realtime observations and daily details use separate two-column tables; all available life indices sit in their own two-column section immediately before air quality.
- **Warnings without background paint**: official warning summaries use a warning glyph plus bold text, while `marked` remains reserved for precipitation peaks.
- **One-message `/tq` charts**: the automatically selected chart is embedded as a Rich `photo` block, including first-time multipart uploads; refresh and view switches edit the same message.
- **Native streaming** for AI reports in private chats via `sendRichMessageDraft` (an animated 30-second preview using the `thinking` block), finalized with `sendRichMessage`; groups and inline messages use throttled rich edits instead.
- **Rich Inline Mode results** via `InputRichMessageContent`, including weather tables, the help card, life indices and the AI-report placeholder.
- **Guest Mode** (`ENABLE_GUEST_MODE=true`): mention the bot in a chat it has not joined and it replies once with the same rich weather card via `answerGuestQuery`.
- **Rain alerts stay Rich even with charts**: cached charts embed as a `photo` block; a first-time upload is sent as a visual lead followed by the full Rich alert card.
- **Ephemeral messages** (`ENABLE_EPHEMERAL_MESSAGES=true`): in groups, subscription management replies are visible only to the requesting user, and those commands are registered with `is_ephemeral`.
- Unsupported servers/proxies are detected once (`EndPointNotFound`) and the capability is disabled for the process — functionality degrades, nothing breaks.

See [the local Bot API 10.1/10.2 integration record](docs/telegram-bot-api-update-2026-07.md) for the verified wire format, PTB 22.8 transport audit, Inline/Guest implementation and deliberate ephemeral boundary.

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
- `/typhoon [city]` - Active tropical cyclones and whether they reach that location (basin NP only).
- `/tide [coastal city]` - Nearest tide station's high/low tide table plus the tide curve.
- `/rain_sub <city> [level]` / `/rain_unsub <city>` / `/rain_my` - Manage rain alerts. Each subscription
  carries its own sensitivity: 全部降雨 (any measurable rain) / 一般降雨 (default, skips drizzle) /
  仅大雨 (≥8mm/h only, and never on probability alone). `/rain_my` opens a card (rich blocks in private
  chats) showing per-city level, last alert time and quiet-hours state, with buttons to change level,
  unsubscribe, flip to the daily card, or one-tap subscribe the last queried city.
- `/daily_sub <city> [HH:MM]` / `/daily_unsub <city>` / `/daily_my` - Manage daily brief subscriptions with an optional custom push time (default 08:00 in `TIMEZONE`). `/daily_my` is a card too: quick-pick time buttons (06:30/07:00/07:30/08:00) per city, unsubscribe, and a flip to the rain card. The weather card offers both `🔔 降雨提醒` and `📅 早安简报` one-tap subscribe buttons.
- **Send Location** - Auto-query + Rain Chart.
- **Inline**: `@your_bot Beijing` - Share weather anywhere.
- **Guest**: mention `@your_bot Beijing` in a chat where the bot is not a member; the bot replies as itself once.

## 🔔 Subscriptions

- Click the **🔔 Subscribe Rain Alert** button under any weather message to enable rain monitoring for that location (checked every `RAIN_CHECK_INTERVAL_MINUTES`, default 30).
- Scheduled rain alerts and daily briefs can be toggled with `ENABLE_RAIN_ALERTS` and `ENABLE_DAILY_BRIEF`.
- Daily briefs fire at 08:00 in the `TIMEZONE` configured in `.env` (default `Asia/Shanghai`); each subscription can override this with `/daily_sub <city> HH:MM` (minute-level dispatcher).
- **Official weather warnings are pushed proactively** to rain-alert subscribers (`ENABLE_ALERT_PUSH`, checked every `ALERT_CHECK_INTERVAL_MINUTES`, default 10). Each warning revision (`alert_id` + publish time) alerts once; levels in `ALERT_QUIET_HOURS_EXEMPT_LEVELS` (default 红色/橙色) bypass quiet hours because they are life-safety information. Non-exempt warnings are *held*, not dropped, and land once the window closes.
- **Tropical cyclone alerts** (`ENABLE_TYPHOON_ALERTS`, basin `NP` only — that is all QWeather covers): severity comes from **wind-circle membership**, not raw distance. Being inside the 30/50/64-knot circle (Beaufort 7/10/12) maps to 黄色/橙色/红色, and the quadrant radius is picked by bearing from the storm centre, so an alert means the wind really reaches you. Storms with no reported radii fall back to a distance watch (`TYPHOON_WATCH_DISTANCE_KM`). Re-alerts happen on escalation (a tighter circle or a closer 100 km bucket), never on every check. `/typhoon [城市]` queries active storms on demand.
- **Derived event alerts** (`ENABLE_DERIVED_EVENT_ALERTS`): air-quality deterioration (`AQI_ALERT_THRESHOLD`), extreme temperature (`HIGH_TEMP_ALERT_THRESHOLD` / `LOW_TEMP_ALERT_THRESHOLD`) and strong wind (`WIND_ALERT_SCALE_THRESHOLD`), each fired at most once per day per location.
- Rain alerts are **episode-based per subscription**: one alert when rain becomes imminent, silence until that episode ends; `RAIN_ALERT_COOLDOWN_HOURS` (default 4h) additionally guards against flapping. Quiet hours (`RAIN_ALERT_QUIET_HOURS`, default 23:00-07:00) are evaluated **in each subscription's own timezone** and suppress the send without recording the episode, so the alert still arrives afterwards.
- Rain alerts attach the **minute-level precipitation chart** when 5-minute data is available (matching what actually triggered them), falling back to the hourly chart otherwise.
- Scheduling is **per-location**: `/daily_sub 东京 07:30` fires at 07:30 Tokyo time, not at the global `TIMEZONE`. Briefs missed while the bot was down are re-sent within `DAILY_BRIEF_CATCHUP_HOURS` (default 2h).
- Subscribing from a **forum topic** remembers the topic, so pushes land there instead of General.
- Each chat can subscribe up to `MAX_SUBSCRIPTIONS_PER_CHAT` (default 3) cities per subscription type; rain alerts attach the hourly precipitation chart.
- Subscription locations are geocoded and normalized on subscribe; chats that block the bot are removed from push lists automatically.
- Ambiguous city names (e.g. 朝阳) prompt an inline chooser instead of silently picking the first match.

## 🔐 QWeather Authentication (API Key or Ed25519 JWT)

QWeather accepts either a long-lived API key or a short-lived Ed25519-signed JWT. The API key is **not** removed, but its daily request volume gets limited from 2027-01-01, so JWT is the better long-term choice.

```bash
uv run python scripts/generate_qweather_key.py     # writes secrets/ (gitignored)
```

Upload the printed **public** key to the QWeather console, then set the Credential ID and Project ID it returns:

```env
QWEATHER_JWT_PRIVATE_KEY_FILE=secrets/qweather_ed25519_private.pem
QWEATHER_JWT_KID=<Credential ID>
QWEATHER_JWT_SUB=<Project ID>
```

`QWEATHER_AUTH_MODE=auto` (the default) signs JWTs as soon as those three are present and keeps using the API key otherwise, so migration needs no code change. Tokens are cached and re-signed 60s before expiry (`QWEATHER_JWT_TTL_SECONDS`, default 900, max 86400). Set `jwt` to make a misconfiguration fail loudly, or `api_key` to pin the old scheme.

## 🧭 Discovery & buttons

- The bot registers **scoped command lists**: private chats get `/start` plus everything, groups get a shorter menu (the `/start` location keyboard is private-only). Personal bookkeeping commands carry the Bot API 10.2 `is_ephemeral` flag.
- `setMyShortDescription` / `setMyDescription` are set on startup, so the profile page explains the bot before anyone presses Start.
- Semantic buttons are colour-coded with the Bot API 10.x `style` field (subscribe = green, unsubscribe = red, AI report = blue); neutral navigation stays uncoloured. Older clients ignore the field.
- Weather cards carry a **📤 分享** button (`switch_inline_query`) that opens a chat picker and shares the card through inline mode.

## ⚙️ BotFather Checklist

- **Inline mode**: enable via `/setinline`.
- **Inline feedback**: set `/setinlinefeedback` to **Enabled (100%)** — the inline AI report relies on `chosen_inline_result` to replace its placeholder message; without feedback the placeholder never updates.
- **Guest Mode**: open the BotFather MiniApp → Bot Settings → **Guest Mode** and enable it. Code startup logs whether Telegram reports `supports_guest_queries=true`.

## 📚 API Notes

- [天气 API 本地文档索引与双源策略](docs/weather-api-index.md)
- [项目命令、API 调用与成本路由](docs/commands-api-mapping.md)
- [彩云天气当前套餐 API 整理](docs/caiyun-api-reference-2026-07.md)
- [和风天气完整 API 整理](docs/qweather-api-reference-2026-07.md)
- [和风天气 2026-07 更新核对](docs/qweather-api-update-2026-07.md)
- [和风图标 → Telegram 自定义 Emoji](docs/weather-custom-emoji.md)（映射：`resources/weather_custom_emoji.json`，Docker `./data` 卷可覆盖）
- [Telegram Bot API 10.1/10.2 与 PTB 兼容性核对](docs/telegram-bot-api-update-2026-07.md)
- [OpenAI SDK 2.48 与 GPT-5.6 Sol 迁移记录](docs/openai-gpt-5p6-upgrade-2026-07.md)
