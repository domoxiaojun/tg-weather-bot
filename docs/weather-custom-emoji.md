# 和风天气图标 → Telegram 自定义 Emoji

把 QWeather 的 icon code（`100`/`301`/…）渲染成 Telegram **Custom Emoji**。无映射时自动回退 Unicode emoji，行为与改造前一致。

## 能力边界

| 路径 | 渲染方式 |
| --- | --- |
| Rich 消息 | `RichTextCustomEmoji`（`type: custom_emoji`） |
| HTML | `<tg-emoji emoji-id="…">☀️</tg-emoji>` |
| MarkdownV2 | `![☀️](tg://emoji?id=…)` |
| 图表/纯文本 | 仍用 Unicode emoji |

**前置条件（Bot API 9.4，2026-02-09）**

- Bot **所有者**开通 Telegram Premium 后，Bot 可在**私聊 / 群 / 超级群**直接发送 custom emoji。
- 频道、以及未满足 Premium/Fragment 条件的场景会失败或回退；本项目映射缺失时始终回退 emoji。
- 创建 sticker set 时，所有者必须先给 Bot 发过 `/start`。

## 一键生成流程

```bash
# 1) 安装光栅化依赖（需先确认；系统 Cairo + Python 包）
# macOS: brew install cairo pkg-config
# uv pip install --python .venv/bin/python cairosvg

# 2) 下载和风 SVG 并输出 100×100 PNG（MIT 图标库）
uv run python scripts/prepare_weather_emoji_assets.py

# 3) 上传为 custom_emoji sticker set，写出映射
# 需要 .env 里 BOT_TOKEN + SUPER_ADMIN_ID（所有者数字 ID）
uv run python scripts/upload_weather_emoji.py

# 4) 确认映射文件存在后重启 Bot
# data/weather_custom_emoji.json
```

配置（`.env`）：

```env
ENABLE_CUSTOM_WEATHER_EMOJI=true
# WEATHER_CUSTOM_EMOJI_MAP_PATH=data/weather_custom_emoji.json
```

## 映射文件格式

见 `data/weather_custom_emoji.json.example`：

```json
{
  "version": 1,
  "sticker_set_name": "qweather_icons_by_YourBot",
  "icons": {
    "100": "5368…",
    "301": "5368…"
  }
}
```

当前会上传 `utils/weather_icons.py` 里 `WEATHER_ICONS` 的全部 code（约 70 个），低于 custom emoji pack 上限 200。

## 彩云兼容

Caiyun `skycon` 在 adapter 里已映射为**和风 code**（不再直接塞 emoji），这样 fusion 补全字段时也能走同一套 custom emoji。

## 许可证

图标 SVG 来自 [QWeather Icons](https://icons.qweather.com/)（MIT）。上传到 Telegram 自定义表情包时请自行遵守 Telegram 与 MIT 条款。
