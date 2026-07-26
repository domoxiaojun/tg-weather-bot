# 项目优化改进分析报告（2026-07-26）

三路并行代码审阅（core 层 / 数据层 / 展示与 LLM 层）合并去重后的结果，按优先级排列。
基线：44 项 unittest 全部通过，compileall 通过。

## P0 — 正确性与稳定性（建议优先处理）

1. **matplotlib 同步渲染阻塞 asyncio 事件循环**
   `services/chart_cache.py:35`、`services/visualizer.py`（三个 `draw_*` 均为同步 CPU 密集函数），调用点 `core/handlers/weather.py:87,146`、`core/handlers/callbacks.py:104`。全项目无任何 `asyncio.to_thread`/`run_in_executor`。每次出图阻塞整个 bot 数百毫秒到秒级，期间所有消息、回调、inline 全部停摆。
   方向：用 `asyncio.to_thread` 包裹渲染；注意 pyplot 全局 API 非线程安全，宜改用 OO API（`Figure` + `FigureCanvasAgg`）或限定单线程 executor。

2. **QWeather 脏值/缺失值被静默转成 0，违反项目自己的 fill-only 融合策略**
   `adapters/qweather.py:124-128`（`_optional_float` 遇 `"N/A"` 等脏值经 `_to_float` 落到默认 0.0 而非 None）；`qweather.py:226-227,267`（缺失温度变成 0°C）。伪 0 会阻止彩云填充、并把错误的 0 展示给用户，且无法与真实 0 区分。
   方向：解析失败一律返回 None；温度缺失时丢弃该条记录（与 caiyun.py 做法拉齐）。

3. **定时任务里写的 `chat_data` 不会被 PicklePersistence 持久化**
   `core/scheduler.py:143-144`（`last_rain_alert` 冷却记录）。PTB 只在处理该 chat 的 update 后才落盘，job 回调里的修改不触发持久化；重启后 4 小时冷却丢失，用户被重复轰炸降雨提醒。
   方向：job 内调用 `application.mark_data_for_update_persistence(...)` + `update_persistence()`。

4. **每日简报按 UTC 触发：naive `time(8,0)` 实际是北京时间 16:00**
   `core/scheduler.py:184`，与 `subscriptions.py:32` 承诺的"早晨 8:00"矛盾（JobQueue 默认时区 UTC）。
   方向：传入 `ZoneInfo("Asia/Shanghai")` 的 aware time，或做成配置项。

5. **频道消息触发 `/tq` 直接 AttributeError**
   `core/handlers/weather.py:106` 用 `update.message.location`，channel_post 时 `update.message` 为 None。
   方向：改用 `update.effective_message` 或注册时限制 `filters.UpdateType.MESSAGES`。

6. **逐日温度图对 None 直接崩溃；逐小时图对 NaN 不设防**
   `services/visualizer.py:894-937`（`int(val)` 遇 None 抛 TypeError，且整个函数还在用旧版 `plt.figtext` 全局 API，与另两张卡片图两套风格并存）；`visualizer.py:377-391`（气温用 `np.min/max` 而非 `nanmin/nanmax`，一条脏数据导致 header 显示 "nan°"）。
   方向：None/NaN 过滤 + 迁移到统一的卡片渲染管线。

7. **naive 时间回退导致小时融合键永不匹配**
   `adapters/qweather.py:274`（`fxTime` 解析失败回退 naive `datetime.now()`），`services/fusion.py:30-35` 对 naive/aware 生成不同形状的键，彩云填充对这些小时静默失效，排序也会错乱。
   方向：解析失败直接丢弃该小时记录，保证全链路 tz-aware。

## P1 — API 成本与配额保护

