# 和风天气 API 文档整理与项目适配分析

整理日期：2026-04-25

资料来源只采用和风天气官方开发文档。本文面向当前 Telegram 天气机器人项目，重点覆盖已经使用或足以替代彩云天气能力的接口。

## 1. 总体结论

和风天气基本够用，可以覆盖本项目的主体能力：

- 地理位置解析：城市名、经纬度、Location ID。
- 实时天气：温度、体感、天气现象、风、湿度、气压、能见度、过去 1 小时降水量。
- 逐小时预报：24/72/168 小时，含降水概率和逐小时降水量。
- 多日预报：3/7/10/15/30 天。
- 分钟级降水：未来 2 小时，每 5 分钟一条，1 公里精度，但只支持中国。
- 天气预警：新 v1 接口支持按经纬度查询当前生效预警。
- 生活指数：中国支持 16 类，海外支持 4 类。
- 空气质量：新 v1 接口提供 1x1 公里实时空气质量、当地 AQI、QAQI、污染物浓度和健康建议。

如果彩云 API 不能用，和风天气可以作为单一数据源继续运行。但本项目当前还没有接入和风的分钟级降水接口，所以降雨提醒、`/tq city rain` 和内联雨量卡片会明显退化。要做到“去彩云也够用”，首要改造是把 `/v7/minutely/5m` 接入 `QWeatherAdapter`。

## 2. Host 与认证

### API Host

官方建议使用开发者自己的独立 API Host，例如：

```text
https://abc1234xyz.def.qweatherapi.com
```

旧公共地址包括：

```text
https://api.qweather.com
https://devapi.qweather.com
https://geoapi.qweather.com
```

官方说明公共 API 地址从 2026 年起逐步停止服务。本项目已改为“根 Host + 完整路径 endpoint”的新写法：`QWEATHER_API_HOST` 不再带 `/v7` 或 `/geo/v2`，代码里统一请求 `/geo/v2/city/lookup`、`/v7/weather/now`、`/airquality/v1/current/{lat}/{lon}` 等完整路径。

### 认证方式

和风天气支持两类认证：

- JWT：官方推荐。请求头使用 `Authorization: Bearer <token>`。JWT 使用 Ed25519 签名，Header 需要 `alg=EdDSA` 和 `kid`，Payload 需要 `sub`、`iat`、`exp`，最长有效期 24 小时。
- API KEY：可放在请求头 `X-QW-Api-Key: <key>`，也可放在 query 参数 `key=<key>`。当前项目使用 query 参数方式。

注意事项：

- 不要同时使用 JWT 和 API KEY。
- API KEY 仍兼容 API v7、GeoAPI v2/v3、Air Quality API v1，但官方提示 SDK 5+ 不再支持 API KEY。
- 从 2027-01-01 起，官方会限制 API KEY 认证的每日请求量。
- 短期可继续用 `X-QW-Api-Key` 或 `key=`，长期建议增加 JWT 模式。

## 3. 通用请求与错误处理

通用 query 参数：

- `lang`：语言。本项目当前固定 `zh`。
- `unit`：单位，天气预报接口支持 `m` 公制和 `i` 英制。
- v7 天气接口和 GeoAPI 通常返回 JSON，并在 body 内用 `code` 表示状态。
- v1 新接口可能使用 HTTP status code 和 `application/problem+json` 错误结构。

常见错误：

- `400`：参数错误、缺少参数、地点不存在、数据不可用。
- `401`：认证失败。
- `403`：余额不足、权限不足、Host 错误、安全限制。
- `404`：路径或路径参数错误。
- `429`：QPM 超限或月限额超限。
- `500`：服务端异常。

项目适配要求：

- 旧 v7 成功判断：`data.get("code") == "200"`。
- 新 v1 成功判断：HTTP 2xx 且存在业务字段，例如 `indexes`、`alerts`、`metadata`。
- 新 v1 错误不一定有 `code` 字段，当前 `_request()` 只按 `code == 200` 判断会误判失败。

## 4. API 明细

### 4.1 城市搜索 / 地理编码

官方文档：

- https://dev.qweather.com/docs/api/geoapi/city-lookup/

请求：

```http
GET /geo/v2/city/lookup
```

参数：

- `location` 必填。支持城市名、经纬度、Location ID、中国 Adcode。
- `adm` 可选。上级行政区划，用于处理重名城市。
- `range` 可选。ISO 3166 国家/地区代码，例如 `cn`。
- `number` 可选。返回数量 1-20，默认 10。
- `lang` 可选。

关键返回字段：

