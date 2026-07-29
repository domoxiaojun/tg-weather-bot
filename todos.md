# 三图重设计（亮色底 + dataviz 配色）

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
