# Rich 全链路与 Guest Mode 升级计划

- ✅ 1. 审计 Bot API 10.2 官方规格与仓库全部发送/编辑链路，明确 Rich、Inline、Guest、Ephemeral 的能力边界。
- ✅ 2. 将 Inline Mode 的天气结果、使用说明与 AI 日报占位升级为 `InputRichMessageContent`，并保留独立能力探测和纯文本降级。
- ✅ 3. 接入 Guest Mode：识别 `guest_message`，解析提及后的天气请求，并通过 `answerGuestQuery` 返回 Rich InlineQueryResult；失败时纯文本降级。
- ✅ 4. 收口其余可使用 Rich 的天气卡、订阅卡、日报与推送路径；保留无法使用 Rich 的 ephemeral、纯图表和错误兜底。
- ✅ 5. 增加回归测试、更新 Bot API/README/环境配置说明，并完成 Ruff、编译与相关测试验证。

## 已确认的能力边界

- `InputRichMessageContent.rich_message` 可用于 Inline、Guest 和 Web App 查询结果。
- Guest Mode 由 Bot API 10.0 提供，需在 BotFather MiniApp 中手动开启；代码无法替用户切换该外部设置。
- Guest Bot 收到 `Update.guest_message` 后只有一次回复机会，必须调用 `answerGuestQuery(guest_query_id, result)`。
- `sendRichMessage` 没有 `receiver_user_id` / `callback_query_id`，因此群内仅个人可见的 ephemeral 回复不能无损升级为 Rich。
