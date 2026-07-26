# Telegram Bot API 10.1/10.2 接入记录（Rich / Ephemeral）

首次核对：2026-07-25 · 实施与更新：2026-07-26

## 结论

项目**已接入** Bot API 10.1/10.2 的富文本与 ephemeral 消息，方式是在 `python-telegram-bot 22.8` 之上做一层轻量封装（`services/telegram_rich.py`），而不是更换 SDK。

- Telegram 服务端：Bot API 10.2（2026-07-14）
- PTB 稳定版 22.8：typed 支持仅到 Bot API 10.0（10.1 支持仍为 upstream open issue）
- 接入通道：`telegram.Bot.do_api_request()` —— PTB **公开且文档化**的"调用尚未封装的新方法"入口（`versionadded:: 20.8`），并非私有 HTTP 拼接；已有 typed 方法（如 `sendMessage`）的新参数则走官方 `api_kwargs`
- 富文本默认开启（`ENABLE_RICH_MESSAGES=true`），任何失败自动降级回 MarkdownV2/HTML

不迁移到 aiogram（它已完整支持 10.2）的原因：需要重写全部 handler、JobQueue 调度、PicklePersistence 订阅数据与 122 项测试，并迁移线上用户数据，代价远超收益。

## 传输层要点（已核对 PTB 22.8 源码）

| 事项 | 结论 | 依据 |
| --- | --- | --- |
| 入口签名 | `do_api_request(endpoint, api_kwargs=None, return_type=None, *, timeouts...)` | `telegram/_bot.py:886` |
| 嵌套结构序列化 | 非 `str` 值由 PTB 自动 `json.dumps`，可直接传 dict/list | `telegram/request/_requestparameter.py:71-80` |
| 未知方法异常 | 服务端返回 404 → PTB 抛 **`telegram.error.EndPointNotFound`**（bot 已初始化时） | `telegram/_bot.py:967-981` |
| 端点名 | `to_camel_case` 处理，`sendRichMessage` 与 `send_rich_message` 均可 | `telegram/_bot.py:957` |
| 返回值 | 传 `return_type=Message` 可直接拿到 `Message` 对象 | `telegram/_bot.py:983+` |
| 已封装端点的警告 | 对 `editMessageText` 会发 `PTBUserWarning`；封装层已定向抑制并注明原因 | `telegram/_bot.py:948-955` |

降级判定策略：`EndPointNotFound` → 永久禁用该能力；连续 3 次 `BadRequest` → 禁用（载荷错误是确定性的，不必每条消息都白付一次往返）；`Forbidden` → 聊天级问题，不影响能力；网络类错误 → 只本次降级。

## 已核对的线格式（Bot API 10.2 官方规格）

这些字段名是照着官方机器可读规格逐个核对的——网页摘要曾给出 `InputRichMessage{text, parse_mode}` 这样的**错误**结构，实际并不存在。

**`InputRichMessage`**：`blocks` | `html` | `markdown`（三者取一）、`media`、`is_rtl`、`skip_entity_detection`。**没有** `text`/`parse_mode`。

**块类型判别串**：`paragraph`、`heading`(+`size` 1-6)、`pre`(+`language`)、`footer`、`divider`、`table`、`list`、`blockquote`、`details`、`thinking`、`photo`、`collage`、`slideshow`、`map`、`animation`、`audio`、`video`、`voice_note`、`math`、`anchor`、`pullquote`。

**`RichBlockTableCell`**：`align`（`left`/`center`/`right`）与 `valign`（`top`/`middle`/`bottom`）**必填**；`text`/`is_header`/`colspan`/`rowspan` 可选。

**`RichText`**：可以是纯 `String`、`Array of RichText`，或 `{type:"bold"|"italic"|"code"|"url"|"marked"|...}`。**不需要 MarkdownV2 转义**。

**`sendRichMessageDraft`**：`chat_id` 仅限**私聊**且为 Integer，`draft_id` 非零（同 id 的更新会做动画），返回 `True`。草稿是**30 秒临时预览、不会留存**，必须在生成结束后再调 `sendRichMessage` 落地。

**Ephemeral（10.2）**：`sendMessage`/`sendPhoto` 等发送方法新增 `receiver_user_id`（**仅群/超级群**）与 `callback_query_id`；配套 `editEphemeralMessageText`/`deleteEphemeralMessage`（需 `chat_id`+`receiver_user_id`+`ephemeral_message_id`）；`BotCommand.is_ephemeral`；`Message.receiver_user`/`Message.ephemeral_message_id`。官方明确"不保证离线用户能收到"，因此所有调用都必须能降级。

## 本项目的使用位置

| 面向用户的表面 | 富文本处理 |
| --- | --- |
| 实时/默认视图 | heading + 主行 + 2 列统计表 + 预警 blockquote + 可折叠"今日详情" + footer |
| 逐小时视图 | 表格（时间/天气/温度/体感/降概/降水），跨天插入 `colspan` 整行日期分隔 |
| 逐日视图 | 表格（日期/天气/气温/降水/UV）+ 可折叠逐日文字描述 |
| 生活指数 | 分组 heading + 列表 |
| 降水视图 | 表格 + 峰值 `marked` 高亮 |
| AI 日报（私聊） | `sendRichMessageDraft` 原生流式（`thinking` 块）→ `sendRichMessage` 落地 |
| AI 日报（群/inline） | 占位消息 + 节流编辑，最终 `editMessageText(rich_message=...)` |
| 降雨提醒推送 | 图表以 `photo` 块嵌入同一条富消息，绕开 1024 字符 caption 上限 |
| 早安简报 | `sendRichMessage(html=...)` |
| 群内订阅管理回复 | ephemeral 消息（`receiver_user_id`），并给相关命令标记 `is_ephemeral` |

## 暂未接入

- **Inline 查询结果**（`InputRichMessageContent`）：`answerInlineQuery` 已由 PTB 封装为 typed 对象，塞入原始 dict 会破坏其序列化；改造需手写整个 `answerInlineQuery` 调用。用户选中后的那次编辑已经是富文本。
- Communities、Join Request Queries、Polls 新增能力：与天气机器人主链路无关。
- `editEphemeralMessage*` / `deleteEphemeralMessage`：当前 ephemeral 消息都是一次性回复，无需再编辑。

## 部署核对项

- BotFather `/setinlinefeedback` 必须为 Enabled(100%)，否则 inline AI 日报占位消息不会被替换。
- 若使用第三方 Bot API 代理，需确认其已支持 10.2；不支持时首次调用会触发 `EndPointNotFound`，日志会打印一次 warning 后永久降级，功能不受影响。
- 富文本渲染效果依赖客户端版本；旧客户端由 Telegram 服务端负责回退展示。
