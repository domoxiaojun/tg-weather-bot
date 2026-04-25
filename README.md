# DomoWeather Bot (Next-Gen)

A powerful, dual-engine Telegram Weather Bot built with Python 3.12+ and optimal architecture.

## 🌟 Features

- **QWeather-first Accuracy**: Uses QWeather's new full-path APIs for core weather, minutely precipitation, air quality, and alerts. Caiyun can be enabled as an optional minutely-rain enhancer.
- **Visual Richness**:
  - Generates **Rainfall Trend Charts** (Matplotlib) when rain is detected.
  - Uses **Emoji Reactions** (bot reacts to your messages).
  - Beautiful Markdown formatting with Indices (Life Suggestions).
- **Proactive Intelligence**:
  - **Rain Alerts**: Subscribe to locations; the bot checks every 5 minutes and alerts you if rain is approaching.
  - **Inline Mode**: Type `@botname Shanghai` in any chat to share weather cards.
  - **Interactive**: "Refresh" button to update weather instantly.

## 🛠 Tech Stack

- **Framework**: `python-telegram-bot` (JobQueue, Async, Persistence).
- **Data**: `Pydantic v2` for robust data modeling.
- **Network**: `HTTPX` (HTTP/2 enabled).
- **Reliability**: `Tenacity` for smart retries, `Redis` (optional) or Pickle for persistence.

## 🚀 Quick Start

1.  **Configure**:
    - Rename `.env.example` to `.env`.
    - Fill in `BOT_TOKEN`, `QWEATHER_API_KEY`, and the QWeather root `QWEATHER_API_HOST`.
    - Optional: set `ENABLE_CAIYUN_API=true` and `CAIYUN_API_TOKEN` to prefer Caiyun for minute-level rain.
2.  **Run**:

    ```bash
    # 1. 创建虚拟环境
    python -m venv .venv

    # 2. 安装依赖
    .venv\Scripts\pip install -r requirements.txt

    # 3. 启动 Bot
    .venv\Scripts\python main.py
    ```

## 🤖 LLM Configuration

- Select provider with `LLM_PROVIDER=openai` or `LLM_PROVIDER=gemini`.
- OpenAI model is configured with `OPENAI_MODEL` (default example: `gpt-5.5`).
- Gemini model is configured with `GEMINI_MODEL`.
- `LLM_MODEL` is still supported as a legacy fallback when provider-specific model values are not set.
- For GPT-5.5, tune reasoning with `OPENAI_REASONING_EFFORT=none|minimal|low|medium|high|xhigh` and output length tendency with `OPENAI_VERBOSITY=low|medium|high`.
- OpenAI defaults to `OPENAI_API_MODE=responses`; set `OPENAI_API_MODE=chat_completions` only for compatible proxies or legacy flows.
- AI weather report style can be overridden with `LLM_WEATHER_REPORT_PROMPT` or `LLM_WEATHER_REPORT_PROMPT_FILE`; the built-in prompt already asks the LLM to use Telegram HTML, emoji headers, and blank-line sectioning.
- If AI reports feel slow, lower `OPENAI_REASONING_EFFORT`, set `OPENAI_VERBOSITY=low`, and reduce `OPENAI_MAX_OUTPUT_TOKENS`; `LLM_REPORT_TIMEOUT_SECONDS` controls when the bot gives up.

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
- `/tq <city>` - Query weather (e.g., `/tq Beijing`).
- `/chart <city>` - View temperature/rain charts.
- `/report <city>` - **AI Weather Report** (Requires OpenAI/Gemini Key).
- **Send Location** - Auto-query + Rain Chart.
- **Inline**: `@your_bot Beijing` - Share weather anywhere.

## 🔔 Subscriptions

- Click the **🔔 Subscribe Rain Alert** button under any weather message to enable 24/7 rain monitoring for that location. (Updates every 5 mins).
