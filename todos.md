# /tq 卡片瘦身 + AI 日报压缩

用户反馈：`/tq` 卡片太长（实况表 5 行 + 今日表 12 行全平铺），AI 日报也太长。
决策：卡片合并成 1 张核心表 + 1 个折叠块，**并重排字段顺序让排版整洁**；
AI 日报 prompt 收紧 + 「未来几天」「建议」折叠。

## 一、天气卡片（utils/rich_formatter.py）

- [x] 1. 新增 `_core_stats_rows()`：按「冷热 → 概貌 → 体感 → 带伞 → 防晒 → 时间 → 趋势」
      排序的核心表（气温/日夜/湿度/风况/降水/紫外线/日出日落/比昨天）
- [x] 2. 新增 `_extra_stats_details()`：月相、月升月落、云量、能见度、气压、当前降水、
      日均温、昼夜降水、日夜风 收进「🔭 更多气象参数」折叠块
- [x] 3. UV 数字加分级文案（11 → `11 极强`），裸数字对用户无意义
- [x] 4. `build_realtime_blocks` 改用新结构；`_current_stats_rows` 保留给预警推送复用
- [x] 5. 折叠块顺序：更多气象参数 → 生活指数 → 空气质量（都收起，视觉对齐）

## 二、AI 日报

- [x] 6. `services/llm.py` prompt：每块句数硬上限、总长 ≤600 字符、建议 2-3 条、
      平稳天气省略「未来几天」
- [x] 7. `build_report_blocks(collapse_tail=True)`：「未来几天」「建议」及之后内容
      收进折叠块，正文只留 预警/现在/接下来
- [x] 8. `core/handlers/report.py` 调用处传 `collapse_tail=True`（guide 帮助文本不受影响）

## 三、验证

- [x] 9. 更新 `tests/test_weather_card_layout.py` 断言 + 新增排序/折叠回归测试
- [x] 10. unittest 全量 + compileall + ruff(F,E9) + git diff --check
- [x] 11. git 提交

---

# 三图重设计（亮色底 + dataviz 配色）— 已完成

配置来源：dataviz 技能。用户选定：亮色卡片底（#fcfcfb 系），逐小时降水图以「降水概率」为主体、去掉双 Y 轴。

- [x] 1. 渲染现状 3 图作对比基线
- [x] 2. 重写 `_THEME`：亮色 surface/ink/grid + dataviz 验证配色
- [x] 3. 网格/坐标轴：虚线 → 1px 实线极细灰
- [x] 4. 逐小时温度图：暖橙主线 + violet 虚线体感，稀疏标注（现在/高/低）
- [x] 5. 逐小时降水图：单轴、概率柱为主体、单色、去 twinx、去三档深浅
- [x] 6. 逐日温度图：稀疏标注（最热日高温 + 最冷日低温 + 今天）
- [x] 7. header/footer/legend 适配亮底（文字用 ink token，不穿数据色）
- [x] 8. 渲染 3 图肉眼检查（降水图叠字已错开修复）
- [x] 9. 缓存 namespace chart:v13 → v14，旧深色缓存图失效
- [x] 10. 更新测试断言 + 新增 twinx 防回归测试
- [x] 11. 验证：unittest(55 passed) + compileall + ruff(F,E9) + git diff --check
- [x] 12. git 提交

## 圆润字体（待用户提供字体文件）

- [x] 代码侧：`_discover_bundled_fonts()` 自动发现 `resources/fonts/` 下的
      Regular/Bold 字体对（字体无关，按文件名关键字匹配，缺失则回退 Noto）
- [x] Dockerfile：把 `resources/fonts/` 装进系统字体目录并刷新缓存（空目录静默跳过）
- [ ] 放入**资源圆体 Resource Han Rounded**（Regular + Bold）到 `resources/fonts/`
      —— 基于中国版思源黑体的圆角化改造，7 字重，简体字形正确
      备选：寒蝉圆黑体 / 仓耳舒圆体
      注意：思源柔黑（GenJyuuGothic）源自**日文版**，简体会缺字并出日文字形，不用
- [ ] 字体到位后重渲染三图确认圆润效果
