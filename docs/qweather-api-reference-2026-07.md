# 和风天气 API 完整整理（2026-07）

整理日期：2026-07-25

本文以和风天气当前官方英文开发文档为准，覆盖官方 API 总目录、核心字段和本项目接入状态。2026-04 的旧迁移记录见 `qweather-api-analysis.md`，不再作为当前状态依据。

## 1. 总体能力

和风天气当前官方 API 分类包括：

- GeoAPI：全球城市、热门城市和 POI。
- 城市天气与全球格点天气。
- 中国未来两小时分钟降水。
- 全球官方天气预警。
- 中国 16 类、海外 4 类天气生活指数。
- 全球实时、小时、逐日和监测站空气质量。
- 最近 10 天历史天气；2000 年至今重分析数据需联系商务。
- 热带气旋、潮汐、太阳辐射和天文数据。
- 控制台财务与流量统计 API。

## 2. API Host 与认证

### 2.1 API Host

生产环境应使用和风控制台分配的专属 API Host：

```text
https://{your-api-host}
```

项目通过 `QWEATHER_API_HOST` 配置根 Host，endpoint 代码内使用完整路径。当前默认值仍是 `https://api.qweather.com`，部署时应覆盖成专属 Host。

### 2.2 JWT

官方推荐 JWT：

- Ed25519 私钥签名。
- JWT Header：`alg=EdDSA`、`kid={credential-id}`。
- Payload：`sub={project-id}`、`iat`、`exp`。
- 请求头：`Authorization: Bearer {jwt}`。
- Token 最长有效期 24 小时，应本地缓存并在到期前刷新。

### 2.3 API Key

当前项目使用：

```text
X-QW-Api-Key: {api-key}
```

API Key 目前仍兼容现有接口，但官方说明从 2027-01-01 起会限制 API Key 认证的每日请求量。项目应在此前增加 JWT，并保留 API Key 兼容模式。

### 2.4 响应与错误

| API 家族 | 成功判定 | 错误形式 |
| --- | --- | --- |
| v7 天气、Geo v2 | HTTP 成功且 body `code="200"` | body 状态码 |
| Air Quality v1、Weather Alert v1、Solar v1、Console v1 | HTTP 2xx | HTTP 状态码与 problem JSON |

新 v1 数据常含 `metadata.attributions`。项目已把 attribution 保存到统一领域模型，后续展示必须继续遵守官方来源声明要求。

## 3. 官方完整 API 目录

### 3.1 GeoAPI

| 能力 | Endpoint | 说明 |
| --- | --- | --- |
| 城市搜索 | `GET /geo/v2/city/lookup` | 城市名、Location ID、经纬度、Adcode；支持模糊和重名城市 |
| 热门城市 | `GET /geo/v2/city/top` | 按国家/地区返回热门城市 |
| POI 搜索 | `GET /geo/v2/poi/lookup` | 景点等 POI 搜索 |
| POI 范围 | `GET /geo/v2/poi/range` | 查询坐标范围内 POI |

GeoAPI 可返回名称、Location ID、经纬度、时区、UTC 偏移、海拔、行政区、国家、类型与排序值。本项目只使用城市搜索。

### 3.2 城市天气与格点天气

| 能力 | Endpoint | 范围 |
| --- | --- | --- |
| 城市实时 | `GET /v7/weather/now` | 当前实况 |
| 城市逐日 | `GET /v7/weather/{days}` | `3d/7d/10d/15d/30d` |
| 城市逐小时 | `GET /v7/weather/{hours}` | `24h/72h/168h` |
| 格点实时 | `GET /v7/grid-weather/now` | 全球 3~5 km 数值模式 |
| 格点逐日 | `GET /v7/grid-weather/{days}` | 经纬度格点预报 |
| 格点逐小时 | `GET /v7/grid-weather/{hours}` | 经纬度格点预报 |

城市天气适合普通地点查询；格点天气适合没有城市 Location ID 的全球坐标和模式数据场景。本项目使用城市天气。

### 3.3 分钟降水

| 能力 | Endpoint | 范围 |
| --- | --- | --- |
| 分钟降水 | `GET /v7/minutely/5m` | 中国、未来 2 小时、每 5 分钟、约 1 km |

### 3.4 预警与生活指数

