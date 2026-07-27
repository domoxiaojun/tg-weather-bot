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

## 第七轮 — 表格能力深化（2026-07-26，用户：先做体验好的，多城市对比暂不做）

- [x] K1 时段概览矩阵：行=日期、列=凌晨/上午/下午/夜间，格内天气+温区间，可能降水的时段 marked 高亮；未覆盖时段用隐形单元格；接入逐日视图顶部
- [x] K2 空气质量详情：六项污染物表格 + 主要污染物 + 健康建议（此前只喂给 AI 日报，文字视图从未展示），可折叠；summary 行常显 AQI/等级，并去掉统计表里的重复空气行
- [x] K3 新增 8 项测试（矩阵列/隐形格/高亮/最少两天守卫/污染物齐全/无数据跳过/去重），全量 130 项通过
- [ ] K4（跳过）多城市对比表 —— 需先做多城市参数解析，用户暂不需要

## 第八轮 — 推送可靠性与平台加固（2026-07-26，用户：高+中等全做）

自定决策：预警去重按 (alert_id, pub_time)；红/橙色预警豁免免打扰（可配 ALERT_QUIET_HOURS_EXEMPT_LEVELS）；灾害预警推送复用降雨订阅列表，不新增订阅类型。

- [x] L1 config: 预警推送/豁免等级/检查间隔、简报补发窗口、限流开关、pickle 备份份数
- [x] L2 pickle 启动轮转备份（防单文件损坏导致订阅全失）
- [x] L3 启用 PTB AIORateLimiter（aiolimiter 已随 [all] 装好，无需新依赖）
- [x] L4 简报调度改为按订阅地点时区（UTC 水位 + 各自 tz 目标时刻）
- [x] L5 简报停机补发（限定窗口内补，按 tz 当天去重）
- [x] L6 官方灾害预警主动推送（事件去重、豁免等级、warning TTL 1800→300）
- [x] L7 派生事件提示：空气恶化 / 极端温度 / 大风
- [x] L8 分钟级降水图（新图表类型），降雨提醒优先使用
- [x] L9 论坛话题订阅（记住 message_thread_id 并用于推送）
- [x] L10 GitHub Actions CI：测试/编译/ruff + 容器内 CJK 字体渲染断言；compose healthcheck
- [x] L11 测试与文档（新增 18 项，共 149 项），全量验证并提交

## 第九轮 — 自查发现的合规与数据缺口（2026-07-26，用户要求：不用子代理，自己核）

自己逐条核对 docs/ 与官方 10.2 规格后的发现与修复：

- [x] M1 合规：attributions 一直只收集不展示，而和风文档明确"attribution 是许可要求，不是可选展示字段"——文本与富文本 footer 现在都展示（含测试防回归）
- [x] M2 生活指数只请求 5/16 类，而 formatter 的 CATEGORIES 早已覆盖全部 16 类（11 类有展示逻辑但永远无数据）→ 默认改为 1-16（和风免费）
- [x] M3 预警推送补充"有效期至"（alert.expire_time 此前完全未展示）
- [x] M4 修正 .env.example 四处过时注释（每 5 分钟 rain check、免打扰按全局时区、提示词要求输出时间戳行）
- [x] M5 新增 5 项测试（共 154 项）

### 已核实但未做（见分析报告）
- 和风未接入端点：历史天气、格点天气、台风、天文（日月升落/太阳高度角）、潮汐、POI、监测站、indices/3d
- 认证仍用 API Key；官方要求 2027-01-01 前迁 Ed25519 JWT
- Telegram 未用：setMyDescription/setMyShortDescription（发现性）、BotCommandScope 分场景命令、按钮 style 配色、switch_inline_query 分享按钮
- 数据未展示：露点、逐小时/逐日的气压云量能见度、月升月落、日均温、白天/夜间降水量

## 第十轮 — Telegram 发现性/按钮与隐藏数据展示（2026-07-26，用户：全部做了）

- [x] N1 setMyShortDescription / setMyDescription：Bot 资料页在用户点 Start 前就能说明用途
- [x] N2 分场景命令注册（BotCommandScopeAllPrivateChats / AllGroupChats / 默认），群聊菜单更短
- [x] N3 按钮语义配色（订阅=绿 / 退订=红 / AI日报=蓝），经 api_kwargs 透传 style，旧客户端自动忽略
- [x] N4 📤 分享按钮（switch_inline_query），一键选聊天分享天气卡片
- [x] N5 逐小时"更多指标"折叠表：露点/气压/云量/能见度/逐小时AQI（此前全部只喂 AI 日报）
- [x] N6 今日详情补 日均温 / 月升月落 / 昼夜分段降水量
- [x] N7 新增 6 项测试（共 160 项）；验证 scope 枚举经 json.dumps 正确序列化为 all_private_chats

