# 和风天气图标 → Telegram 自定义 Emoji

## 目标

把天气图标从 Unicode emoji 映射，升级为 Telegram **Custom Emoji**（和风官方 SVG 转 100×100 静态图），在 Rich / HTML / MarkdownV2 全路径可用；无映射或失败时回退 emoji。

## 前置条件（需人工）

- [ ] Bot 所有者账号开通 **Telegram Premium**（Bot API 9.4：私聊/群/超级群可直接发 custom emoji）
- [ ] 所有者用户已向 Bot 发过 `/start`（`createNewStickerSet` 要求）
- [ ] 安装 SVG 光栅化依赖后再跑上传脚本（`cairosvg` + 系统 Cairo；执行前会再确认）

## 实现任务

- [x] 抽取 `utils/weather_icons.py`：emoji 表、custom_emoji_id 映射加载、plain/html/md/rich 解析
- [x] `services/telegram_rich.py` 增加 `custom_emoji()` RichText 构建
- [x] `utils/rich_formatter.py` 全部天气图标改为 RichText custom_emoji（表格/标题/日夜）
- [x] `utils/formatter.py` MarkdownV2/HTML 回退路径支持 `tg-emoji` / `![…](tg://emoji?id=…)`
- [x] 彩云 `skycon` 改为存和风 icon code（不再直接存 emoji），以便统一映射
- [x] `core/config.py` + `.env.example`：开关与映射文件路径
- [x] `scripts/prepare_weather_emoji_assets.py`：下载和风 SVG → 100×100 PNG
- [x] `scripts/upload_weather_emoji.py`：创建/更新 custom_emoji sticker set，写出映射 JSON
- [x] 单元测试：有映射 / 无映射 / 回退
- [x] 文档简短说明（README 或 docs）
- [x] 本地 unittest + compileall；提交 git（`81ae4d1`）
- [x] 固定上传顺序 + `import_weather_emoji_md.py`（你贴 MDV2 即可生成映射）
- [ ] （人工）按 `data/weather_emoji_order.md` 建包 → 贴 MarkdownV2 → import

## 范围说明

- 上传约 70 个项目实际使用的和风 code（`WEATHER_ICONS` 全集），远低于 custom emoji pack 上限 200
- **推荐**：你建包后给 MarkdownV2，用 import 脚本写 JSON；**不强制** cairosvg
- 映射文件默认 `data/weather_custom_emoji.json`；未生成前行为与现在一致（emoji）