- `location[].name`
- `location[].id`
- `location[].lat`
- `location[].lon`
- `location[].adm1`
- `location[].adm2`
- `location[].country`
- `location[].tz`
- `location[].utcOffset`
- `location[].type`
- `location[].rank`
- `location[].fxLink`

当前项目映射：

- `id` -> 后续天气接口的 `location`。
- `lon,lat` -> `WeatherData.coords`，也用于彩云天气。
- `name, adm1` -> `WeatherData.location_name`。
- 已使用新路径 `/geo/v2/city/lookup`，不再硬编码 `geoapi.qweather.com`。

适配建议：

- 保留永久缓存。
- 增加 `adm/range/number` 配置或查询参数，以改善重名城市。
- 从硬编码 `geoapi.qweather.com` 改成统一 API Host。

### 4.2 实时天气

官方文档：

- https://dev.qweather.com/docs/api/weather/weather-now/

请求：

```http
GET /v7/weather/now
```

参数：

- `location` 必填。Location ID 或 `lon,lat`。
- `lang` 可选。
- `unit` 可选。

关键返回字段：

- `now.obsTime`：观测时间。
- `now.temp`：温度。
- `now.feelsLike`：体感温度。
- `now.icon`：天气图标代码。
- `now.text`：天气现象。
- `now.wind360`、`now.windDir`、`now.windScale`、`now.windSpeed`。
- `now.humidity`。
- `now.precip`：过去 1 小时降水量，单位毫米。
- `now.pressure`。
- `now.vis`。
- `now.cloud`：可能为空。
- `now.dew`：可能为空。

当前项目映射：

- 已接入 `/v7/weather/now`。
- 已映射温度、体感、天气文本、图标、风、湿度、降水量、气压、能见度。
- 未映射 `obsTime` 到 `WeatherData.update_time`，当前 `update_time` 是本地对象创建时间。
- 未映射 `cloud`、`dew` 到实时模型字段。

适配建议：

- 用 `now.obsTime` 或 `updateTime` 填充 `WeatherData.update_time`。
- `now.precip > 0` 时可设置 `is_raining=True`，但不能替代未来降雨提醒。

### 4.3 每日天气预报

官方文档：

- https://dev.qweather.com/docs/api/weather/weather-daily-forecast/

请求：

```http
GET /v7/weather/{days}
```

路径参数：

- `3d`
- `7d`
- `10d`
- `15d`
- `30d`

参数：

- `location` 必填。
- `lang` 可选。
- `unit` 可选。

关键返回字段：

- `daily[].fxDate`
- `daily[].sunrise`
- `daily[].sunset`
- `daily[].moonrise`
- `daily[].moonset`
- `daily[].moonPhase`
- `daily[].moonPhaseIcon`
- `daily[].tempMax`
- `daily[].tempMin`
- `daily[].iconDay`
- `daily[].textDay`
- `daily[].iconNight`
- `daily[].textNight`
- `daily[].wind360Day`
- `daily[].windDirDay`
- `daily[].windScaleDay`
- `daily[].windSpeedDay`
- `daily[].wind360Night`
- `daily[].windDirNight`
- `daily[].windScaleNight`
- `daily[].windSpeedNight`
- `daily[].humidity`
- `daily[].precip`
- `daily[].pressure`
- `daily[].vis`
- `daily[].cloud`
- `daily[].uvIndex`

当前项目映射：

- 已接入 `weather/7d`。
- 已映射最高/最低温、昼夜天气、图标、降水、日出日落、月相、湿度、能见度、紫外线、昼夜风向风力。
- 未映射 `moonrise/moonset/moonPhaseIcon/pressure/cloud/windSpeedDay/windSpeedNight`。

适配建议：

- 7 天够当前 Bot 使用。
- 如需要“未来两周/一个月”，可把 `days` 做成配置：`QWEATHER_DAILY_DAYS=7d|15d|30d`。

### 4.4 逐小时天气预报

官方文档：

- https://dev.qweather.com/docs/api/weather/weather-hourly-forecast/

请求：

```http
GET /v7/weather/{hours}
```

路径参数：

- `24h`
- `72h`
- `168h`

参数：

- `location` 必填。
- `lang` 可选。
- `unit` 可选。

关键返回字段：

- `hourly[].fxTime`
- `hourly[].temp`
- `hourly[].icon`
- `hourly[].text`
- `hourly[].wind360`
- `hourly[].windDir`
- `hourly[].windScale`
- `hourly[].windSpeed`
- `hourly[].humidity`
- `hourly[].pop`：降水概率，百分比，可能为空。
- `hourly[].precip`：当前小时累计降水量，毫米。
- `hourly[].pressure`
- `hourly[].cloud`
- `hourly[].dew`

