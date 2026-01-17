# DomoWeather Bot (Next-Gen)

A powerful, dual-engine Telegram Weather Bot built with Python 3.12+ and optimal architecture.

## 🌟 Features

- **Dual Engine Accuracy**: Combines **QWeather** (General data) + **Caiyun** (Minute-level rain forecast).
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
    - Fill in `BOT_TOKEN`, `QWEATHER_API_KEY`, `CAIYUN_API_TOKEN`.
2.  **Run**:

    ```bash
    # 1. 创建虚拟环境
    python -m venv .venv

    # 2. 安装依赖
    .venv\Scripts\pip install -r requirements.txt

    # 3. 启动 Bot
    .venv\Scripts\python main.py
    ```

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
