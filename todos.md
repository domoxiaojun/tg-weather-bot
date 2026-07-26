# 项目整理与优化分析 Todos (2026-07-26)

## 一、项目整理

- [x] 清理 .DS_Store 杂项文件，并在 .gitignore 中忽略
- [x] 运行完整测试套件确认基线（44 项 unittest 通过 + compileall 通过；ruff 未安装在 .venv，待确认后补装）
- [x] 检查 git 工作区，将已完成的双源融合优化改动分类提交（feat + chore 两个提交）

## 二、优化分析

- [x] 通读核心模块（handlers / adapters / services / utils），三路并行审阅完成
- [x] 输出优化改进建议报告 → `docs/optimization-review-2026-07.md`

## 三、后续待办（来自分析报告，未开始）

优先级详见 docs/optimization-review-2026-07.md：

- [ ] P0-1: matplotlib 渲染移出事件循环（asyncio.to_thread + OO API）
- [ ] P0-2: QWeather 脏值/缺失温度不再落 0（保持 None 或丢弃记录）
- [ ] P0-3: 定时任务 chat_data 持久化（mark_data_for_update_persistence）
- [ ] P0-4: 每日简报时区修正（Asia/Shanghai aware time）
- [ ] P0-5: 频道消息触发 /tq 的 AttributeError
- [ ] P0-6: 图表 None/NaN 防护 + 逐日图迁移到统一渲染管线
- [ ] P0-7: 小时数据 naive 时间回退改为丢弃
- [ ] P1: QWeather 失败冷却 / 刷新只刷短周期端点 / Caiyun 429 长冷却 / geo 缓存 TTL / 死订阅清理 / 图表负缓存 / 订阅归一化
- [ ] P2: 回调先 answer / keep_typing 吞异常 / 64 字节限制 / LLM 错误转义与 4096 截断 / 单地点超时 / main.py 退出码 / Docker CJK 字体验证
- [ ] P3: 死代码清理（llm_streaming、enable_caiyun_minutely、job_error_handler、tenacity retry、inline view 参数等）+ formatter/visualizer 重复提取 + pyproject/ruff/CI