| 能力 | Endpoint | 范围 |
| --- | --- | --- |
| 当前天气预警 | `GET /weatheralert/v1/current/{latitude}/{longitude}` | 全球官方当前生效预警 |
| 生活指数 | `GET /v7/indices/{days}` | `1d/3d`，按 `type` 选择 |

### 3.5 空气质量

| 能力 | Endpoint | 范围 |
| --- | --- | --- |
| 实时空气质量 | `GET /airquality/v1/current/{latitude}/{longitude}` | 全球，1×1 km |
| 小时空气质量 | `GET /airquality/v1/hourly/{latitude}/{longitude}` | 未来 24 小时 |
| 逐日空气质量 | `GET /airquality/v1/daily/{latitude}/{longitude}` | 未来 3 日 |
| 监测站数据 | `GET /airquality/v1/stations/{locationId}` | 各国/地区监测站污染物 |

### 3.6 历史天气

| 能力 | Endpoint | 范围 |
| --- | --- | --- |
| Time Machine | `GET /v7/historical/weather` | 最近 10 天，不含今天 |

请求使用 Location ID 和 `yyyyMMdd` 日期。2000 年至今的历史重分析数据需要向和风商务申请。

### 3.7 热带气旋

| 能力 | Endpoint | 说明 |
| --- | --- | --- |
| 气旋预报 | `GET /v7/tropical/storm-forecast` | 位置、等级、气压、风和预测路径 |
| 气旋路径 | `GET /v7/tropical/storm-track` | 指定气旋历史路径 |
| 气旋列表 | `GET /v7/tropical/storm-list` | 主要洋盆最近 2 年列表 |

### 3.8 海洋、太阳辐射与天文

| 能力 | Endpoint | 范围 |
| --- | --- | --- |
| 潮汐 | `GET /v7/ocean/tide` | 全球主要港口/城市 |
| 太阳辐射 | `GET /solarradiation/v1/forecast/{latitude}/{longitude}` | 全球 1 km，最长 60 小时，15/30/60 分钟间隔 |
| 日出日落 | `GET /v7/astronomy/sun` | 未来 60 天 |
| 月升月落与月相 | `GET /v7/astronomy/moon` | 未来 60 天 |
| 太阳高度角 | `GET /v7/astronomy/solar-elevation-angle` | 任意时间地点 |

太阳辐射接口可返回 DNI、DHI、GHI，并可通过 `extra=weather/poa` 请求基础天气或光伏阵列平面辐照度。

### 3.9 控制台 API

| 能力 | Endpoint | 说明 |
| --- | --- | --- |
| 财务摘要 | `GET /finance/v1/summary` | 余额、账单等摘要 |
| 流量统计 | `GET /metrics/v1/stats` | 最近 24 小时 API 流量 |

控制台 API 默认关闭，需要在指定凭证上单独授权。返回数据通常至少延迟 1 小时，以 `asOf` 为准。

## 4. 核心天气字段

### 4.1 实时天气 `/v7/weather/now`

| 字段 | 含义 | 当前项目 |
| --- | --- | --- |
| `obsTime` | 观测时间 | 用作更新时间候选 |
| `temp` | 温度，°C | 已映射 |
| `feelsLike` | 原生实时体感，°C | 已映射 |
| `icon/text` | 天气图标与现象 | 已映射 |
| `wind360/windDir` | 风向角与文字 | 文字已映射 |
| `windScale/windSpeed` | 风力等级、km/h | 风力已映射；当前小时会映射风速 |
| `humidity` | 相对湿度 % | 已映射 |
| `precip` | 过去 1 小时降水量，mm | 已映射 |
| `pressure` | 气压，hPa | 已映射 |
| `vis` | 能见度，km | 已映射 |
| `cloud` | 云量 %，可能为空 | 实时模型未保存 |
| `dew` | 露点，°C，可能为空 | 实时模型未保存 |

当前 `WeatherData.now_feels_like` 仍是必填浮点；若和风意外缺失 `feelsLike`，适配器会得到 0。为了遵守“不估算”，该字段应改为可选并在缺失时省略。

### 4.2 城市逐小时 `/v7/weather/{hours}`

主要字段：

