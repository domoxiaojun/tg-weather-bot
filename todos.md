# 优化修复执行 Todos (2026-07-26)

依据 docs/optimization-review-2026-07.md。用户决策：和风基本免费不做成本保护（跳过 P1-8 失败冷却、P1-9 刷新限流）；彩云该用就用，只防无效重试。

## 批次 A — 数据层（qweather / caiyun / fusion / cache / config）

- [x] A1 qweather: `_optional_float` 脏值返回 None；缺失温度丢弃记录而非落 0
- [x] A2 qweather: 小时 `fxTime` 解析失败丢弃记录（保证 tz-aware）
- [x] A3 qweather: `_map_daily`/`_map_indices` 按条目容错跳过畸形记录
- [x] A4 qweather: 非 JSON 的 2xx 响应按失败处理，不缓存垃圾
- [x] A5 qweather: geo 缓存坐标归一化 + 30 天 TTL（防 Redis 无限增长）
- [x] A6 qweather: indices 缓存 TTL 按地点时区计算（geo tz 字段）
- [x] A7 caiyun: 429 纳入长冷却，HTTPStatusError 传响应体判定；删 `_probability_over`
- [x] A8 fusion: 删 estimated 清理死代码；彩云禁用路径异常处理与双源路径一致
- [x] A9 config: `enable_caiyun_minutely` 折叠进 `enable_caiyun_api`（弃用告警）；删 `llm_streaming`；新增 timezone、gemini 超时配置
- [x] A10 cache: `_get_redis` 并发保护；Redis 恢复后内存回退层可见

## 批次 B — core 层（scheduler / handlers / bot / main）

- [x] B1 scheduler: job 内 chat_data 修改触发持久化
- [x] B2 scheduler: 每日简报改为时区感知（默认 Asia/Shanghai，可配置）
- [x] B3 scheduler: 单地点 wait_for 超时；Forbidden 时清理死订阅；删 job_error_handler 与无效 tenacity retry
- [x] B4 handlers: `/tq` 频道消息用 effective_message 防 AttributeError；提示去反引号
- [x] B5 callbacks: 进 handler 先 `query.answer()`
- [x] B6 report: keep_typing 吞掉全部异常，不毁掉已生成报告
- [x] B7 inline/formatter: result id 与 callback_data 64 字节安全（短 hash 映射）
- [x] B8 inline: 删除未使用的 view 参数解析并修正帮助文案
- [x] B9 subscriptions: 订阅时 geocode 校验 + 归一化存储
- [x] B10 bot/main: error handler 给用户反馈 + 管理员告警；main.py logger.exception + sys.exit(1)；删 "123456" 校验；post_init 并入 create_app（builder 链式）

## 批次 C — 展示与 LLM 层（visualizer / chart_cache / formatter / llm）

- [x] C1 chart_cache: 渲染移入单线程 executor（不阻塞事件循环）；失败负缓存
- [x] C2 visualizer: 逐日图 None 防护并迁移到统一卡片渲染管线
- [x] C3 visualizer: 小时图 NaN 防护（nanmin/nanmax，全 NaN 返回 None）
- [x] C4 visualizer: figure 异常路径 try/finally 关闭；CJK 字体补 Linux 路径并缓存探测结果
- [x] C5 visualizer: 降水双面板与峰值标注去重提取
- [x] C6 formatter: 提取 `_format_wind`（6 处重复）与日字段准备（2 处重复）；pop 缺失不显示 "N/A%"；minutely 数值格式化；删死代码
- [x] C7 llm: 错误路径通用文案 + 详情仅日志；HTML 标签配对校验；Markdown 规则防误伤；4096 安全截断
- [x] C8 llm: Gemini 超时读配置；删 delta 流式死代码

## 批次 D — 收尾

- [x] D1 更新 .env.example / README / CLAUDE.md（配置增删与成本决策）
- [x] D2 新增/调整测试覆盖关键修复，全量测试通过
- [x] D3 pyproject.toml 声明项目与 dev 依赖（ruff 声明为 dev 依赖，安装待用户确认，未擅自安装）
- [x] D4 分批提交

## 决策跳过（不修）

- P1-8 QWeather 失败冷却、P1-9 刷新按钮限流 —— 用户确认和风基本免费，无成本压力

## 第二轮 — AI 日报优化（2026-07-26，用户决策）

决策：effort 保持 medium；提示词不限字数、追求最佳总结；payload 字段保持全量；日报缓存 TTL 4h（预警/降雨信号变化自动失效）；超时 60s；流式输出。

- [x] E1 config: 超时默认 60s；max_output_tokens 默认不限制；新增 LLM_STREAMING 与 LLM_REPORT_CACHE_TTL_SECONDS
- [x] E2 llm: 提示词放开字数限制并允许"未来几天"块
- [x] E3 llm: OpenAI Responses/Chat Completions 流式输出（Gemini 回退整段）
- [x] E4 llm: 日报缓存（coords+模型+预警/降雨指纹+提示词哈希，TTL 可配）
- [x] E5 handlers: /report 与 inline 日报渐进编辑消息（节流），替换 keep_typing
- [x] E6 tests: 流式聚合、缓存命中、OpenAI 流事件解析；更新 900 token 断言
- [x] E7 .env.example / README 同步；全量验证并提交

## 第三轮 — 性能与用户体验（2026-07-26，用户确认全做）

### F 性能
- [x] F1 图表 file_id 复用：普通聊天发图后回存 file_id，同数据秒发
- [x] F2 定时任务有界并发（Semaphore 5）
- [x] F3 visualizer 迁移 OO API（Figure/FigureCanvasAgg），渲染线程池开到 2
- [x] F4 图表 dpi 140→120 瘦身
- [x] F5 uvloop 接入（requirements + main.py，仅非 Windows）