8. **QWeather 无失败冷却**：`adapters/qweather.py:58-108` 失败返回 None 且不缓存；key 失效/配额耗尽/持续 5xx 时，每个用户请求实时重打最多 8 个付费端点。方向：对齐 Caiyun 的冷却机制（认证/配额长冷却，瞬时故障短冷却）。
9. **刷新按钮强刷全部端点**：`qweather.py:554-636` 连 TTL 43200s 的 daily/air_daily/indices 也强刷，连点刷新 = 每次约 8 个付费调用。方向：force_refresh 只作用于 now/minutely/warning 等短周期组件。
10. **Caiyun 长冷却判定漏掉 429 与响应体文案**：`adapters/caiyun.py:147-155,219-225`，配额耗尽若以 400/429 返回则只有 60s 冷却，形同虚设。方向：429 纳入长冷却，HTTPStatusError 分支传入响应体判定。
11. **geo 缓存永久 TTL + 原始坐标字符串作键**：`qweather.py:164-184`，位置分享的高精度坐标几乎条条唯一，Redis 键无限增长且命中率趋零。方向：坐标先归一化精度，TTL 改有限长期（如 30 天）。
12. **被拉黑/移出群的订阅永不清理**：`core/scheduler.py:94-97,146-149` 推送失败只 log；每 5 分钟对死 chat 白耗天气 API。方向：捕获 `telegram.error.Forbidden` 时移除订阅。
13. **图表渲染/上传失败无负缓存**：`services/chart_cache.py:92-113`，持续失败场景下每个请求都重新走一遍完整渲染+上传（与第 1 条叠加放大）。方向：失败写短 TTL 哨兵值。
14. **订阅位置不归一化、不校验**："北京"/"北京市" 算两个订阅，订阅不存在的城市后每 5 分钟白查一次（`core/handlers/subscriptions.py:26-31,67-75`）。方向：订阅时先 geocode 验证并存归一化名称。

## P2 — 健壮性与用户体验

15. **回调查询未先 `answer()`**：`core/handlers/callbacks.py:66-70` 先做数秒网络请求，按钮长时间转圈甚至 "Query is too old"。方向：进 handler 先 answer。
16. **`keep_typing` 后台任务异常会毁掉已生成的报告**：`core/handlers/report.py:43-54`，finally 只 suppress `CancelledError`，`send_chat_action` 抛错会让成功的 LLM 结果被回复成"生成失败"。方向：内部吞掉全部异常。
17. **inline result id / callback_data 可能超 Telegram 64 字节限制**：`core/handlers/inline.py:181`、`utils/formatter.py:537-553`，中文长地名（UTF-8 每字 3 字节）会让整个 answer/键盘被 BadRequest 拒绝。方向：截断或短 hash 映射。
18. **LLM 错误文案未转义直接进 HTML 消息且泄露内部错误**：`services/llm.py:605-610`（含 `response.text[:100]`），错误体带尖括号时 Telegram 解析失败，用户连错误提示都收不到。方向：用户只给通用文案，详情进日志。
19. **AI 日报 HTML 加固不足**：`llm.py:654-686` 不校验 `<b>/<i>` 配对（不配对 = 整条消息被拒）；`llm.py:665-666` 单下划线/星号规则会误伤正常文本；无 4096 字符硬截断（`llm.py:558-594`）。
20. **单条畸形数据可让整个 QWeather 结果归零**：`qweather.py:225,505-507` 的 `_map_daily`/`_map_indices` 直接下标取键，KeyError 冒泡后整源被判 None，且脏数据在缓存里 TTL 内持续复现。方向：按条目 try/except 跳过（`_map_hourly` 已有，拉齐）。
21. **定时任务无单地点超时**：`scheduler.py:81,124` 无 `wait_for`，一个上游卡住拖垮整轮 job（`max_instances=1` 下后续周期被跳过）。
22. **`main.py:72-73` 吞掉致命异常且退出码 0**：systemd/Docker 不会自动重启，也没有堆栈。方向：`logger.exception` + `sys.exit(1)`。
23. **Docker/Linux 下 CJK 字体探测可能失效**：`services/visualizer.py:46-84` 只探测 macOS 字体路径；Dockerfile 虽装了 fonts-noto-cjk，但需确认回退 family 列表能命中，否则生产图表中文变方块。方向：补充 Linux 路径（Noto Sans CJK）并缓存探测结果，实际容器里跑一次验证。
24. **缓存层细节**：Redis 恢复瞬间内存回退层数据不可见，产生集中重取（`utils/cache.py:88-111`）；`_get_redis` 初始化无并发保护（`cache.py:53-76`）；indices 缓存的"至午夜 TTL"用服务器时区而非地点时区（`qweather.py:549-551`）；`force_refresh` 撞上在途任务时实际不会强刷（`cache.py:177-180`）。
25. **全局 error handler 用户零反馈**：`core/bot.py:75-78` 只写日志；`super_admin_id` 声称用于 critical alerts 却从未收到告警。方向：给用户通用错误提示，严重错误转发管理员。
26. **LLM 超时三层不一致**：Gemini 硬编码 60s（`llm.py:184`）不读配置，外层 `wait_for` 与 provider 内部超时互相独立。方向：统一从 settings 派生，外层略大于内层兜底。

