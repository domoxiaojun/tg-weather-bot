# 适配已上传的和风 custom emoji

## 目标

让运行时稳定加载 `data/weather_custom_emoji.json`，Docker/VPS 不丢映射，启动可观测。

## 任务

- [x] 映射路径相对项目根解析（不依赖 CWD）
- [x] 启动时 log 已加载图标数 / 开关状态
- [x] `.dockerignore`：排除 `data/*` 但保留 `weather_custom_emoji.json`
- [x] 打包 `resources/weather_custom_emoji.json` 作镜像兜底（不被 `./data` 卷盖住）
- [x] 实时卡片 hero 也显示 custom emoji
- [x] 文档 / .env.example 说明查找顺序
- [x] 单测 + 提交