### G 用户体验
- [x] G1 城市歧义候选按钮（同名城市列出让用户选）
- [x] G2 天气卡片新增 🤖 AI日报 与 📅 逐日图 按钮（AI日报走流式+缓存）
- [x] G3 /chart 回复附带图表切换键盘
- [x] G4 /rain_my、/daily_my 列表带一键退订按钮
- [x] G5 /start 私聊提供"发送位置"快捷键盘
- [x] G6 降雨提醒附降水图；冷却时长做成配置 RAIN_ALERT_COOLDOWN_HOURS
- [x] G7 早安简报自定义时间 /daily_sub 城市 HH:MM（分钟级调度窗口）
- [x] G8 全量验证（tests/ruff/compile/factory）并分批提交

## 第四轮 — 降雨订阅体验重做与剩余 UX 修复（2026-07-26，用户决策）

决策：降雨检查间隔可配置默认 30 分钟；免打扰时段默认 23:00-07:00；订阅上限每聊天 3 城；降雨提醒改按"降雨事件"触发。

- [x] H1 config: RAIN_CHECK_INTERVAL_MINUTES(30) / RAIN_ALERT_QUIET_HOURS(23:00-07:00) / MAX_SUBSCRIPTIONS_PER_CHAT(3)
- [x] H2 scheduler: 降雨状态机（每次降雨事件只提醒一次，雨停复位）；免打扰时段整体跳过检查；前瞻窗口随间隔自适应
- [x] H3 /tq 支持 今天/明天/后天/大后天；查询失败文案给出参数示例与改名建议
- [x] H4 inline 死胡同：noop 按钮给出等待反馈；占位文案加预期时长与重试指引；SUPER_ADMIN 术语弹窗改为用户可懂文案；README 增加 BotFather /setinlinefeedback 清单
- [x] H5 图表键盘补齐三图互切 + 📝 文字天气 出口；back 文案修正；歧义选择后收起按钮
- [x] H6 订阅上限（命令+按钮双入口）；订阅确认文案含预期管理；usage 全部中文化；时区显示"北京时间"
- [x] H7 /start 帮助补齐订阅命令与相对日期示例，inline 示例用真实 bot 用户名
- [x] H8 新增 9 项测试（状态机/免打扰/上限/相对日期/时段解析），全量 84 项通过

## 第五轮 — 输出排版与交互体验打磨（2026-07-26）

- [x] I1 排版: 默认视图去掉与下方重复的自动 summary 行；日期全面带周几；温度不再显示 22.0 式小数；N/A 字段整段省略（能见度/湿度/UV）
- [x] I2 排版: 逐小时视图重做为每小时 2 行紧凑版式，跨天插入日期分隔行（24h 消息长度约降 60%）
- [x] I3 排版: 多日预报从 10 行树状压缩为 5 行卡片（天气箭头式 日→夜、统计一行、日出日落一行）
- [x] I4 排版: 早安简报头部加日期与周几，去掉 ASCII 分隔线
- [x] I5 交互: 视图切换按钮（实时/逐小时/未来7天/指数）原地编辑消息不刷屏，当前视图自动隐藏；图片消息给出替代指引
- [x] I6 交互: 刷新按钮携带当前视图，刷新后不再跳回默认视图
- [x] I7 使用: 私聊直接发送城市名即可查询（支持"北京 明天"参数；>20字或多行自动忽略防误伤）
- [x] I8 使用: /tq 不带参数时回落到上次查询的城市
- [x] I9 新增 6 项测试（键盘视图编码/64字节上限/紧凑版式/上次城市/私聊文本守卫），全量 90 项通过

## 第六轮 — Bot API 10.1/10.2 富文本接入（2026-07-26，用户决策：轻量封装 + 默认开启 rich）

方案：留在 PTB 22.8，用官方公开逃生舱 do_api_request/api_kwargs 封装；不迁移 aiogram。线格式对照官方 10.2 机器可读规格逐字段核对（网页摘要给出的 InputRichMessage{text,parse_mode} 是错的）。

- [x] J1 services/telegram_rich.py: 块/富文本构造器 + 传输层 + 能力记忆降级（EndPointNotFound 永久 / 3×BadRequest / 瞬时不禁用）
- [x] J2 utils/rich_formatter.py: 五个视图的 block 渲染（表格/可折叠/引用/高亮），完全无需 MarkdownV2 转义
- [x] J3 config: ENABLE_RICH_MESSAGES / ENABLE_EPHEMERAL_MESSAGES / ENABLE_RICH_REPORT_STREAMING 全部默认 true
- [x] J4 weather/callbacks: 发送与视图切换/刷新走 rich，失败回落文本
- [x] J5 report: 私聊 sendRichMessageDraft 原生流式（thinking 块）→ sendRichMessage 落地；群/inline 最终 editMessageText(rich_message)
- [x] J6 scheduler: 降雨提醒把图表以 photo 块嵌入同一条富消息（绕开 1024 caption 上限）；早安简报 rich html
- [x] J7 subscriptions: 群内订阅管理回复改 ephemeral（receiver_user_id）；命令注册带 is_ephemeral
- [x] J8 新增 32 项 rich 测试（线格式/降级分级/ephemeral 路由/五视图块合法性），全量 122 项通过
- [x] J9 文档：重写 docs/telegram-bot-api-update-2026-07.md（含 PTB 传输层审计与已核对线格式）、README、CLAUDE.md、.env.example

### 暂未接入（有意）
- Inline 查询结果的 InputRichMessageContent：answerInlineQuery 由 PTB typed 封装，需重写整个调用；选中后的编辑已是富文本
- editEphemeralMessage*/deleteEphemeralMessage：当前 ephemeral 均为一次性回复
