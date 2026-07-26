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
- [ ] C2 visualizer: 逐日图 None 防护并迁移到统一卡片渲染管线
- [ ] C3 visualizer: 小时图 NaN 防护（nanmin/nanmax，全 NaN 返回 None）
- [ ] C4 visualizer: figure 异常路径 try/finally 关闭；CJK 字体补 Linux 路径并缓存探测结果
- [ ] C5 visualizer: 降水双面板与峰值标注去重提取
- [ ] C6 formatter: 提取 `_format_wind`（6 处重复）与日字段准备（2 处重复）；pop 缺失不显示 "N/A%"；minutely 数值格式化；删死代码
- [ ] C7 llm: 错误路径通用文案 + 详情仅日志；HTML 标签配对校验；Markdown 规则防误伤；4096 安全截断
- [ ] C8 llm: Gemini 超时读配置；删 delta 流式死代码

## 批次 D — 收尾

- [ ] D1 更新 .env.example / README / CLAUDE.md（配置增删与成本决策）
- [ ] D2 新增/调整测试覆盖关键修复，全量测试通过
- [ ] D3 pyproject.toml 声明项目与 dev 依赖（ruff 安装待用户确认，不擅自装）
- [ ] D4 分批提交

## 决策跳过（不修）

- P1-8 QWeather 失败冷却、P1-9 刷新按钮限流 —— 用户确认和风基本免费，无成本压力