## P3 — 死代码、重复与工程化

**死代码 / 无效配置（建议删除或补全）**
- `llm_streaming` 配置全仓库无人读取，Gemini `_extract_text` 的 delta 流式分支是死代码（`config.py:66`、`llm.py:227-230`）。
- `enable_caiyun_minutely` 已弃用但仍会单独触发付费彩云调用（`fusion.py:26-28`）；应在配置层折叠进 `enable_caiyun_api` 并告警。
- `core/scheduler.py:54-55` `job_error_handler` 从未注册；`scheduler.py:102` 的 tenacity `@retry` 实际永不生效。
- inline 查询解析出的 `view_type/start_day/days` 完全未使用，行为与帮助文案不符（`inline.py:58-66` vs 41-49）。
- `adapters/caiyun.py:117-122` `_probability_over` 无引用；`utils/formatter.py:2,17` logging 残留；`formatter.py:103-104` 不可达分支；`feels_like_estimated` 字段链在 model_validator 清理后已成 legacy（`fusion.py:47-50`、`models.py:279-286`）。
- `main.py:19` `"123456" in bot_token` 校验冗余且可能误伤。

**重复代码（可砍约 150+ 行）**
- `utils/formatter.py` 风况四步拼装逻辑重复 6 处（141-151 等）→ 提取 `_format_wind()`。
- `format_today_detail` 与 `format_daily_weather` 字段准备段两份拷贝（207-293 vs 295-351）→ 提取 `_extract_day_fields()`。
- `visualizer.py` 降水量/降水强度两个面板约 55 行镜像（731-786 vs 788-851），峰值标注重复 3 处 → 提取面板/标注函数。

**小体验问题**
- pop 缺失显示"降概 N/A%"（`formatter.py:398,419,474`）；minutely 原始 float 直出可能显示 `0.30000000000000004mm`（`formatter.py:455`）；`weather.py:111,115` 提示含反引号但没传 parse_mode。
- figure 异常路径不 `plt.close`，长期运行内存增长（`visualizer.py:105-115`）→ try/finally。
- 非 JSON 的 2xx 响应被当成功缓存半天（`qweather.py:77-79,104-105`）。
- `post_init`/`post_shutdown` 用属性赋值而非 builder 链式 API；命令注册散落 main.py，宜并入 `create_app`。

**工程化建议**
- 增加 `pyproject.toml` 管理项目元数据与 dev 依赖（ruff、可选 mypy），当前 .venv 里没有 ruff，plan.md 声称的 Ruff F/E9 校验无法复现。
- 补一个最小 CI（unittest + compileall + ruff）。
- `google-generativeai` 未锁版本，requirements 其余为下限约束；考虑加锁文件（uv lock）。

## 审阅中确认无需改动的部分

- 融合层 fill-only 策略实现严谨（`is None` 判空、`field_sources` 溯源、deep copy 正确）。
- `utils/cache.py` single-flight + shield 设计正确，内存回退有 2048 条上限。
- 服务共享、shutdown 资源关闭、后台任务按地点去重扇出均正确。
- 未发现同步 Redis 调用；除 matplotlib 外无其他事件循环阻塞点。
