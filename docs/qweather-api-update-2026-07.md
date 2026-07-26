# 和风天气 API 更新核对（2026-07-25）

> 和风全部官方 API 分类、endpoint 与当前项目映射见
> [和风天气 API 完整整理（2026-07）](./qweather-api-reference-2026-07.md)。

本次只以和风天气当前官方开发文档为准，并与项目现有实现逐项对照。

## 结论

- 城市逐小时预报 `/v7/weather/{hours}` 仍支持 `24h`、`72h`、`168h`，原生提供温度、降水概率 `pop`、小时降水量 `precip`、湿度、风速、气压、云量和露点。
- 逐小时接口**没有** `feelsLike`；该字段只存在于实时天气 `/v7/weather/now`。因此项目不能把小时体感冒充为和风原始数据。
- 当前逐小时文档的响应示例已经出现 `uvIndex`，但同页字段说明尚未列出它。项目按“可选字段”兼容：有值就展示，无值不报错。
- 格点逐小时预报不提供 `pop`、`feelsLike` 或 `uvIndex`，不适合替换本项目的城市逐小时接口。
- 分钟降水仍是中国区域未来 2 小时、每 5 分钟一条，返回 5 分钟累计毫米数与雨雪类型，不返回分钟级概率。
- WebAPI v7 的空气质量、天气预警和太阳辐射已进入弃用区，官方标记自 2026-06-01 起 EOL。本项目的空气质量与预警已经使用 v1 新接口。
- 官方推荐 JWT；API KEY 认证从 2027-01-01 起会被限制每日请求量。当前项目仍使用 API KEY，这是下一项基础设施升级重点。

## 本次项目更新

### 逐小时体感温度

- 当前小时继续使用和风实时接口返回的官方 `feelsLike`。
- 和风未来小时没有 `feelsLike` 时不进行任何本地估算，界面直接省略该项。
- 启用彩云天气后，项目按预报小时对齐数据，只用彩云 v2.6 原生 `hourly.apparent_temperature` 补全和风缺失的小时体感。
- 旧缓存中若存在 `feels_like_estimated=true` 的历史估算值，文本、图表和 AI 日报都会忽略它。
- 旧缓存没有这些字段时仍可正常载入。

### 逐小时字段与展示

- 新增模型字段：`feels_like`、`feels_like_source`、`wind_speed`、`uv_index`；`feels_like_estimated` 仅保留为旧缓存安全标记。
- 小时文本同时展示天气、气温/体感、降水概率、降水量、湿度、UV、风力与风速。
- 温度图增加体感虚线；降水图同时展示概率百分比与毫米数。
- AI 日报输入增加小时原生体感及来源、风速、露点和 UV，并把未来 6 小时原生体感/UV 纳入风险信号。
- 彩云小时数据会映射概率、湿度、风速、气压、云量和原生体感；除分钟降水外，其余和风已有字段仍保持和风优先。

## 后续建议

1. 在 2027-01-01 前增加 Ed25519 JWT 认证，同时保留 API KEY 兼容模式。
2. 如需查询过去日期，可接入 Time Machine；官方当前支持最近 10 天的历史天气。
3. 若增加空气质量/预警的高级展示，应保存并展示 v1 响应里的 attribution 信息。
4. 台风、海洋、太阳辐射与天文 API 适合按命令独立启用，不建议默认随每次天气查询调用，以免增加费用和延迟。

## 官方资料

- [逐小时天气预报](https://dev.qweather.com/en/docs/api/weather/weather-hourly-forecast/)
- [实时天气](https://dev.qweather.com/en/docs/api/weather/weather-now/)
- [格点逐小时天气预报](https://dev.qweather.com/en/docs/api/weather/grid-weather-hourly-forecast/)
- [分钟级降水](https://dev.qweather.com/en/docs/api/minutely/minutely-precipitation/)
- [API 总览](https://dev.qweather.com/en/docs/api/)
- [弃用产品](https://dev.qweather.com/en/docs/deprecated/)
- [身份认证](https://dev.qweather.com/en/docs/configuration/authentication/)
- [Time Machine](https://dev.qweather.com/en/docs/api/time-machine/)
- [彩云天气 v2.6](https://docs.caiyunapp.com/weather-api/v2/v2.6/)
- [彩云小时级预报](https://docs.caiyunapp.com/weather-api/v2/v2.6/3-hourly.html)
