# OpenAI SDK 2.48 与 GPT-5.6 Sol 迁移记录

核对日期：2026-07-25

## 结论

项目已将 OpenAI Python SDK 精确锁定为当前最新版 `2.48.0`，活动默认模型从 `gpt-5.5` 升级为 `gpt-5.6-sol`。继续使用 Responses API、`medium` 推理强度、`medium` verbosity 和现有天气日报提示词，不在基线迁移中启用 Pro、持久化推理、显式缓存、Programmatic Tool Calling 或 Multi-agent。

官方资料：

- [Using GPT-5.6](https://developers.openai.com/api/docs/guides/latest-model)
- [Upgrading to GPT-5.6 Sol](https://developers.openai.com/api/docs/guides/upgrading-to-gpt-5p6-sol)
- [GPT-5.6 prompting guidance](https://developers.openai.com/api/docs/guides/prompt-guidance-gpt-5p6)

## 当前用法清单

| 使用点 | 旧配置 | 角色与接口 | 迁移结果 |
| --- | --- | --- | --- |
| `OpenAIProvider` 默认值 | `gpt-5.5` | 生成 260–420 字天气日报；Responses API | `gpt-5.6-sol` |
| `LLMService` OpenAI fallback | `gpt-5.5` | 未显式设置 `OPENAI_MODEL/LLM_MODEL` 时生效 | `gpt-5.6-sol` |
| `.env.example` | `OPENAI_MODEL=gpt-5.5` | 部署示例 | `gpt-5.6-sol` |
| Chat Completions fallback | 同一模型配置 | 只用于兼容代理，无工具调用 | 保留，不改接口结构 |
| Gemini 路由 | `gemini-2.5-flash` | 独立供应商 | 不变 |

项目没有 OpenAI 工具调用、会话续传、图片/PDF 输入、结构化输出、模型路由器、显式 prompt cache 或定价注册表，因此属于简单 Sol 迁移。

## 行为保持

- 旧配置显式使用 `medium`，GPT-5.6 迁移后继续使用 `reasoning: {effort: "medium"}`。
- 继续传递 `text.verbosity=medium` 与 `max_output_tokens=900`。
- 不修改已用于 Telegram HTML 的系统提示词；官方指南建议先完成等行为基线，再根据代表性结果做小范围提示词调整。
- `OPENAI_MODEL` 或旧 `LLM_MODEL` 如果由部署环境显式设置，仍优先于新默认值。
- 自定义 `OPENAI_API_BASE` 必须由对应代理实际支持 `gpt-5.6-sol`；本项目不能替代理声明兼容。

## 参数兼容

GPT-5.6 支持 `none/low/medium/high/xhigh/max`。项目配置已增加 `max`，并在有效模型为 GPT-5.6 时拒绝旧的 `minimal`；旧模型显式配置仍可继续使用 `minimal`。

项目没有 Chat Completions function tools，因此不存在“工具调用必须使用有效推理强度 `none`”的兼容问题。若以后加入带推理的工具调用，应优先继续使用 Responses API。

## 验证边界

自动测试使用模拟 SDK 响应，不发送真实请求，也不消耗 OpenAI 额度。当前工作区没有 OpenAI API Key，因此以下内容仍需部署后观察：

- 账户是否已获得 `gpt-5.6-sol` 权限。
- 第三方 `OPENAI_API_BASE` 是否支持该模型。
- 与 GPT-5.5 相比的真实日报质量、延迟、token 和费用。
- `medium` 与 `low` 的代表性 A/B 结果；在有数据前不擅自降低推理强度。