当前项目映射：

- 已接入 `weather/24h`。
- 已映射时间、温度、文本、图标、降水概率、降水量、风向风力、湿度。
- 模型已有 `pressure/cloud/dew` 字段，但适配器未填。
- 当前代码手动把实时天气补到当前小时，这是合理的展示补偿。

适配建议：

- 若不用彩云，逐小时 `pop` 可作为分钟级数据缺失时的降雨提醒兜底。
- 可配置 `QWEATHER_HOURLY_HOURS=24h|72h|168h`。

### 4.5 分钟级降水

官方文档：

- https://dev.qweather.com/docs/api/minutely/minutely-precipitation/

请求：

```http
GET /v7/minutely/5m
```

覆盖能力：

- 中国区域。
- 1 公里精度。
- 未来 2 小时。
- 每 5 分钟一条。

参数：

- `location` 必填。必须是 `lon,lat`，最多支持小数点后两位。
- `lang` 可选。

关键返回字段：

- `summary`：分钟降水摘要。
- `minutely[].fxTime`：预报时间。
- `minutely[].precip`：5 分钟累计降水量，毫米。
- `minutely[].type`：`rain` 或 `snow`。

当前项目状态：

- 已接入 `/v7/minutely/5m`。
- `WeatherFusionService` 只在 `ENABLE_CAIYUN_API=true` 且彩云成功时用彩云分钟级降水覆盖和风数据。
- `core/scheduler.py` 的降雨提醒依赖 `weather.is_raining` 或 `weather.minutely[:30]`。
- `utils/formatter.py` 的 rain 视图依赖 `data.minutely`，并显示概率。

与彩云差异：

- 和风给的是 5 分钟降水量和降水类型。
- 当前彩云适配器给的是 `precipitation_2h` 和 `probability`。
- 和风没有逐 5 分钟概率字段，不能原样填充 `probability`。

适配建议：

- 在 `QWeatherAdapter` 增加 `minutely/5m` 调用，使用 `coords` 作为 location。
- 将每条 `precip > 0` 映射为 `probability=1.0`，否则 `0.0`，这是兼容现有模型的低成本方案。
- 更好的方案是调整格式化逻辑：如果来源是和风分钟级降水，则展示“5 分钟累计降水量”和“雨/雪类型”，不展示概率。
- `is_raining` 可以按 `now_precip > 0` 或未来 30 分钟任意 `precip > 0` 计算。
- 当接口返回数据不可用时，降级使用逐小时 `pop` 做提醒。

结论：

- 国内分钟级降雨提醒：和风够用。
- 海外分钟级降雨提醒：和风不够，应降级到逐小时预报。
- 精度侧重点不同：彩云当前模型有概率，和风有 5 分钟累计量和雨雪类型。

### 4.6 天气预警

官方新接口文档：

- https://dev.qweather.com/docs/api/warning/weather-alert/

官方旧接口文档：

- https://dev.qweather.com/docs/api/warning/webapi-v7-weather-warning/

新接口请求：

```http
GET /weatheralert/v1/current/{latitude}/{longitude}
```

参数：

- `latitude` 必填。
- `longitude` 必填。
- `localTime` 可选。`true` 返回本地时间，默认 `false` 返回 UTC。
- `lang` 可选。

关键返回字段：

- `metadata.zeroResult`：请求成功但无预警时为 `true`。
- `metadata.attributions`：需要随数据展示的来源声明。
- `alerts[].id`
- `alerts[].senderName`
- `alerts[].issuedTime`
- `alerts[].messageType.code`
- `alerts[].eventType.name`
- `alerts[].eventType.code`
- `alerts[].severity`
- `alerts[].icon`
- `alerts[].color.code`
- `alerts[].effectiveTime`
- `alerts[].onsetTime`
- `alerts[].expireTime`
- `alerts[].headline`
- `alerts[].description`
- `alerts[].criteria`
- `alerts[].instruction`

当前项目状态：

- 已迁移到新接口 `/weatheralert/v1/current/{latitude}/{longitude}`。

适配建议：

- 尽快迁移到新 v1 接口。
- 新接口需要经纬度路径参数，当前 Geo 已有 `lat/lon`，可以直接使用。
- 需要把新结构映射到 `WarningAlert`：
  - `headline` -> `title`
  - `eventType.name` -> `type`
  - `color.code` 或 `severity` -> `level`
  - `description` + `instruction` -> `text`
  - `issuedTime` -> `pub_time`
- 注意展示 `metadata.attributions`，官方要求随当前数据展示。

### 4.7 天气生活指数

官方文档：

