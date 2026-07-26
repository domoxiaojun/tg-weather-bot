# Telegram Bot API 10.1/10.2 与 PTB 兼容性核对

核对日期：2026-07-25

## 结论

项目可以升级客户端依赖，但目前不能诚实地宣称“已完整支持 Bot API 10.1/10.2”。Telegram 服务端已经更新到 10.2，`python-telegram-bot`（下文简称 PTB）最新稳定版 22.8 及其开发分支仍只声明完整支持 Bot API 10.0。

本轮将项目从 PTB 22.5 升级到 22.8。这会把类型化协议支持从 Bot API 9.2 提升到 10.0，并获得中间版本的修复与安全依赖更新。现有天气查询、图片、回调按钮、Inline、Webhook、持久化和 JobQueue 不依赖 10.1/10.2 新对象，可以继续正常调用 Telegram 的向后兼容服务端。

## 官方版本边界

| 层级 | 当前版本 | 日期 | 本项目状态 |
| --- | --- | --- | --- |
| Telegram Bot API 服务端 | 10.2 | 2026-07-14 | 现有方法可继续使用 |
| Telegram Bot API 10.1 | 10.1 | 2026-06-11 | 新增 Rich Messages 等能力 |
| PTB 最新稳定版 | 22.8 | 2026-06-12 | 完整支持到 Bot API 10.0 |
| PTB 开发分支 | 未发布 | 2026-07-25 核对 | 常量仍为 Bot API 10.0 |
| 本项目依赖 | PTB 22.8 | 本轮升级 | 与当前调用点兼容 |

官方资料：

- [Telegram Bot API changelog](https://core.telegram.org/bots/api-changelog)
- [PTB 22.8 changelog](https://docs.python-telegram-bot.org/en/v22.8/changelog.html)
- [PTB 22.8 release](https://github.com/python-telegram-bot/python-telegram-bot/releases/tag/v22.8)

## 10.1 新能力与项目价值

### Rich Messages

10.1 增加结构化富文本、表格、列表、引用、图片/视频等 Rich Block，并增加 `sendRichMessage`、`sendRichMessageDraft`。对本项目最有价值的方向是：

- `/report` 可以流式发送 AI 日报，而不是等待整篇生成完成。
- 日报可以使用结构化区块和表格，减少 Telegram HTML 手工转义。
- Inline 结果未来可以使用 `InputRichMessageContent`。

当前 PTB 22.8 没有 `RichMessage`、`InputRichMessage` 或 `Bot.send_rich_message`，因此本轮继续使用已测试的 Telegram HTML。直接拼接原始 HTTP 请求会绕过 PTB 的对象序列化、错误类型、请求参数校验与后续兼容处理，不适合作为默认生产链路。

### Join Request Queries 与 Polls

10.1 还增加入群请求查询、入群 Web App 与投票链接媒体。本项目没有入群守卫、Web App 或投票功能，当前没有接入收益。

## 10.2 新能力与项目价值

- Rich Messages 增加可发送的输入区块和媒体。
- Ephemeral Messages 可在群聊中向单个用户显示临时回复，未来适合 `/tq`、设置与订阅管理，减少群聊刷屏。
- Communities、订阅状态更新与 Mini App 域名加固目前与天气机器人主链路无直接关系。

这些对象和方法同样尚未进入 PTB 22.8/当前开发分支，本轮不实现私有兼容层。

## 22.5 → 22.8 兼容审计

- PTB 22.6 移除 Python 3.9 支持；项目使用 Python 3.12，不受影响。
- PTB 22.7 移除 Bot API 9.3 已废弃的礼物相关字段/参数；项目没有礼物、支付或相关调用。
- PTB 22.8 对 `InputMedia*` 的位置参数 `filename` 发出弃用提示；项目现有 `InputMediaPhoto(media=..., caption=...)` 和 `InputFile(..., filename=...)` 都使用关键字参数。
- Bot API 9.6/10.0 的 Poll 新必填字段不影响项目，因为项目不创建或解析投票。
- PTB 22.8 包含 `cryptography`、`tornado` 等安全依赖更新；使用 `[all]` 安装时会一并解析兼容版本。

## 后续接入条件

当 PTB 稳定版的 `telegram.constants.BOT_API_VERSION_INFO` 达到 `(10, 2)` 后，再按以下顺序实施：

1. 升级 PTB 并跑现有完整测试与 Bot 工厂烟测。
2. 给 Rich Messages 增加显式功能开关，保留 Telegram HTML 降级路径。
3. 先在 `/report` 接入 `sendRichMessageDraft`，验证流式更新、超时、取消和最终消息落盘。
4. 再评估群聊查询/订阅管理是否使用 Ephemeral Messages。
5. 更新 `allowed_updates`、持久化兼容测试和本文档。

在此之前，Telegram 服务端版本高于 PTB 声明版本并不代表现有命令失效；Bot API 对旧方法保持向后兼容。