### 仍未做（需你决策，非补短板）
- 和风新功能线端点：历史天气、格点天气（任意经纬度）、台风路径、潮汐、POI、监测站、indices/3d
- 天文端点冗余：日月升落/月相已由逐日预报提供，无需另接
- Ed25519 JWT 认证迁移（官方要求 2027-01-01 前，需要你在和风控制台生成密钥对）

## 第十一轮 — 新端点接入（2026-07-26，用户：全都加上；历史天气交给 AI 总结）

对照官方文档核实字段后接入（自己核，无子代理）：

- [x] O1 格点天气：坐标查询时若最近城市距用户坐标 > GRID_WEATHER_DISTANCE_KM(15km) 则改用 /v7/grid-weather/*，
      地名显示为"XX 附近"不再冒用城市名；字段名与城市天气一致故复用现有 mapper；缺失的 vis/feelsLike/pop 保持为空
- [x] O2 历史天气（Time Machine）：只取昨日 weatherDaily，进 LLM payload（yesterday_observed）+ 提示词要求对比昨天，
      今日详情加"📊 比昨天 最高/最低 ±N°"；LocationID only、不含今天、缓存 24h
- [x] O3 生活指数 1d→3d：视图按天选择（明后天进折叠块），LLM payload 也只取当天，避免每个指数重复 3 次
- [x] O4 监测站：复用实时空气质量响应里的 stations[] 名称，展示进空气质量折叠块（零额外 API 调用）
- [x] O5 预警缓存 TTL 1800→300 秒（保命数据不该延迟半小时）
- [x] O6 新增 18 项测试（共 178 项）：距离计算、格点范围降级、格点 payload 无 pop/vis、历史映射与脏值、
      监测站去重、指数按天选择不重复
- [x] O7 文档：docs/qweather-api-reference-2026-07.md 记录格点字段差异与接入状态、.env.example 新增三项配置

### 下一批（尚未做）
- [x] P1 台风：已完成
      · 模型 TropicalStorm/TyphoonPoint/TyphoonWindRadius；adapter get_storm_list/get_storm_detail/get_active_storms
      · services/typhoon.py：方位角选风圈象限 → 7/10/12 级风圈判定（黄/橙/红），无风圈数据时按距离关注
      · 复用 check_weather_alerts 的去重/免打扰豁免/Forbidden 清理；活跃台风每轮只取一次，全地点共享
      · 富文本推送块（当前强度/气压/移动 + 预测路径表）+ 路径图（历史虚线/预测实线/当前★/用户▲，等比例坐标）
      · /typhoon [城市] 命令；新增 22 项测试（共 200 项）
- [x] P2 潮汐：POI(type=TSTA) 找最近站点 → 潮汐表；新增 /tide 命令、富文本高低潮表、潮位曲线图（高低潮标注）
- [x] P3 太阳辐射：按 fill-only 用 GHI 补 HourlyForecast.radiation（彩云关闭时的唯一辐射来源），
      逐小时"更多指标"折叠表新增辐射列，AI payload 自动带上；不新增独立界面
- [x] P4 新增 16 项测试（共 216 项）：GHI 按小时聚合取最强、fill-only 不覆盖、TSTA 站点排序与兼容 poi/location 键、
      潮汐表脏数据跳过、潮位曲线渲染

## 第十二轮 — 和风 Ed25519 JWT 认证迁移（2026-07-26）

- [x] Q1 services/qweather_auth.py：Ed25519 签名 + token 缓存（到期前 60s 重签）+ TTL 钳制到官方上限 86400
- [x] Q2 config：QWEATHER_AUTH_MODE(auto/jwt/api_key) + JWT 三项凭据（支持内联 PEM 的 \n 转义或文件路径）；
      qweather_api_key 改为可选，并用 model_validator 保证"至少一种凭据存在"
- [x] Q3 adapter：_auth_headers() 优先 Bearer，签名失败且有 API Key 时回退，无 Key 才抛出
- [x] Q4 scripts/generate_qweather_key.py 生成密钥对；secrets/ 与 *.pem 加入 .gitignore（生成前就加）
- [x] Q5 新增 31 项测试（共 247 项）：header/payload 结构、公钥验签、篡改验签失败、base64url 无 padding、
      非 Ed25519 密钥拒绝、TTL 钳制、缓存与重签、三种模式选择、坏密钥在 auto 下降级、适配器请求头
- [x] Q6 文档：README 认证章节、CLAUDE.md、API 文档更正（官方是"限制每日请求量"而非停用 API Key）

### 待用户完成
- [x] 把公钥填入和风控制台，拿到 Credential ID(kid) 与 Project ID(sub) 后写入 .env，真机验证一次
      （2026-07-27 完成：本地 .env 已写入三件套，auto 模式选中 JWT，真机 geo 查询 200；
      docker-compose 已挂 ./secrets:/app/secrets:ro）
- [x] .env 体检并修复 7 处（2026-07-27）：补 JWT 三件套；停用旧提示词覆盖（会复活缓存陈旧时间 bug）；
      指数恢复 16 类、逐时恢复 72h；注释被覆盖的 LLM_MODEL；LOG_LEVEL DEBUG→INFO；
      WEBHOOK_SECRET 占位符换随机值；清理过时超时注释。专属 host + JWT 真机 200 验证通过
- [ ] 部署机：同步修好的 .env 与 secrets/ 目录（保持 600 权限）后重启
- [ ] 私钥备份到密码管理器或离线介质（丢了只能在控制台重建凭据）
- [ ] 部署后真机冒烟：/tq 北京 看富文本卡片与按钮、/rain_my 卡片操作、等第一轮推送看按钮

## 第十三轮 — 降雨阈值 + 过度设计排查（2026-07-26）

用户质疑上一轮方案过度设计，复核后确认成立，砍掉大部分并只做真正修缺陷的部分。

- [x] R1 降雨阈值可配：新增 rate_mm_per_hour() 统一单位（分钟级 5 分钟累积 ×12、intensity 已是 mm/h），
      evaluate_rain() 返回峰值速率/概率/等级，痕量降水不再触发；is_raining 不再短路绕过阈值；
      推送文案带上「中雨 约 6.0mm/h」；新增 20 项测试专门覆盖单位换算
- [x] R2 删除 6 处死代码：telegram_rich 的 preformatted/ordered_list/checklist、typhoon.utc_now、
      qweather_auth.jwt_config_complete、rich_formatter.build_report_blocks（都是"以防万一"建的，从未被调用）
- [x] R3 删除 3 个多余开关：ENABLE_GRID_WEATHER / ENABLE_SOLAR_RADIATION / ENABLE_HISTORY_COMPARISON
      —— 它们只控制不可见的数据补全、失败已优雅降级，且与"和风免费不做省调用机制"的决策矛盾；
      格点的关闭需求由 GRID_WEATHER_DISTANCE_KM 设大值等效满足。配置项 81→78
- [x] R4 移除已被实测证伪的推测性防御：POI 数组名确认为 poi，删掉兼容 location 的分支
- [x] R5 全量 265 项测试通过

### 复核后主动放弃的设计（等有证据再做）
- 数据模型整并（8 个平行字典 → 嵌套记录）：对用户零价值却要动线上订阅数据；
  我原先拿"已有 pickle 备份"当理由是反向论证——备份是兜意外，不是给不必要风险发许可
- 按聊天免打扰、简报数据模式、合并推送、群聊归属、运维汇总、暂停时长解析：均为替用户想象的需求
- 触发条件：群里真出现互删 → 做归属；有人抱怨小雨仍烦 → 加档位；订阅涨到几十城 → 做合并与整并

## 第十四轮 — 每个订阅独立的降雨档位（2026-07-26，用户要求）

用户："应该是每个订阅有对应的设置项，降雨量分档次我也不太懂，你觉得怎么设计比较好"。
设计取舍：既然连 Bot 作者都觉得 mm/h 不直观，界面就不能出现 mm/h。三档按"意图"命名，
mm/h 只作为实现细节；档位存 chat_data["rain_level"][location]，不动数据模型。

- [x] S1 scheduler: RAIN_LEVELS 三档（全部降雨 0mm/h ／ 一般降雨 = 配置值默认 1.0 ／ 仅大雨 8mm/h），
      别名表兼容「小雨/灵敏/1」「标准/默认/中雨/2」「大雨/暴雨/3」等写法；仅大雨同时关掉"高概率"
      单独触发通道，避免"90% 概率的小雨"把它吵起来
- [x] S2 scheduler: check_rain_alerts 分组携带每条订阅的档位，同一城市只取一次数据、按不同档位各算一次
      信号，再按订阅者自己的阈值过滤；低于本人阈值时清空其降雨事件状态，后续更大的雨仍能送达
- [x] S3 /rain_sub 城市 [档位]：尾词是档位才剥离（"New York" 不会被误吃），已订阅的城市再执行一次即改档位，
      确认消息带档位与白话说明；无参用法提示列出三档
- [x] S4 /rain_my 重做：每城显示档位 + 上次提醒时间 + 当前是否免打扰（免打扰不再被误当成 Bot 坏了），
      档位按钮一排（callback lvl|{index}|{level}，索引制以守住 64 字节），当前档位打 ✅；退订清理档位记录
- [x] S5 callbacks: lvl 回调原地改档位并重渲染列表；退订与改档位共用 _refresh_subscription_list
- [x] S6 scheduler._zone_for 改为公开 zone_for（handlers 复用，避免复制时区兜底逻辑）
- [x] S7 新增 24 项测试（别名解析、档位阈值语义、同城两订阅不同档位、仅大雨忽略高概率、
      按钮 callback 预算、免打扰提示、退订清理），全量 289 项通过

## 第十五轮 — 订阅卡片式按钮交互（2026-07-26，用户要求）

用户："订阅没有做卡片式按钮交互这种吗? 优化用户体验啊"。
规则：任何订阅流程不得以"再去敲一条命令"收尾——每个触点都回可直接操作的卡片。

- [x] T1 /rain_my /daily_my 卡片化：私聊富文本块（heading+bullet_list+footer），群聊 ephemeral 文本回退；
      HTML 与富文本共用 _subscription_rows()，按钮共用 _subscription_keyboard_rows()
- [x] T2 早安卡片每城一排预设时间按钮 06:30/07:00/07:30/08:00（当前 ✅ 绿色），dtime|{index}|{HH:MM} 点按即改；
      自定义时间仍走 /daily_sub 城市 HH:MM
- [x] T3 卡片底部 subview|{kind} 按钮原地翻转 降雨卡↔早安卡；空状态也有按钮（➕ 订阅上次查询城市 + 翻卡），
      退订最后一城后留下的也是可用的空卡片
- [x] T4 天气卡片第一排新增「📅 早安简报」（dsub|token）；🔔/📅/➕ 统一 _handle_subscribe(kind)：
      重复点按→toast、超限→alert、成功→私聊回卡片、群聊发公开确认（文案指向命令而非不存在的按钮）
- [x] T5 修 bug：按钮订阅路径此前不存时区（sub_tz/daily_sub_tz），推送时区会退回全局 TIMEZONE
- [x] T6 全部命令确认（订阅/改档/改时/退订/重复订阅）都带管理按钮；富文本编辑仅私聊启用，
      群内 ephemeral 编辑失败不会打击 FEATURE_EDIT 全局能力
- [x] T7 新增 18 项卡片交互测试（tests/test_subscription_cards.py），全量 307 项通过

## 第十六轮 — 推送消息可操作化 + 交互面复查（2026-07-26，用户问"都优化了吗"触发的审计）

审计方法：全量测试 + 回调 producer/dispatcher 交叉核对（12 个前缀全对上）+ 逐条走查交互面。
发现的缺口：推送消息本身没有任何按钮，是最后一处"死"输出。

- [x] U1 scheduler.push_keyboard()：所有推送尾部挂 [📊 查看完整天气 (tq|token)] [⚙️ 管理提醒/简报 (submy|kind)]；
      覆盖 降雨提醒（rich/图+文/纯文本三条路径，按钮挂在最后一条可见消息上）、早安简报、
      官方预警/衍生事件/台风推送
- [x] U2 callbacks: submy|{kind} 回调从推送直接打开订阅卡片（不动推送本身）
- [x] U3 修复：点「查看完整天气/文字天气」后 edit_message_reply_markup(None) 会把推送和图表卡的按钮剥掉——
      现在仅当整张键盘都是 tq| 按钮（纯歧义选择列表）才收回按钮
- [x] U4 /start 订阅一行改为提示卡片交互
- [x] U5 新增 7 项推送按钮测试（tests/test_push_buttons.py），全量 314 项通过

## 第十七轮 — 截图反馈：排版重排 + 渲染 bug 修复（2026-07-27，用户实测反馈）

- [x] V1 实时卡片重排：实况六字段并入「今日详情」单张表，去掉折叠（最想看的数字不该藏在开关后）；
      空气质量保持折叠但摘要注明「点击展开详情」；头部一行装下 位置 · 日期 周几；
      实时温度行标明「🌡️ 实时」；生活指数贴士改两列表格（5 条从 5 行变 3 行）
- [x] V2 修 bug：AI 日报/早安简报挤成一坨——最终消息走了 rich html=，InputRichMessage 按真 HTML
      语义折叠换行。新增 build_report_blocks()（<b>/<i> → 实体、逐行成段、Generated-by → footer 块），
      日报三个出口（私聊 draft 收尾/占位符编辑/inline 编辑）与早安简报全部改走 rich 块，回落纯 HTML
- [x] V3 修 bug：私聊流式期间显示原始 <b> 标签——draft 的 thinking 块是纯文本不解析 HTML，
      新增 plain_stream_preview() 先剥标签
- [x] V4 修 bug：「小雨」天查询不再附降水图——is_raining 只认测量值（now_precip/分钟级），
      当前文本含雨但未来一小时无降水时为 False；提取 should_attach_rain_chart()，文本含雨/雪也触发
- [x] V5 新增 18 项测试（tests/test_layout_and_report.py），全量 333 项通过

## 第十八轮 — 图表全面重设计（2026-07-27，用户："生成的图表很差"）

方法：先真机渲染四张图逐张目检，配色跑色觉验证器（发现体感青 vs 概率蓝 ΔE 6.7，
正常视力都难分辨），再按"选形式→按职责配色→验证→标记规范→目检"流程重画。

- [x] W1 主题配色修正：体感改粉（三项硬指标全过）；键按语义拆分 water/track/pop_low/mid/high
- [x] W2 逐小时温度：标签 高/低/现在 三处；删体感散点；底部降雨概率背景条；清除"API 原值"黑话
- [x] W3 逐小时降水：概率柱三档亮度（顺序渐变）；"HH:MM 转雨"自动标注；雨量/雨势面板改说人话
- [x] W4 逐日：范围竖条替代双折线；只标极值与今天；有雨蓝点（此前哪天下雨不可见）
- [x] W5 分钟级：统一 mm/h 后隐藏数字，中雨/大雨/暴雨等级参考线；峰值标注 "HH:MM 中雨"
- [x] W6 全量 336 项测试通过，四张图重渲染目检无碰撞

## 进行中 — 两个后台审计工作流（2026-07-27）

- [ ] UX 审计（wf_4e3b52d1）：引导/命令死角/私聊摩擦/图表 四路并行 + 反过度设计把关
      → 产出后实现 /start 重做、使用指南、订阅新手引导
- [ ] 性能审计（wf_55caff7e）：请求链路/并发模型/缓存/内存持久化 四路并行 + 证据核实排序

## 第十九轮 — 双审计工作流落地（2026-07-27，用户允许子代理后首次编排）

两个 Workflow（各 4 审计代理 + 1 反过度设计/核实把关）：UX 34→9 条、性能 21→10 条
CONFIRMED；关键论断逐条回代码抽查属实后分四批落地，每批全量测试。

性能批（全部 CONFIRMED）：
- [x] X1 concurrent_updates(16)——PTB 默认串行，一次 60s AI 日报冻结全部用户
- [x] X2 pickle 备份轮转前验证可加载——坏文件+自动重启 3 次会把 bak1..3 全覆盖为损坏副本
- [x] X3 hourly TTL 6h→1h（无分钟级地区降雨告警曾评估"过去的小时"）、daily 12h→6h
- [x] X4 可选组件（solar/history/air/indices）4s 分级超时，一个挂起不再拖垮整个 /tq
- [x] X5 /tq 雨图先读 file_id 缓存；指纹剔除 update_time；反馈动作 fire_and_forget 后台化
- [x] X6 AI 日报缓存带 data_time，命中时不再用当前时间冒充（诚实性）
UX 批：
- [x] X7 /start 重做（三行欢迎+快速开始卡，老用户一键订阅上次城市）；新增 /help 按钮翻页指南
- [x] X8 北京明天/上海降水 免空格直查；私聊错命令兜底（/tq北京 自动救回）
- [x] X9 订阅命令六处死角收口卡片；图片卡视图切换改发新消息；/chart /report 无参回落；
      全部失败/空态路径配出口按钮（重试/上次城市/位置键盘/图表互切/查天气）
- [x] X10 图表方形化 1080×1080（气泡内字号相对 +70%）+「现在」锚点 + 降水两层面板
砍掉（过度设计/负结论）：深链预留、inline 错误按钮、持久键盘、闲聊过滤启发式、
Redis pipeline、简报 O(n²) 阈值项（几百 chat 再做）、id(app) 泄漏疑虑等 13 条，理由在案。
- [x] 日志收尾（主人拍板 2026-07-27）：文件 sink 对齐 LOG_LEVEL；时间戳在 loguru 层
      固定 CST(+8)，与容器/宿主时区和 tzdata 无关；.env.example 注释同步