- https://dev.qweather.com/docs/api/indices/indices-forecast/

请求：

```http
GET /v7/indices/{days}
```

路径参数：

- `1d`
- `3d`

参数：

- `location` 必填。
- `type` 必填。多个类型用英文逗号分隔，例如 `type=3,5`。
- `lang` 可选。

支持范围：

- 中国：舒适度、洗车、穿衣、感冒、运动、旅游、紫外线、空气污染扩散、空调、过敏、太阳镜、化妆、晾晒、交通、钓鱼、防晒。
- 海外：运动、洗车、紫外线、钓鱼。

关键返回字段：

- `daily[].date`
- `daily[].type`
- `daily[].name`
- `daily[].level`
- `daily[].category`
- `daily[].text`

当前项目映射：

- 已接入 `indices/1d`。
- 当前请求 `type=1,2,3,5,9`。
- 格式化器里有更多类型的展示分组，但适配器没有请求全部类型。

适配建议：

- 对国内城市可增加更多指数类型，或做成配置：`QWEATHER_INDICES_TYPES=1,2,3,5,9`。
- 对海外城市只请求海外支持的类型，避免无数据或浪费请求。

### 4.8 空气质量

官方新接口文档：

- https://dev.qweather.com/docs/api/air-quality/air-current/

官方旧接口文档：

- https://dev.qweather.com/docs/api/air-quality/webapi-v7-air-now/

新接口请求：

```http
GET /airquality/v1/current/{latitude}/{longitude}
```

参数：

- `latitude` 必填。
- `longitude` 必填。
- `lang` 可选。

新接口能力：

- 1x1 公里精度。
- 当地标准 AQI。
- 和风通用 QAQI。
- 污染物浓度和分指数。
- 健康建议。
- 关联监测站。

关键返回字段：

- `metadata.tag`
- `indexes[].code`
- `indexes[].name`
- `indexes[].aqi`
- `indexes[].aqiDisplay`
- `indexes[].level`
- `indexes[].category`
- `indexes[].primaryPollutant.code`
- `indexes[].primaryPollutant.name`
- `indexes[].health.effect`
- `indexes[].health.advice.generalPopulation`
- `indexes[].health.advice.sensitivePopulation`
- `pollutants[].code`
- `pollutants[].name`
- `pollutants[].concentration.value`
- `pollutants[].concentration.unit`
- `stations[].id`
- `stations[].name`

当前项目状态：

- 已迁移到新接口 `/airquality/v1/current/{latitude}/{longitude}`。

适配建议：

- 用新接口替代 `air/now`。
- 新接口路径使用 `{latitude}/{longitude}`，不能用 Location ID。
- 新接口响应没有旧接口的 `now.aqi/category/primary/pm2p5` 结构，需要重新映射：
  - 优先选择本地 AQI 指数。如果存在中国标准指数则使用中国标准；否则选择第一个非 `qaqi` 的本地指数；再不行使用 `qaqi`。
  - `AirQuality.aqi` 当前是 `int`，但 `qaqi` 可能是小数。建议保留本地 AQI 为主，或把模型改为 `float`。
  - `pm2p5` 从 `pollutants` 中查 `code == "pm2p5"`。
  - `description` 可填健康建议。

## 5. 当前项目接口对照

当前 `adapters/qweather.py` 已使用：

| 能力 | 当前 endpoint | 官方状态 | 备注 |
| --- | --- | --- | --- |
| 城市搜索 | `/geo/v2/city/lookup` | 可用 | 统一根 Host |
| 实时天气 | `/v7/weather/now` | 可用 | 主数据 |
| 空气质量 | `/airquality/v1/current/{lat}/{lon}` | 可用 | 新 v1 接口 |
| 天气预警 | `/weatheralert/v1/current/{lat}/{lon}` | 可用 | 新 v1 接口 |
| 每日预报 | `/v7/weather/{days}` | 可用 | `QWEATHER_DAILY_DAYS` 可配置 |
| 小时预报 | `/v7/weather/{hours}` | 可用 | `QWEATHER_HOURLY_HOURS` 可配置 |
| 生活指数 | `/v7/indices/1d` | 可用 | type 列表可配置 |
| 分钟降水 | `/v7/minutely/5m` | 可用 | `QWEATHER_ENABLE_MINUTELY` 控制 |

## 6. 和风替代彩云的数据缺口

### 可以直接替代

- 实时天气：和风已经是主数据源。
- 多日预报：和风覆盖更完整。
- 小时预报：和风有 `pop` 和 `precip`，够做 24 小时降雨趋势。
- 生活指数：和风更适合当前 Bot 的指数展示。
- 预警：和风 v1 足够，但需要迁移旧接口。
- 空气质量：和风 v1 足够且更细，但需要迁移旧接口。

