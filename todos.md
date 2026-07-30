# 圆润字体 + inline 图表往返（2026-07-30）

- [x] 1. 下载资源圆体 Resource Han Rounded CN v0.990（SIL OFL），取 Regular +
      Bold 两个字重放进 `resources/fonts/`，共 27MB；`_discover_bundled_fonts()`
      自动识别为 Resource Han Rounded CN，字重探测 400/700
- [x] 2. 三图用圆体重渲，肉眼确认笔画末端圆润
- [x] 3. 27MB 字体 → GB2312 子集 5.6MB，已提交（fonttools 本就是 matplotlib 依赖）
- [x] 4. 修 inline bug：点降水图后「📝 文字天气」切不回去
      根因：inline 走 `edit_message_media` 把消息变成了 media 消息，而 Bot API
      不允许 media→text 编辑，所以 `_handle_weather_choice` 原本直接弹 alert 死路。
      改为 inline 下把图表内嵌进 rich 消息（file_id 走 photo block，不需上传），
      来回都是 rich→rich 编辑；「文字天气」则就地编辑回不带图的视图
- [x] 5. 两条回归测试：inline 图表不得退化成 media、文字天气必须就地编回
- [x] 6. 逐小时体感曲线 —— **不是代码问题**。实测潮安：彩云返回 72h 数据，
      fusion 给 71 小时补上了 apparent_temperature，曲线正常。
      根因是本地 `.env` 只有 `CAIYUN_API_TOKEN`、缺 `ENABLE_CAIYUN_API=true`，
      而 `fusion.py:298` 要求两者同时成立（enable 默认 False）。已补本地 .env

遗留可选项（未做，等你决定）：
- [ ] `core/config.py:127` 的 `enable_caiyun_api` 默认值是否改成「有 token 即启用」。
      现在配了 token 却静默不调用，确实反直觉；但改默认值会让任何「配了 token
      但故意没开」的环境在升级后开始产生彩云费用，影响面大，没擅自改

# 卡片配图策略 + 降水图雨量标注（2026-07-30）

- [x] 1. 默认卡片不再自动配图：`select_auto_chart_type` 对 default/hourly/indices
      一律返回 None，只有 daily / rain 主题视图才带图（用户已经点过按钮了）
- [x] 2. 降水图按「一场雨」标注累计雨量：`_precip_spells()` 把逐小时降水切成
      连续段，最多标 3 场，标签是深底白字的 pill 贴在该段底部中心
- [x] 3. header 指标口径统一：「最大雨量（单小时峰值）」→「预计雨量（全时段累计）」，
      否则图内 12.8mm 和标题 3.4mm 会让人以为其中一个错了
- [x] 4. 测试改写：自动选图规则、每场雨标累计、最多 3 场、概率峰仍只标一次
- [x] 5. 验证 + 提交

待定（需要你决定）：
- [ ] 逐小时体感曲线：和风逐小时不返回 feelsLike，只有实况 now 有；彩云的
      apparent_temperature 可以补（fusion 已实现），但需要 CAIYUN_API_KEY +
      ENABLE_CAIYUN_API=true。项目明令禁止本地估算体感（模型层有 validator
      强制丢弃），所以只能走彩云
- [ ] 圆润字体：`resources/fonts/` 仍是空目录，代码侧（自动发现 + Dockerfile
      安装）早已就绪，缺的是字体文件本身

---

# 三图审查整改（2026-07-29）

审查依据 dataviz 技能。配色本身跑 validator 全过（含 CVD），问题在编码诚实性
与信息重复。用户选定：逐日图改渐变区间条、温度图删降雨概率柱。

- [x] 1. 逐日图：`(low+high)/2` 假分界 → 蓝→橙连续渐变区间条（LineCollection
      分段 + 两端圆头），图例改「低温 / 高温」端点语义
- [x] 2. 逐日图：7 根柱只有 4 个日期标签（tick_step=2）→ ≤8 天时全标
- [x] 3. 逐日图：y padding 0.22 两侧对称 → 收紧，顶部只留标注空间
- [x] 4. 逐日图：「有雨」蓝点与低温端点同色 → 换形状（倒三角）区分
- [x] 5. 逐小时温度图：删掉降雨概率柱（无刻度的隐藏第二 y 轴，压到 12% 高度后
      80% 与 40% 几乎无差别），图例减到 2 项
- [x] 6. 逐小时降水图：单系列不需要图例框 → 删图例
- [x] 7. 逐小时降水图：图内「雨量最大 3.4mm」与 header「最大雨量」重复 →
      仅在雨峰与概率峰不同柱时标注，且不重复数值
- [x] 8. 缓存 namespace v14 → v15，旧图失效
- [x] 9. 渲染三图肉眼复核 + 全量验证 + 提交

不做：柱顶 4px 圆角。matplotlib 位图下 4px 在实际显示尺寸几乎不可见，
做成半圆头则柱顶会超出真实值约 3%，得不偿失。

---

# 系统 emoji 全面统一为和风 custom emoji

用户要求：有合适语义的一律换成已上传的 QWeather 图标包（120 个 code），
没有对应语义的保留系统 emoji。

- [x] 1. `UI_ICON_CODES` 新增 wind=1006 / typhoon=1001 / rain_alert=1003 /
      heat=1009 / chill=1034 / pollution=1029 / tide=1045 / detail=102
- [x] 2. 风况、昼夜风：dust(503 扬沙) → wind(1006 大风预警)，语义更准
- [x] 3. 预警新增 `alert_icon_code(alert)`：卡片折叠 + 推送标题按预警类型选
      1001-1045（高温预警→🥵 而非固定 ⛈️）
- [x] 4. 潮汐 🌊→1045、降雨提醒 🚨→1003、台风 🌀→1001、中心风速 💨→1006、
      中心气压 📉→104、逐小时/逐日折叠 🔬🔎→102
- [x] 5. scheduler 派生事件改为 (key, icon_key, title, detail) 四元组，
      rich 与 HTML fallback 两条路径都用 custom emoji
- [x] 6. `build_report_blocks` 标题改用 `_strip_leading_section_emoji`，
      修早安简报 ☀️ 与天气图标并排显示的既有 bug
- [x] 7. inline 帮助卡 🌤️→101、inline AI 日报卡 🤖→now_icon
- [x] 8. 新增 `RichSystemEmojiTests`：扫描 5 个视图 + 3 类推送的全部块，
      白名单外出现系统 emoji 即失败
- [x] 9. 验证 + 提交

保留系统 emoji（图标包无对应语义）：🔺🔻 高低潮（需区分方向）、➡️ 移动、
📍 位置、🕐 时间、🧭 路径、💡 生活指数、👋 欢迎、📭 空态、⏳ 等待、
生活指数条目（🚗 洗车 / 👕 穿衣 …）。
按钮文字一律保留——Telegram InlineKeyboardButton 不支持 custom emoji。

---

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