- `fxTime`
- `temp`
- `icon/text`
- `wind360/windDir/windScale/windSpeed`
- `humidity`
- `pop`：降水概率 %，可能为空。
- `precip`：该小时累计降水量 mm。
- `pressure`
- `cloud`
- `dew`
- 当前响应样例中还出现 `uvIndex`，字段说明页未稳定列出，项目按可选值处理。

重要限制：城市逐小时接口没有未来小时 `feelsLike`。项目不能本地估算；启用彩云后，只用彩云原生 `hourly.apparent_temperature` 按小时补全。

项目会在逐小时数组未包含当前整点时，用和风实时观测插入当前小时。这些值仍来自 API 实况，不属于气象估算，但必须明确是“当前实况”而不是未来预报。

### 4.3 城市逐日 `/v7/weather/{days}`

主要字段：

- 日期、日出、日落。
- 月升、月落、月相和月相图标。
- 最高/最低温度。
- 昼夜天气文字与图标。
- 昼夜风向、风力、风速。
- 湿度、逐日降水量、气压、能见度、云量、UV。

当前项目已经映射最高/最低温、昼夜天气、降水、日月基础字段、湿度、能见度、UV、逐日气压/云量及昼夜风向、风力和风速；月相图标仍未保存。

### 4.4 格点天气

格点接口以经纬度请求全球 3~5 km 数值模式。字段与城市天气相似，但能力并不完全相同：格点逐小时不提供城市逐小时的 `pop`，也没有 `feelsLike`；不适合直接替换当前城市预报链路。

## 5. 分钟降水字段

请求：

```text
GET /v7/minutely/5m?location={lon},{lat}
```

关键字段：

| 字段 | 含义 |
| --- | --- |
| `updateTime` | 数据更新时间 |
| `summary` | 未来两小时降水摘要 |
| `minutely[].fxTime` | 预报时刻 |
| `minutely[].precip` | 该 5 分钟累计降水量，mm |
| `minutely[].type` | `rain` 或 `snow` |

和风没有分钟概率字段，项目必须保持 `probability=None`，不能把“有降水”转换成 100% 概率。当前彩云套餐没有分钟权限，因此和风是本项目唯一可用的分钟源。

## 6. 天气预警字段

`/weatheralert/v1/current/{lat}/{lon}` 主要返回：

- `metadata.zeroResult`
- `metadata.attributions`
- `alerts[].id/senderName`
- `issuedTime/effectiveTime/onsetTime/expireTime`
- `messageType/eventType/severity/color`
- `headline/description/criteria/instruction`

当前项目已使用 v1 新接口，并映射标题、类型、颜色/严重度、正文、发布时间、预警 ID 和到期时间；标准与图标仍未保存，attribution 进入统一模型。

## 7. 天气生活指数

中国支持 16 类：舒适度、洗车、穿衣、感冒、运动、旅游、紫外线、空气污染扩散、空调、过敏、太阳镜、化妆、晾晒、交通、钓鱼、防晒。

海外支持 4 类：运动、洗车、紫外线、钓鱼。

响应字段：`date/type/name/level/category/text`。当前项目默认请求 `1,2,3,5,9`，不是全部 16 类。

## 8. 空气质量字段

v1 空气质量统一结构：

| 字段组 | 内容 |
| --- | --- |
| `metadata` | 唯一 tag 和必须展示的 attribution |
| `indexes` | QAQI、当地标准 AQI、等级、类别、颜色、首要污染物 |
| `health` | 健康影响、普通人群和敏感人群建议 |
| `pollutants` | PM2.5、PM10、NO₂、O₃、SO₂、CO 等浓度和分指数 |
| `stations` | 相关监测站，实时接口条件出现 |

当前项目已调用实时、未来 24 小时和未来 3 日接口；实时保存当地标准 AQI、类别、首要污染物、完整污染物和建议，未来值按时刻/日期合并到小时及日预报。

## 9. 当前项目接口状态

