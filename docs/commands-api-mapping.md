# 项目命令、API 调用与成本路由

更新日期：2026-07-25

## 1. 当前统一调用链

所有需要天气数据的入口调用：

```text
WeatherFusionService.get_fused_weather(location, profile=..., refresh_qweather=...)
```

执行顺序：

1. 和风 GeoAPI 解析城市；成功结果永久缓存，并对首次并发查询去重。
2. 坐标确定后，和风 profile 请求和彩云综合缓存查询并行执行。
3. 和风作为主源；彩云只填充和风返回值为 `None` 的原生字段。
4. 和风分钟降水永不被彩云覆盖；当前彩云套餐没有分钟权限。
5. 彩云失败时返回和风；和风天气失败但 Geo 成功时允许彩云兜底。

## 2. 和风 profile

所有 endpoint 独立缓存并进行 single-flight；不再缓存 10 分钟的组合 `WeatherData`，避免遮住分钟接口的 5 分钟更新周期。

| Profile | 使用场景 | 和风 API |
| --- | --- | --- |
| `full` | 默认天气、Inline、AI 日报、早安简报 | now、minutely、current/hourly/daily air、warning、hourly、daily、indices |
| `hourly` | 小时查询、温度图 | now、current/hourly air、warning、hourly |
| `daily` | 日预报、日温度图 | now、daily air、warning、daily |
| `rain` | 降水查询、降水图、降雨任务 | now、minutely、current air、warning、hourly fallback |
| `indices` | 生活指数 | now、current air、warning、indices |

和风现有缓存周期：Geo 长期、分钟 5 分钟、实况 10 分钟、预警 30 分钟、当前/小时空气质量 1 小时、小时天气 6 小时、逐日/日空气质量 12 小时、指数到当天结束。

## 3. 彩云全局融合

启用 `ENABLE_CAIYUN_API=true` 且配置 Token 后，每个天气 profile 都查询同一个缓存 key。缓存未命中时只调用一次：

```text
GET /v2.6/{token}/{lon},{lat}/weather
    ?alert=true
    &hourlysteps=72
    &dailysteps=15
    &unit=metric:v2
```

- 成功原始 JSON 按四位小数经纬度缓存 3600 秒，固定 TTL、不滑动续期。
- 同一进程同一 key 的并发未命中共享一个 Task。
- 临时失败冷却 60 秒；鉴权和额度错误冷却 1 小时。
- 单次用户请求不自动重试彩云。
- 缓存保存完整原始响应，后续视图可复用未立即展示的字段。
- `dailysteps=15` 即使套餐只返回 7 日也不会补发请求；日志记录实际小时/日数组长度。

### 费用上限

持续有访问或降雨订阅时，每个唯一坐标最多产生：

```text
1 次/小时 × 24 小时 = 24 次/天
10000 ÷ 24 ≈ 417 个地点·天
```

10 个持续活跃地点约可使用 42 天。没有查询的地点不会产生调用。

## 4. 命令与 API 对照

| 命令/入口 | Profile | 彩云行为 | 其他调用 |
| --- | --- | --- | --- |
| `/start` | 无 | 不调用 | Telegram API |
| `/tq 城市`、发送定位 | `full` | 读取/建立 1 小时综合缓存 | 无 |
| `/tq 城市 hourly N`、`Nh` | `hourly` | 同一综合缓存 | 无 |
| `/tq 城市 daily N`、日期、范围 | `daily` | 同一综合缓存 | 无 |
| `/tq 城市 rain` | `rain` | 同一综合缓存；不使用彩云分钟块 | 无 |
| `/tq 城市 indices` | `indices` | 同一综合缓存 | 无 |
| `/chart 城市`、`hourly` | `hourly` | 同一综合缓存 | 本地 Matplotlib、Telegram file_id 缓存 |
| `/chart 城市 daily` | `daily` | 同一综合缓存 | 本地 Matplotlib |
| `/chart 城市 rain` | `rain` | 同一综合缓存 | 本地 Matplotlib |
| `/report 城市` | `full` | 同一综合缓存 | OpenAI 或 Gemini |
| Inline 查询 | `full` | 同一综合缓存 | Telegram Inline API |
| Inline AI 日报选择 | `full` | 复用同一坐标缓存 | OpenAI 或 Gemini |
| 刷新按钮 | `full` | 继续使用 1 小时缓存 | 强制刷新和风天气 endpoint，Geo 不刷新 |
| 5 分钟降雨任务 | `rain` | 每唯一地点每小时最多一次 | 同地点订阅者共享结果 |
| 每日早安简报 | `full` | 每唯一地点共享结果 | 同地点只生成一次 LLM 日报 |

订阅管理命令 `/daily_sub`、`/daily_unsub`、`/daily_my`、`/rain_sub`、`/rain_unsub`、`/rain_my` 只修改 Telegram 持久化状态，不立即调用天气 API。

## 5. 彩云实际补充字段

| 数据 | 优先级与行为 |
| --- | --- |
| 未来小时体感 | 和风城市小时接口没有该字段，按时刻使用彩云原生 `apparent_temperature` |
| 小时降水 | 和风累计量 `mm` 优先；仅和风缺失时使用彩云强度 `mm/h`，保留单位类型 |
| 分钟降水 | 只用和风未来 2 小时、每 5 分钟累计量；不读取彩云分钟块 |
| 实况基础字段 | 和风优先；彩云只补缺失的体感、风速、云量、能见度、气压、辐射等 |
| 小时空气质量 | 和风未来 24 小时优先；后续缺失时彩云补 AQI/PM2.5 |
| 逐日补充 | 和风日预报优先；彩云补昼夜降水概率、辐射、AQI/PM2.5 等缺失字段，并可延长实际返回范围 |
| 生活指数 | 和风已有类型优先；彩云日级穿衣、洗车、感冒、紫外线只补缺失类型 |
| 预警 | 和风预警列表优先；和风为空时使用彩云，并由 code 确定性解析等级 |

缺失字段保持 `None` 并省略展示；不复制温度作为体感、不默认晴、不推断首要污染物，也不把缺失降水概率当作 0%。
