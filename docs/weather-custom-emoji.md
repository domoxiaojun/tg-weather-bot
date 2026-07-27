# 和风天气图标 → Telegram 自定义 Emoji

把 QWeather 的 icon code（`100`/`301`/…）渲染成 Telegram **Custom Emoji**。无映射时自动回退 Unicode emoji，行为与改造前一致。

## cairosvg 是什么？

**可选工具，不是 Bot 运行依赖。**

- 和风开源图标是 **SVG**（矢量）
- Telegram 自定义 emoji 要 **100×100 的 PNG/WEBP**
- [cairosvg](https://cairosvg.org/) = 用系统 [Cairo](https://www.cairographics.org/) 把 SVG 画成 PNG 的 Python 库

只有跑 `scripts/prepare_weather_emoji_assets.py` 时才需要它。  
若你在别的地方已经做好 100×100 图、或自己用 Stickers 机器人建包，**完全不用装 cairosvg**。运行时 Bot 只读 `data/weather_custom_emoji.json` 里的 id。

## 推荐流程（你建包 → 给我 MarkdownV2）

1. **按固定顺序**往表情包里加图标（见 `data/weather_emoji_order.md`，与 `UPLOAD_ICON_CODES` 一致）  
   顺序：白天晴云 → 夜间晴云 → 雨 → 雪 → 雾霾沙尘 → 其它。
2. 建包成功后，把**整包**导出/复制成 MarkdownV2，形如：

   ```
   ![☀️](tg://emoji?id=5368324170671202286)![🌤️](tg://emoji?id=…)…
   ```

3. 导入映射（按出现顺序对齐 code）：

   ```bash
   uv run python scripts/import_weather_emoji_md.py pack.md
   # 或
   pbpaste | uv run python scripts/import_weather_emoji_md.py -
   ```

4. 确认生成 `data/weather_custom_emoji.json`，**并复制到** `resources/weather_custom_emoji.json`（随镜像打包、不被 `./data` 卷盖住），然后重启 Bot。  
   开关默认开：`ENABLE_CUSTOM_WEATHER_EMOJI=true`。

运行时查找顺序：`WEATHER_CUSTOM_EMOJI_MAP_PATH` → `data/…` → `resources/…`。  
Docker Compose 把 `./data` 挂到 `/app/data` 时，请把映射放在**宿主机** `./data/weather_custom_emoji.json`，或依赖镜像内 `resources/` 兜底。

顺序错了 id 会对错天气；**务必 1…N 与清单一致**。

## 可选：本机自动出图 + API 上传

```bash
# 仅准备 PNG 时需要（确认后再装）
# brew install cairo pkg-config
# uv pip install --python .venv/bin/python cairosvg

uv run python scripts/prepare_weather_emoji_assets.py
# 产出 data/weather_emoji_assets/01_100.png … 与 ORDER.txt

uv run python scripts/upload_weather_emoji.py   # 需 Premium + BOT_TOKEN + SUPER_ADMIN_ID
```

## 能力边界

| 路径 | 渲染方式 |
| --- | --- |
| Rich 消息 | `RichTextCustomEmoji`（`type: custom_emoji`） |
| HTML | `<tg-emoji emoji-id="…">☀️</tg-emoji>` |
| MarkdownV2 | `![☀️](tg://emoji?id=…)` |
| 图表/纯文本 | 仍用 Unicode emoji |

**前置条件（Bot API 9.4）**

- Bot **所有者** Premium → 私聊/群/超群可发 custom emoji
- 映射缺失时始终回退 Unicode emoji

## 映射文件格式

见 `data/weather_custom_emoji.json.example`。约 70 个 code，低于 pack 上限 200。

## 语义（code → 中文现象）

渲染层只需要 `code → custom_emoji_id`；**含义**在 `utils/weather_icons.py` 的 `WEATHER_ICON_LABELS`（和风官方语义）：

| 用途 | 来源 |
| --- | --- |
| Bot UI Rich | `weather_icon_rich(code)` |
| Bot UI MarkdownV2 | `weather_icon_md(code)` → `![☀️](tg://emoji?id=…)`（**勿**再 `escape_v2`） |
| Bot UI HTML | `weather_icon_html(code)` → `<tg-emoji emoji-id="…">` |
| LLM 日报 | `weather_icon_code` / `weather_icon_label` + `weather_icon_legend` |
| 人读 / 粘贴整包 | `data/weather_emoji_order.md`、`resources/weather_custom_emoji.markdown_v2.txt` |

彩色包 short name：`qweather_color_by_domoweather_bot`  
添加：https://t.me/addemoji/qweather_color_by_domoweather_bot  

栅格化时会把官方 SVG 的 `currentColor` 换成分类色（晴金 / 雨蓝 / 雪冰蓝 / 预警橙…），避免黑剪影。

映射 JSON（`data/` 与 `resources/`）在 v2 起额外带：

```json
{
  "icons": { "100": "6287…" },
  "labels": { "100": "晴" },
  "emoji": { "100": "☀️" },
  "markdown_v2": { "100": "![☀️](tg://emoji?id=6287…)" },
  "html": { "100": "<tg-emoji emoji-id=\"6287…\">☀️</tg-emoji>" }
}
```

运行时仍只读 `icons`；`markdown_v2` / `html` 给工具与文档。重新导出：

```bash
uv run python scripts/export_weather_emoji_formats.py
```

LLM **不必**也不会拿到 custom emoji id；正文用中文现象描述，图标由 Bot 按 code 渲染。

## 彩云兼容

Caiyun `skycon` 在 adapter 里映射为**和风 code**，与 custom emoji 共用一张表。

## 许可证

图标 SVG 来自 [QWeather Icons](https://icons.qweather.com/)（MIT）。