### 需要补实现后才能替代

- 分钟级降水：和风有接口，但项目未接入。
- 降雨提醒：当前依赖 `minutely.probability`，和风没有概率字段，需要改判定逻辑。
- `/tq city rain`：当前展示概率，需要对和风数据单独展示累计降水量和类型。

### 仍会缺失或退化

- 海外分钟级降水：和风分钟级降水文档只覆盖中国。海外应降级为小时级 `pop/precip`。
- 概率型分钟降水：当前彩云模型有分钟级概率；和风分钟级接口返回的是 5 分钟累计降水量，不返回概率。
- 彩云自己的自然语言摘要：和风也有 `summary`，但内容和口径不同。

## 7. 推荐改造优先级

### P0：让彩云不可用时仍能跑核心天气

1. 增加 QWeather 分钟级降水请求 `/v7/minutely/5m`。
2. 用和风分钟级数据填充 `WeatherData.minutely` 和 `is_raining`。
3. `WeatherFusionService` 改成：如果彩云成功且启用则用彩云覆盖分钟级，否则保留和风分钟级。
4. rain 格式化器支持“无概率”的分钟级数据。

### P1：避免官方停服风险

1. 空气质量从 `/v7/air/now` 迁移到 `/airquality/v1/current/{latitude}/{longitude}`。
2. 天气预警从 `/v7/warning/now` 迁移到 `/weatheralert/v1/current/{latitude}/{longitude}`。
3. `_request()` 支持 v7/geo/v1 三类响应结构。

### P2：Host 与认证配置现代化

1. `QWEATHER_API_HOST` 改为根 Host，例如 `https://abc.qweatherapi.com`，不要带 `/v7/`。
2. endpoint 使用完整路径，例如 `/v7/weather/now`、`/geo/v2/city/lookup`、`/airquality/v1/current/{lat}/{lon}`。
3. 支持 `QWEATHER_AUTH_TYPE=api_key|jwt`。
4. API KEY 优先放请求头 `X-QW-Api-Key`，减少 query 泄露。
5. JWT 模式增加 `QWEATHER_JWT_KEY_ID`、`QWEATHER_JWT_PROJECT_ID`、`QWEATHER_JWT_PRIVATE_KEY` 或私钥路径。

### P3：可配置预报范围

1. `QWEATHER_DAILY_DAYS=7d`。
2. `QWEATHER_HOURLY_HOURS=24h`。
3. `QWEATHER_INDICES_TYPES=1,2,3,5,9`。
4. `QWEATHER_ENABLE_MINUTELY=true`。

## 8. 当前代码额外发现

`adapters/qweather.py` 末尾存在一段不可达缓存代码：

```python
return WeatherData(...)

# Cache weather data for 1 hour (3600s)
cache.set(cache_key, weather_obj.dict(), ttl=3600)
return weather_obj
```

`return WeatherData(...)` 之后的代码不会执行，而且 `weather_obj` 未定义。这个问题和和风 API 本身无关，但会导致天气整体缓存没有按预期写入。后续改适配器时应顺手修复为：

```python
weather_obj = WeatherData(...)
cache.set(cache_key, weather_obj.dict(), ttl=3600)
return weather_obj
```

## 9. 官方资料链接

- API 总览：https://dev.qweather.com/docs/api/
- API Host：https://dev.qweather.com/docs/configuration/api-host/
- 身份认证：https://dev.qweather.com/docs/configuration/authentication/
- 错误码：https://dev.qweather.com/docs/resource/error-code/
- 城市搜索：https://dev.qweather.com/docs/api/geoapi/city-lookup/
- 实时天气：https://dev.qweather.com/docs/api/weather/weather-now/
- 每日天气预报：https://dev.qweather.com/docs/api/weather/weather-daily-forecast/
- 逐小时天气预报：https://dev.qweather.com/docs/api/weather/weather-hourly-forecast/
- 分钟级降水：https://dev.qweather.com/docs/api/minutely/minutely-precipitation/
- 实时天气预警 v1：https://dev.qweather.com/docs/api/warning/weather-alert/
- 天气预警 v7 弃用：https://dev.qweather.com/docs/api/warning/webapi-v7-weather-warning/
- 天气指数预报：https://dev.qweather.com/docs/api/indices/indices-forecast/
- 实时空气质量 v1：https://dev.qweather.com/docs/api/air-quality/air-current/
- 实时空气质量 v7 弃用：https://dev.qweather.com/docs/api/air-quality/webapi-v7-air-now/