| 能力 | 当前 endpoint | 状态 |
| --- | --- | --- |
| 城市解析 | `/geo/v2/city/lookup` | 已接入 |
| 实时天气 | `/v7/weather/now` | 已接入 |
| 城市逐小时 | `/v7/weather/{hours}` | 已接入，可配置 24/72/168 小时 |
| 城市逐日 | `/v7/weather/{days}` | 已接入，可配置 3/7/10/15/30 日 |
| 分钟降水 | `/v7/minutely/5m` | 已接入，默认启用 |
| 当前天气预警 | `/weatheralert/v1/current/{lat}/{lon}` | 已接入 |
| 实时空气质量 | `/airquality/v1/current/{lat}/{lon}` | 已接入 |
| 小时/逐日空气质量 | v1 hourly/daily | 已接入；无数据时负缓存并由彩云补缺 |
| 生活指数 | `/v7/indices/1d` | 已接入，默认只请求 5 类 |
| 历史天气 | `/v7/historical/weather` | 已接入（仅取昨日 weatherDaily，喂 AI 日报与"比昨天"对比；LocationID only，不含今天） |
| 格点天气 | `/v7/grid-weather/now|{hours}|{days}` | 已接入（坐标与最近城市相距超 GRID_WEATHER_DISTANCE_KM 时启用；无 vis/feelsLike/pop） |
| 监测站 | 复用实时空气质量响应的 `stations[]` 名称 | 已接入（未单独调 `/airquality/v1/stations/`） |
| 气旋、海洋、辐射、天文、控制台 | 对应官方 endpoint | 未接入（天文与逐日预报重复，无需接入） |

### 格点天气字段差异（对照官方文档核实）

`/v7/grid-weather/*` 与城市天气**字段名一致**（temp/icon/text/windDir/windScale/windSpeed/humidity/precip/pressure/cloud/dew），并额外提供 `wind360`；但**缺少 `vis`、`feelsLike` 和 `pop`**。
因此现有 `_map_hourly` / `_map_daily` 可直接复用，缺失字段按项目规则保持为空——尤其 `pop` 缺失会让 `will_rain_soon()` 退化到只看 `precip`，这是格点作为"坐标兜底"而非默认源的原因。
坐标精度要求 2 位小数，与既有 `_coord_location()` 一致。

默认配置：

```text
QWEATHER_DAILY_DAYS=15d
QWEATHER_HOURLY_HOURS=72h
QWEATHER_INDICES_TYPES=1,2,3,5,9
QWEATHER_ENABLE_MINUTELY=true
```

## 10. 和风侧禁止估算规则

- 未来小时没有 `feelsLike` 时保持为空。
- `pop` 缺失不能当成 0%。
- `cloud/dew/uvIndex` 缺失不能当成 0。
- 分钟接口没有概率，不能由降水量反推概率。
- 空气质量应优先使用 API 返回的当地标准和首要污染物，不能只按 PM2.5 猜测。
- `metadata.zeroResult=true` 表示当前无预警；请求失败不等于无预警。
- 对 optional endpoint 返回的 `data-not-available` 应显式降级，不应伪造数据。

## 11. 弃用与迁移提醒

- WebAPI v7 旧空气质量、旧天气预警和旧太阳辐射已经进入弃用区，官方标记自 2026-06-01 起 EOL。
- 本项目空气质量和预警已迁移到 v1 新接口。
- 当前仍使用 API Key，应在 2027-01-01 前实现 Ed25519 JWT。
- attribution 是许可要求，不是可选展示字段，应进入后续模型改造。

## 12. 官方资料

- [API 总览](https://dev.qweather.com/en/docs/api/)
- [GeoAPI](https://dev.qweather.com/en/docs/api/geoapi/)
- [天气 API](https://dev.qweather.com/en/docs/api/weather/)
- [分钟降水](https://dev.qweather.com/en/docs/api/minutely/minutely-precipitation/)
- [天气预警](https://dev.qweather.com/en/docs/api/warning/weather-alert/)
- [生活指数](https://dev.qweather.com/en/docs/api/indices/indices-forecast/)
- [空气质量](https://dev.qweather.com/en/docs/api/air-quality/)
- [Time Machine](https://dev.qweather.com/en/docs/api/time-machine/)
- [热带气旋](https://dev.qweather.com/en/docs/api/tropical-cyclone/)
- [海洋](https://dev.qweather.com/en/docs/api/ocean/)
- [太阳辐射](https://dev.qweather.com/en/docs/api/solar-radiation/)
- [天文](https://dev.qweather.com/en/docs/api/astronomy/)
- [控制台 API](https://dev.qweather.com/en/docs/api/console/)
- [弃用产品](https://dev.qweather.com/en/docs/deprecated/)
- [身份认证](https://dev.qweather.com/en/docs/configuration/authentication/)
